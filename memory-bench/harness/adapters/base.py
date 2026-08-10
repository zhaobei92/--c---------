# -*- coding: utf-8 -*-
"""被测框架必须实现的统一接口。

公平性是这套接口存在的唯一理由：主流程、数据表示、提问文本、top-k、判分口径
全部由 harness 固定，各框架只在 ``ingest`` / ``query`` 里做自己那套事。
适配器里**不允许**出现针对本问题集的特殊提示词或后处理——那样测出来的是
prompt 工程水平，不是框架能力。
"""

from __future__ import annotations

import abc
import datetime as dt
from typing import Iterable

from ..schema import IngestStats, QueryResult, Utterance

# 所有框架统一的检索条数上限。框架若不支持指定 top-k，在 describe() 里注明。
DEFAULT_TOP_K = 8

# 所有框架统一的回答指令。适配器如果自带 LLM 生成环节，必须原样使用这段系统提示，
# 不得追加任何与本问题集相关的额外提示。
SYSTEM_PROMPT = (
    "你是一个记忆助手。只能依据检索到的记忆内容回答问题，不得编造。"
    "如果记忆中没有相关信息，直接回答'记忆中没有相关记录'。"
    "回答要简短、直接，给出具体事实即可。"
)


class MemoryAdapter(abc.ABC):
    """一个被测记忆框架的适配器。"""

    #: 框架名，用于 results/ 下的目录命名
    name: str = "unnamed"

    @abc.abstractmethod
    def setup(self, scenario: str, config: dict | None = None) -> None:
        """为某个场景准备一套干净的记忆存储。

        必须保证场景之间互不串味——上一个场景的记忆不能残留到下一个场景。
        """

    @abc.abstractmethod
    def ingest(self, utterances: Iterable[Utterance]) -> IngestStats:
        """按时间顺序灌入全部对话。实现方需统计自身消耗的 token。"""

    @abc.abstractmethod
    def query(self, question: str, as_of: dt.datetime) -> QueryResult:
        """回答一个问题。

        ``as_of`` 是提问时点：回答只应基于该时刻之前已经发生的信息。框架若不支持
        时间点过滤，就忽略这个参数并在 ``describe()`` 的 ``supports_as_of``
        里标 False——这本身就是一项要写进报告的能力差异，不是可以偷偷绕过的细节。
        """

    def teardown(self) -> None:
        """释放资源。默认什么都不做。"""

    def describe(self) -> dict:
        """框架自述：版本、所用模型、存储后端、能力开关。写进结果供溯源。"""
        return {
            "name": self.name,
            "supports_as_of": False,
            "top_k": DEFAULT_TOP_K,
        }
