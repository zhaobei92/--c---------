# -*- coding: utf-8 -*-
"""Mem0 适配器。

> **未在本机验证过**：当前开发容器没有 GPU、没有模型权重、访问不了 huggingface。
> 本文件照着 `mem0ai/mem0` 源码写成，首次在 GPU 机器上运行可能需要小幅调整；
> 改动请记进 NOTES.md。

## 时间处理：一个需要绕行的坑

Mem0 开源版**不支持**给记忆指定原始事件时间，两个相关参数都是硬报错而非静默忽略：

* `add(..., timestamp=...)` → `mem0/memory/main.py:812` `raise ValueError`
* `search(..., reference_date=...)` → 同文件 `:1427` `raise ValueError`

按默认路径灌入我们的 30 天语料，所有记忆的 `created_at` 都会变成"灌数据那一刻"
（`:1028` 取 `datetime.now(timezone.utc)`），时间维度整个塌掉，
时间定位与回溯类题目全废。

绕行点在 `:1024` 的 `mem_metadata = deepcopy(metadata)` 加上 `:1028` 的
`if "created_at" not in mem_metadata:`——用户 metadata 里显式给了 `created_at`
就不会被覆盖。本适配器走这条路。

**这条绕行的三点局限必须写进报告，不许粉饰：**

1. 属于绕过官方"OSS 不支持"声明的非官方用法，未来版本可能失效；
2. 只是把时间**存下来**，检索排序依然不感知时间，`as_of` 只能靠元数据事后过滤；
3. 最根本的：Mem0 的 UPDATE/DELETE 是**原地覆盖**，旧事实压根不在库里，
   再怎么过滤也回溯不出来。3 道回溯题预期失败——架构决定，不是调参能救。
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path
from typing import Iterable

from mem0 import Memory

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.adapters.base import DEFAULT_TOP_K, SYSTEM_PROMPT, MemoryAdapter  # noqa: E402
from harness.metrics import count_tokens  # noqa: E402
from harness.schema import IngestStats, QueryResult, Utterance  # noqa: E402

# 与其它适配器保持一致（公平性约束，见 docs/plan.md 第一节）
LLM_MODEL = os.environ.get("BENCH_LLM_MODEL", "qwen2.5:14b-instruct")
EMBED_MODEL = os.environ.get("BENCH_EMBED_MODEL", "bge-m3")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
EMBED_DIMS = int(os.environ.get("BENCH_EMBED_DIMS", "1024"))  # bge-m3 是 1024 维

# 一次 add 送入多少条对话。逐条送会让 LLM 抽取调用次数达到 430 次/场景。
BATCH = int(os.environ.get("MEM0_BATCH", "10"))


class Adapter(MemoryAdapter):
    name = "mem0"

    def __init__(self, top_k: int = DEFAULT_TOP_K):
        self.top_k = top_k
        self._m: Memory | None = None
        self._tokens_in = 0

    # -- 生命周期 --------------------------------------------------------

    def setup(self, scenario: str, config: dict | None = None) -> None:
        self.scenario = scenario
        self.user_id = f"bench_{scenario}"
        store_path = Path(__file__).parent / ".store" / scenario
        store_path.mkdir(parents=True, exist_ok=True)

        # 全本地：Ollama 出 LLM 与 embedding，Qdrant 走本地文件模式，全程不出网
        cfg = {
            "llm": {
                "provider": "ollama",
                "config": {"model": LLM_MODEL, "temperature": 0, "ollama_base_url": OLLAMA_HOST},
            },
            "embedder": {
                "provider": "ollama",
                "config": {"model": EMBED_MODEL, "ollama_base_url": OLLAMA_HOST},
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": f"bench_{scenario}",
                    "path": str(store_path),
                    "embedding_model_dims": EMBED_DIMS,
                },
            },
        }
        self._m = Memory.from_config(cfg)
        # 场景之间必须互不串味
        try:
            self._m.delete_all(user_id=self.user_id)
        except Exception:  # noqa: BLE001 —— 首次运行时集合还不存在
            pass

    def teardown(self) -> None:
        if self._m is not None:
            try:
                self._m.close()
            except Exception:  # noqa: BLE001
                pass
        self._m = None

    # -- 灌入 ------------------------------------------------------------

    def ingest(self, utterances: Iterable[Utterance]) -> IngestStats:
        assert self._m is not None
        utts = list(utterances)
        n_calls = 0
        for i in range(0, len(utts), BATCH):
            chunk = utts[i : i + BATCH]
            messages = [{"role": "user", "content": u.as_text()} for u in chunk]
            self._tokens_in += sum(count_tokens(u.as_text()) for u in chunk)
            self._m.add(
                messages,
                user_id=self.user_id,
                # 关键绕行：把这批对话里最后一条的时间写进 created_at，
                # 否则 mem0 会填"现在"，30 天的时间结构直接丢失。
                metadata={
                    "created_at": chunk[-1].ts,
                    "scenario": self.scenario,
                    "day": chunk[-1].day,
                    "utt_ids": ",".join(u.utt_id for u in chunk),
                },
            )
            n_calls += 1
        return IngestStats(
            n_items=len(utts),
            tokens_in=self._tokens_in,
            notes=(
                f"每 {BATCH} 条对话一次 add，共 {n_calls} 次；"
                f"created_at 经 metadata 注入（OSS 的 timestamp= 参数会直接报错）；"
                f"token 未含 mem0 内部事实抽取 prompt 的开销"
            ),
        )

    # -- 查询 ------------------------------------------------------------

    def query(self, question: str, as_of: dt.datetime) -> QueryResult:
        assert self._m is not None
        # reference_date= 在 OSS 会报错，只能靠元数据过滤。
        # created_at 是同格式 ISO 字符串，字典序比较等价于时间先后。
        filters = {
            "user_id": self.user_id,
            "created_at": {"lte": as_of.isoformat()},
        }
        try:
            res = self._m.search(question, top_k=self.top_k, filters=filters)
        except Exception:  # noqa: BLE001
            # 某些向量后端不支持元数据比较运算，退化为不过滤，并在 raw 里标明。
            res = self._m.search(question, top_k=self.top_k, filters={"user_id": self.user_id})
            res.setdefault("_bench_note", "元数据时间过滤不被后端支持，已退化为不过滤")

        items = res.get("results", []) if isinstance(res, dict) else list(res)
        facts = [it.get("memory", "") for it in items]
        ids = [it.get("id", "") for it in items]
        answer = self._generate(question, facts)
        return QueryResult(
            answer=answer,
            # mem0 的记忆是 LLM 重写过的事实，不是原始对话，
            # 因此它的 id 无法与 gold_evidence 的 utt_id 对齐——
            # 这会让 gold_evidence_recall 恒为 0，报告里要注明该指标对 mem0 不适用。
            retrieved=[],
            tokens_in=count_tokens(question) + sum(count_tokens(f) for f in facts),
            tokens_out=count_tokens(answer),
            raw={"memory_ids": ids, "facts": facts, "note": res.get("_bench_note")},
        )

    def _generate(self, question: str, facts: list[str]) -> str:
        from openai import OpenAI

        client = OpenAI(api_key="ollama", base_url=f"{OLLAMA_HOST}/v1")
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
            "kind": "向量记忆层 + LLM 事实抽取",
            "memory_representation": "LLM 抽取的自然语言事实，向量化存入 Qdrant",
            "update_mechanism": "LLM 在 ADD/UPDATE/DELETE/NONE 中择一，原地覆盖",
            "supports_as_of": False,
            "as_of_mechanism": "OSS 的 reference_date 会报错；此处用 metadata.created_at 的 lte 过滤近似",
            "stale_facts_retained": False,
            "stale_facts_note": "UPDATE/DELETE 原地覆盖；旧值仅存在于 history() 流水，不可检索",
            "timestamp_injection": "经 metadata.created_at 注入，属非官方用法",
            "top_k": self.top_k,
            "llm": LLM_MODEL,
            "embedding": EMBED_MODEL,
            "backend": "Qdrant 本地文件模式",
            "gold_evidence_recall_applicable": False,
        }
