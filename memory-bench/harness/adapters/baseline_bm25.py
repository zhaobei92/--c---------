# -*- coding: utf-8 -*-
"""零依赖 BM25 检索基线。

存在的意义有三个：

1. **验证 harness 本身**。在没有网络、没有 GPU、没有任何模型权重的机器上也能把
   "灌数据 → 提问 → 判分 → 出表"整条链路跑通，证明流水线没问题。
2. **给出对比下限**。任何一个记忆框架如果打不过一个几十行的 BM25，那它那层
   "记忆"就没有创造价值——这是选型时非常实用的一把尺子。
3. **隔离变量**。它不调用任何 LLM，因此它的得分完全来自检索质量。

注意它的两个先天特点，报告里必须注明，不能拿来跟带生成能力的框架直接比总分：

* 没有生成环节，"回答"就是拼接检索到的原文。措辞类判分对它有利（原文里有关键词），
  但需要归纳、计数、比较的题它基本答不对。
* 它天然支持 as_of 过滤（直接按时间戳裁剪候选），这是很多框架没有的能力。
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections import Counter
from typing import Iterable

from ..metrics import count_tokens
from ..schema import IngestStats, QueryResult, Utterance
from .base import DEFAULT_TOP_K, MemoryAdapter

_LATIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*")
_CJK_CHAR = re.compile("[一-鿿]")

K1 = 1.5
B = 0.75
# 最高分低于这个阈值就认为"没检索到相关内容"，据此触发拒答
SCORE_FLOOR = 1.0


def tokenize(text: str) -> list[str]:
    """中文按字 + 相邻二元组切，拉丁串整体保留。

    对中文来说，只按单字切会让"电池"和"电量"这类词互相干扰，加上二元组能显著
    提升短查询的区分度，而且不需要任何分词库依赖。
    """
    toks = [m.group(0).lower() for m in _LATIN.finditer(text)]
    chars = _CJK_CHAR.findall(text)
    toks.extend(chars)
    toks.extend(a + b for a, b in zip(chars, chars[1:]))
    return toks


class Adapter(MemoryAdapter):
    name = "baseline_bm25"

    def __init__(self, top_k: int = DEFAULT_TOP_K):
        self.top_k = top_k
        self._docs: list[Utterance] = []
        self._toks: list[list[str]] = []
        self._tf: list[Counter] = []
        self._df: Counter = Counter()
        self._avg_len: float = 0.0

    # -- 生命周期 --------------------------------------------------------

    def setup(self, scenario: str, config: dict | None = None) -> None:
        self.scenario = scenario
        self._docs, self._toks, self._tf = [], [], []
        self._df = Counter()
        self._avg_len = 0.0

    def ingest(self, utterances: Iterable[Utterance]) -> IngestStats:
        n_tokens = 0
        for u in utterances:
            text = u.as_text()
            n_tokens += count_tokens(text)
            toks = tokenize(text)
            self._docs.append(u)
            self._toks.append(toks)
            tf = Counter(toks)
            self._tf.append(tf)
            self._df.update(tf.keys())
        self._avg_len = (sum(len(t) for t in self._toks) / len(self._toks)) if self._toks else 0.0
        # 纯本地索引，不经过任何模型，所以 tokens_in 记录的是"如果送进模型会是多少"，
        # 便于和其它框架横向参照；tokens_out 为 0。
        return IngestStats(
            n_items=len(self._docs),
            tokens_in=n_tokens,
            tokens_out=0,
            notes="纯本地倒排索引，未调用任何模型；tokens_in 仅为语料规模参照值",
        )

    # -- 检索 ------------------------------------------------------------

    def _score(self, query_toks: list[str], idx: int, n_docs: int) -> float:
        tf, dl = self._tf[idx], len(self._toks[idx])
        s = 0.0
        for q in set(query_toks):
            f = tf.get(q, 0)
            if not f:
                continue
            df = self._df[q]
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            s += idf * (f * (K1 + 1)) / (f + K1 * (1 - B + B * dl / max(self._avg_len, 1e-9)))
        return s

    def query(self, question: str, as_of: dt.datetime) -> QueryResult:
        q_toks = tokenize(question)
        candidates = [i for i, d in enumerate(self._docs) if d.datetime <= as_of]
        n_docs = max(len(candidates), 1)
        scored = [(self._score(q_toks, i, n_docs), i) for i in candidates]
        scored.sort(key=lambda x: (-x[0], self._docs[x[1]].ts))
        top = [(s, i) for s, i in scored[: self.top_k] if s > 0]

        if not top or top[0][0] < SCORE_FLOOR:
            return QueryResult(
                answer="记忆中没有相关记录。",
                retrieved=[],
                tokens_in=count_tokens(question),
                tokens_out=count_tokens("记忆中没有相关记录。"),
                raw={"top_score": round(top[0][0], 3) if top else 0.0, "floor": SCORE_FLOOR},
            )

        lines = [self._docs[i].as_text() for _s, i in top]
        answer = "根据记忆中的记录：\n" + "\n".join(lines)
        return QueryResult(
            answer=answer,
            retrieved=[self._docs[i].utt_id for _s, i in top],
            tokens_in=count_tokens(question),
            tokens_out=count_tokens(answer),
            raw={"scores": [round(s, 3) for s, _i in top]},
        )

    def describe(self) -> dict:
        return {
            "name": self.name,
            "kind": "检索基线（无 LLM 生成）",
            "memory_representation": "倒排索引 / BM25，记忆单元 = 单条对话原文",
            "supports_as_of": True,
            "top_k": self.top_k,
            "llm": None,
            "embedding": None,
            "bm25": {"k1": K1, "b": B, "score_floor": SCORE_FLOOR},
            "caveat": "无生成能力：需要归纳、计数、比较的题目基本答不对，不可与带 LLM 的框架比总分",
        }
