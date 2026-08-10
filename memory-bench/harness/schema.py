# -*- coding: utf-8 -*-
"""harness 的公共数据结构与语料装载。

刻意只依赖标准库：每个被测框架都装在自己的虚拟环境里，runner 必须能在任意一个
venv 的解释器下直接跑起来，不能反过来要求各 venv 安装 harness 的依赖。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
QUESTION_SET = ROOT / "question_set.json"

SCENARIOS = ["eldercare", "pet", "robot"]
NUM_DAYS = 30


@dataclass
class Utterance:
    utt_id: str
    scenario: str
    day: int
    ts: str
    speaker: str
    text: str

    @property
    def datetime(self) -> dt.datetime:
        return dt.datetime.fromisoformat(self.ts)

    def as_text(self) -> str:
        """灌入框架时使用的统一文本表示。所有框架必须使用同一种表示，否则不公平。"""
        return f"[{self.ts}] {self.speaker}：{self.text}"


@dataclass
class IngestStats:
    n_items: int = 0
    wall_seconds: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    notes: str = ""


@dataclass
class QueryResult:
    answer: str
    retrieved: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    raw: dict = field(default_factory=dict)
    # latency 由 runner 统一在外部计时，适配器不必自己填
    latency_ms: float = 0.0


def load_utterances(scenario: str) -> list[Utterance]:
    """按时间顺序返回某场景的全部对话。"""
    sdir = DATA_DIR / scenario
    if not sdir.exists():
        raise FileNotFoundError(f"{sdir} 不存在，请先运行 scripts/gen_data.py")
    out: list[Utterance] = []
    for day in range(1, NUM_DAYS + 1):
        for line in (sdir / f"day_{day:02d}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                out.append(
                    Utterance(
                        utt_id=d["utt_id"],
                        scenario=d["scenario"],
                        day=d["day"],
                        ts=d["ts"],
                        speaker=d["speaker"],
                        text=d["text"],
                    )
                )
    out.sort(key=lambda u: (u.ts, u.utt_id))
    return out


def load_facts(scenario: str) -> list[dict]:
    return json.loads((DATA_DIR / scenario / "facts_timeline.json").read_text(encoding="utf-8"))


def load_question_set() -> dict:
    if not QUESTION_SET.exists():
        raise FileNotFoundError("question_set.json 不存在，请先运行 scripts/gen_question_set.py")
    return json.loads(QUESTION_SET.read_text(encoding="utf-8"))


def questions_for(scenario: str) -> list[dict]:
    qs = load_question_set()["questions"]
    return [q for q in qs if q["scenario"] == scenario]
