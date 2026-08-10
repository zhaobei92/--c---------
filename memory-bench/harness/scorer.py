# -*- coding: utf-8 -*-
"""判分器。

自动判分只做**可机械核对**的部分，判完一律导出人工核对表；报告里的最终准确率
以人工复核后的列为准。这样既不用逐题手工跑，也不至于把自动判分的误差当成事实。

三条规则：

``keywords``
    must_include 全中、每个 any_of 组至少中一个，即判对。

``supersede``（事实变更题）
    在 keywords 基础上，额外检查旧值：旧值出现在回答里本身不算错——"之前是 A，
    现在是 B" 是完全正确的回答——但旧值附近（前后 25 字窗口内）必须有转折标记，
    否则视为把旧事实当成了现行事实，判错。

``refusal``（拒答题）
    必须出现拒答表述，且不得出现任何被禁词。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

STALE_WINDOW = 25  # 旧值前后各看多少个字符找转折标记


def normalize(text: str) -> str:
    """全角转半角、去空白、统一小写，避免因排版差异误判。"""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"\s+", "", t)
    return t.lower()


@dataclass
class Verdict:
    correct: bool
    reason: str

    def as_str(self) -> str:
        return "对" if self.correct else "错"


def _missing_keywords(ans: str, scoring: dict) -> list[str]:
    missing = [kw for kw in scoring.get("must_include", []) if normalize(kw) not in ans]
    for group in scoring.get("any_of", []):
        if not any(normalize(t) in ans for t in group):
            missing.append("(" + "|".join(group) + ")")
    return missing


def _stale_asserted(ans: str, scoring: dict) -> list[str]:
    """返回"被当成现行事实陈述"的旧值列表。"""
    markers = [normalize(m) for m in scoring.get("change_markers", [])]
    bad: list[str] = []
    for stale in scoring.get("stale_values", []):
        s = normalize(stale)
        start = 0
        while True:
            i = ans.find(s, start)
            if i < 0:
                break
            lo = max(0, i - STALE_WINDOW)
            hi = min(len(ans), i + len(s) + STALE_WINDOW)
            window = ans[lo:hi]
            if not any(m and m in window for m in markers):
                bad.append(stale)
                break
            start = i + len(s)
    return bad


def score(question: dict, answer: str) -> Verdict:
    sc = question["scoring"]
    ans = normalize(answer)
    if not ans:
        return Verdict(False, "回答为空")

    if sc["rule"] == "refusal":
        hit_marker = next((m for m in sc["refusal_markers"] if normalize(m) in ans), None)
        leaked = [f for f in sc.get("forbidden", []) if normalize(f) in ans]
        if leaked:
            return Verdict(False, f"编造了数据中不存在的内容：{'、'.join(leaked)}")
        if not hit_marker:
            return Verdict(False, "没有明确表示不知道/无相关记录")
        return Verdict(True, f"正确拒答（命中表述：{hit_marker}）")

    missing = _missing_keywords(ans, sc)
    if missing:
        return Verdict(False, f"缺少关键事实：{'、'.join(missing)}")

    if sc["rule"] == "supersede":
        bad = _stale_asserted(ans, sc)
        if bad:
            return Verdict(False, f"把已失效的旧值当成现行事实陈述：{'、'.join(bad)}")
        return Verdict(True, "命中现行事实，且未把旧值当成现行")

    return Verdict(True, "命中全部关键事实")
