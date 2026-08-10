# -*- coding: utf-8 -*-
"""Hindsight 适配器（vectorize-io/hindsight，MIT）。

> **未在本机验证过**：当前开发容器没有 GPU、访问不了模型仓库。本文件照着
> `hindsight-clients/python/hindsight_client/hindsight_client.py` 的签名写成，
> 首次在 GPU 机器上运行可能需要小幅调整；改动请记进 NOTES.md。

Hindsight 的时间能力比预想的强，两个参数都在开源客户端里：

* ``retain(..., timestamp: datetime | None)`` —— 事件时间是一等参数
* ``recall(..., query_timestamp: str | None)`` —— 提问时点是一等参数

因此本适配器如实使用它们，`supports_as_of` 报 True。这一点在阶段2 要重点验证：
参数存在不等于语义正确，得看它是"按时间过滤"还是仅仅"给 LLM 一个时间提示"。

**与其它适配器的一处不可消除的差异**：Hindsight 的 `recall` 不是 top-k 接口，
而是按 token 预算组装上下文（`max_tokens` / `budget`）。这意味着统一 top-k=8 的
公平性约束对它无法完全适用——这本身是要写进报告的能力差异，不是可以掩盖的细节。
本适配器把预算调到与其它框架 top-k=8 大致相当的量级，并在 describe() 里注明。
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path
from typing import Iterable

from hindsight_client import HindsightClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.adapters.base import DEFAULT_TOP_K, SYSTEM_PROMPT, MemoryAdapter  # noqa: E402
from harness.metrics import count_tokens  # noqa: E402
from harness.schema import IngestStats, QueryResult, Utterance  # noqa: E402

LLM_MODEL = os.environ.get("BENCH_LLM_MODEL", "qwen2.5:14b-instruct")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
# hindsight-embed 的本地守护进程默认端口
HINDSIGHT_URL = os.environ.get("HINDSIGHT_URL", "http://localhost:8888")

# 8 条记忆大致相当的 token 预算，用于向其它框架的 top_k=8 靠拢
RECALL_MAX_TOKENS = int(os.environ.get("HINDSIGHT_RECALL_TOKENS", "1024"))


class Adapter(MemoryAdapter):
    name = "hindsight"

    def __init__(self, top_k: int = DEFAULT_TOP_K):
        self.top_k = top_k
        self._c: HindsightClient | None = None
        self._tokens_in = 0

    # -- 生命周期 --------------------------------------------------------

    def setup(self, scenario: str, config: dict | None = None) -> None:
        self.scenario = scenario
        self.bank_id = f"memory_bench_{scenario}"
        self._c = HindsightClient(base_url=HINDSIGHT_URL)
        # 场景之间必须互不串味。若客户端没有删除接口，需在 setup.sh 里换用全新 bank_id。
        for meth in ("delete_bank", "clear_bank"):
            fn = getattr(self._c, meth, None)
            if fn is not None:
                try:
                    fn(self.bank_id)
                except Exception:  # noqa: BLE001 —— bank 不存在属正常
                    pass
                break

    def teardown(self) -> None:
        if self._c is not None:
            close = getattr(self._c, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
        self._c = None

    # -- 灌入 ------------------------------------------------------------

    def ingest(self, utterances: Iterable[Utterance]) -> IngestStats:
        assert self._c is not None
        utts = list(utterances)
        for u in utts:
            self._tokens_in += count_tokens(u.text)
            self._c.retain(
                bank_id=self.bank_id,
                content=u.text,
                # 事件时间是一等参数，直接给原始对话时间
                timestamp=u.datetime,
                context=f"{self.scenario} 场景，说话人：{u.speaker}",
                document_id=f"{self.scenario}-day{u.day:02d}",
                metadata={"utt_id": u.utt_id, "speaker": u.speaker, "day": str(u.day)},
            )
        return IngestStats(
            n_items=len(utts),
            tokens_in=self._tokens_in,
            notes=(
                "逐条 retain，事件时间经 timestamp 参数传入（一等支持）；"
                "token 未含 Hindsight 服务端内部处理开销"
            ),
        )

    # -- 查询 ------------------------------------------------------------

    def query(self, question: str, as_of: dt.datetime) -> QueryResult:
        assert self._c is not None
        res = self._c.recall(
            bank_id=self.bank_id,
            query=question,
            # 提问时点，同样是一等参数
            query_timestamp=as_of.isoformat(),
            max_tokens=RECALL_MAX_TOKENS,
            include_source_facts=True,
        )

        context = self._extract_context(res)
        answer = self._generate(question, context)
        return QueryResult(
            answer=answer,
            # recall 返回的是组装好的上下文而非原始 utt_id 列表，
            # 除非 include_source_facts 能给出可对齐的 id，否则证据召回率对它不适用。
            retrieved=self._extract_utt_ids(res),
            tokens_in=count_tokens(question) + count_tokens(context),
            tokens_out=count_tokens(answer),
            raw={"context_chars": len(context)},
        )

    @staticmethod
    def _extract_context(res) -> str:
        for attr in ("context", "memories", "content", "text"):
            v = getattr(res, attr, None)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, list) and v:
                return "\n".join(str(x) for x in v)
        return str(res)

    @staticmethod
    def _extract_utt_ids(res) -> list[str]:
        """尽力从返回结果里挖出 utt_id，挖不到就返回空（该指标对本框架不适用）。"""
        ids: list[str] = []
        facts = getattr(res, "source_facts", None) or []
        for f in facts:
            md = getattr(f, "metadata", None) or {}
            uid = md.get("utt_id") if isinstance(md, dict) else None
            if uid:
                ids.append(uid)
        return ids

    def _generate(self, question: str, context: str) -> str:
        from openai import OpenAI

        client = OpenAI(api_key="ollama", base_url=f"{OLLAMA_HOST}/v1")
        body = context.strip() or "（没有检索到任何记忆）"
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"检索到的记忆：\n{body}\n\n问题：{question}"},
            ],
        )
        return (resp.choices[0].message.content or "").strip()

    def describe(self) -> dict:
        return {
            "name": self.name,
            "kind": "分网络记忆（事实/经历/观察/观点）+ retain/recall/reflect",
            "memory_representation": "按认识论类型分网络，实体与关系关联，锚定时间",
            "supports_as_of": True,
            "as_of_mechanism": "recall(query_timestamp=...)，为开源客户端一等参数",
            "top_k": None,
            "top_k_note": (
                f"recall 是 token 预算接口而非 top-k 接口，此处用 max_tokens="
                f"{RECALL_MAX_TOKENS} 近似其它框架的 top_k={DEFAULT_TOP_K}；"
                f"统一 top-k 的公平性约束对本框架无法完全适用"
            ),
            "llm": LLM_MODEL,
            "embedding": "由 Hindsight 服务端自带（未与其它框架统一，需在报告注明）",
            "backend": "hindsight-embed 本地守护进程 + 内嵌 PostgreSQL(pg0)",
            "license": "MIT",
        }
