# -*- coding: utf-8 -*-
"""Graphiti 适配器。

> **未在本机验证过**：当前开发容器没有 GPU、没有模型权重、访问不了 huggingface，
> 无法真正跑起来。本文件是照着 `getzep/graphiti` 源码（commit 见 NOTES.md）写的，
> 首次在 GPU 机器上运行时可能需要小幅调整。凡是需要调整的地方，改完请把改动记进
> NOTES.md，那也是调研结论的一部分。

Graphiti 是五个框架里唯一原生支持双时间轴的：``add_episode(reference_time=...)``
把事件时间作为必填参数，``SearchFilters`` 能按 ``valid_at`` / ``invalid_at`` 过滤。
因此本适配器**如实使用**这两项能力——这正是要测的东西。

本地优先（宪法第 1 条）：默认走 Ollama 提供 LLM 与 embedding，图库用嵌入式
falkordblite，全程不出网、不需要 Docker。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
from typing import Iterable

from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType
from graphiti_core.search.search_filters import (
    ComparisonOperator,
    DateFilter,
    SearchFilters,
)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.adapters.base import DEFAULT_TOP_K, SYSTEM_PROMPT, MemoryAdapter  # noqa: E402
from harness.metrics import count_tokens  # noqa: E402
from harness.schema import IngestStats, QueryResult, Utterance  # noqa: E402

# 统一模型配置，三个框架共用同一组，改这里要同步改其它适配器（公平性约束）
LLM_MODEL = os.environ.get("BENCH_LLM_MODEL", "qwen2.5:14b-instruct")
EMBED_MODEL = os.environ.get("BENCH_EMBED_MODEL", "bge-m3")
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")

# 一次灌入多少条对话作为一个 episode。
# 逐条灌会让 LLM 抽取调用次数暴涨（430 条 × 3 场景），按天聚合更接近真实用法。
EPISODE_GRANULARITY = os.environ.get("GRAPHITI_EPISODE", "day")  # day | utterance


class Adapter(MemoryAdapter):
    name = "graphiti"

    def __init__(self, top_k: int = DEFAULT_TOP_K):
        self.top_k = top_k
        self._g: Graphiti | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tokens_in = 0
        self._tokens_out = 0

    # -- 生命周期 --------------------------------------------------------

    def setup(self, scenario: str, config: dict | None = None) -> None:
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.llm_client.openai_client import OpenAIClient

        self.scenario = scenario
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        # Ollama 暴露 OpenAI 兼容接口，所以直接复用 OpenAI client 指向本地
        llm_cfg = LLMConfig(api_key="ollama", model=LLM_MODEL, base_url=OLLAMA_BASE)
        embed_cfg = OpenAIEmbedderConfig(
            api_key="ollama", embedding_model=EMBED_MODEL, base_url=OLLAMA_BASE
        )

        driver = self._make_driver(scenario)
        self._g = Graphiti(
            llm_client=OpenAIClient(config=llm_cfg),
            embedder=OpenAIEmbedder(config=embed_cfg),
            graph_driver=driver,
        )
        self._loop.run_until_complete(self._g.build_indices_and_constraints())

    def _make_driver(self, scenario: str):
        """嵌入式图库，无需 Docker。

        注意不要用 Kuzu：graphiti 的 pyproject.toml 明确注明上游 Kuzu 已停维护、
        该 extra 将被移除。这里用 falkordblite（需 Python >= 3.12）。
        """
        from graphiti_core.driver.falkordb_driver import FalkorDriver

        # 每个场景一张独立的图，避免场景之间串味
        return FalkorDriver(database=f"memory_bench_{scenario}")

    def teardown(self) -> None:
        if self._g and self._loop:
            self._loop.run_until_complete(self._g.close())
            self._loop.close()
        self._g = None
        self._loop = None

    # -- 灌入 ------------------------------------------------------------

    def ingest(self, utterances: Iterable[Utterance]) -> IngestStats:
        assert self._g and self._loop
        utts = list(utterances)
        episodes = self._group(utts)
        for name, body, ref_time in episodes:
            self._tokens_in += count_tokens(body)
            self._loop.run_until_complete(
                self._g.add_episode(
                    name=name,
                    episode_body=body,
                    source=EpisodeType.message,
                    source_description=f"{self.scenario} 场景对话转写",
                    # 事件时间是一等公民——这正是 Graphiti 相对其它框架的核心差异
                    reference_time=ref_time,
                    group_id=self.scenario,
                )
            )
        return IngestStats(
            n_items=len(utts),
            tokens_in=self._tokens_in,
            tokens_out=self._tokens_out,
            notes=(
                f"按 {EPISODE_GRANULARITY} 聚合成 {len(episodes)} 个 episode；"
                f"token 为送入文本的估算值，不含 graphiti 内部抽取 prompt 的开销"
            ),
        )

    def _group(self, utts: list[Utterance]) -> list[tuple[str, str, dt.datetime]]:
        if EPISODE_GRANULARITY == "utterance":
            return [(u.utt_id, u.as_text(), u.datetime) for u in utts]
        by_day: dict[int, list[Utterance]] = {}
        for u in utts:
            by_day.setdefault(u.day, []).append(u)
        out = []
        for day in sorted(by_day):
            group = by_day[day]
            body = "\n".join(u.as_text() for u in group)
            # reference_time 取当天最后一条的时间，保证 episode 的事件时间不早于内容
            out.append((f"{self.scenario}-day{day:02d}", body, group[-1].datetime))
        return out

    # -- 查询 ------------------------------------------------------------

    def query(self, question: str, as_of: dt.datetime) -> QueryResult:
        assert self._g and self._loop

        # 只看在 as_of 之前已经成立、且到 as_of 时还没失效的事实。
        # invalid_at 为空（还没失效）或晚于 as_of，两者取其一。
        filters = SearchFilters(
            valid_at=[[DateFilter(date=as_of, comparison_operator=ComparisonOperator.less_than_equal)]],
            invalid_at=[
                [DateFilter(comparison_operator=ComparisonOperator.is_null)],
                [DateFilter(date=as_of, comparison_operator=ComparisonOperator.greater_than)],
            ],
        )
        edges = self._loop.run_until_complete(
            self._g.search(
                query=question,
                group_ids=[self.scenario],
                num_results=self.top_k,
                search_filter=filters,
            )
        )

        facts = [e.fact for e in edges]
        retrieved = [e.uuid for e in edges]
        answer = self._generate(question, facts)
        return QueryResult(
            answer=answer,
            retrieved=retrieved,
            tokens_in=count_tokens(question) + sum(count_tokens(f) for f in facts),
            tokens_out=count_tokens(answer),
            raw={"n_edges": len(edges), "facts": facts},
        )

    def _generate(self, question: str, facts: list[str]) -> str:
        """用统一的系统提示词把检索到的事实汇成回答。

        必须使用 harness 的 SYSTEM_PROMPT 原文，不得追加任何针对本问题集的提示——
        见 docs/plan.md 第一节。
        """
        from openai import OpenAI

        client = OpenAI(api_key="ollama", base_url=OLLAMA_BASE)
        context = "\n".join(f"- {f}" for f in facts) if facts else "（没有检索到任何记忆）"
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"检索到的记忆：\n{context}\n\n问题：{question}"},
            ],
        )
        return (resp.choices[0].message.content or "").strip()

    def describe(self) -> dict:
        return {
            "name": self.name,
            "kind": "时序知识图谱（双时间轴）",
            "memory_representation": "实体-关系图，每条边带 valid_at/invalid_at/created_at/expired_at",
            "supports_as_of": True,
            "as_of_mechanism": "SearchFilters 对 valid_at/invalid_at 做比较运算过滤",
            "stale_facts_retained": True,
            "top_k": self.top_k,
            "llm": LLM_MODEL,
            "embedding": EMBED_MODEL,
            "backend": "falkordblite（嵌入式，无需 Docker）",
            "episode_granularity": EPISODE_GRANULARITY,
        }
