#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""memory-bench 模拟数据生成器。

产出三个场景 × 各 30 天的对话转写文本，以及与之严格对应的事实时间线。

设计要点
--------
1. **时间线是唯一事实来源**。每条对话文本都由 ``facts_timeline.json`` 中的事实
   记录渲染而来（或标记为无关闲聊）。问题集的标准答案由时间线推导，而不是人工
   撰写——这样答案永远不会与数据脱节。
2. **确定性**。固定随机种子，同一份代码任意机器上重跑产出逐字节一致的数据。
3. **含随时间变化的事实**。每个场景都安排了若干"状态型"事实在中途被新版本取代
   （用药方案、狗粮、充电桩位置……），用于考察框架的新旧事实覆盖能力。
4. **闲聊噪声受控**。闲聊语料经过筛选，不会误答问题集中的"拒答类"问题，
   也不会与任何事实冲突。

用法::

    python3 scripts/gen_data.py                # 写入 data/
    python3 scripts/gen_data.py --out /tmp/d   # 写到别处
    python3 scripts/gen_data.py --check        # 只做自检，不写文件
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

# --------------------------------------------------------------------------
# 全局常量
# --------------------------------------------------------------------------

SEED = 20260310
START_DATE = dt.date(2026, 3, 2)  # 周一，便于"上周三"这类相对时间表达对齐
NUM_DAYS = 30
WEEKDAY_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def day_to_date(day: int) -> dt.date:
    """第 N 天（1-based）对应的日历日期。"""
    return START_DATE + dt.timedelta(days=day - 1)


def day_weekday(day: int) -> int:
    """第 N 天的星期索引，0=周一。"""
    return (day - 1) % 7


def ts(day: int, hour: int, minute: int) -> str:
    """构造时间戳。允许 minute 超过 59，自动进位到小时（方便写 "+10 分钟" 这类偏移）。"""
    d = day_to_date(day)
    base = dt.datetime(d.year, d.month, d.day, 0, 0)
    return (base + dt.timedelta(hours=hour, minutes=minute)).isoformat()


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass
class Utterance:
    utt_id: str
    scenario: str
    day: int
    ts: str
    speaker: str
    text: str
    fact_refs: list[str] = field(default_factory=list)


@dataclass
class Fact:
    """一条事实记录。

    kind="state" 表示持续性状态（有生效/失效时间窗，可被新版本取代）；
    kind="event" 表示一次性事件（发生在某一天）。
    """

    fact_id: str
    scenario: str
    kind: str
    subject: str
    attribute: str
    value: str
    value_keywords: list[str]
    valid_from_day: int
    valid_to_day: int | None = None  # 左闭右开；None 表示至今有效
    supersedes: str | None = None
    superseded_by: str | None = None
    evidence: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        d = asdict(self)
        d["valid_from"] = day_to_date(self.valid_from_day).isoformat()
        d["valid_to"] = (
            day_to_date(self.valid_to_day).isoformat() if self.valid_to_day else None
        )
        return d


class Builder:
    """按天累积对话与事实，负责分配稳定的 utt_id。"""

    def __init__(self, scenario: str, rng: random.Random):
        self.scenario = scenario
        self.rng = rng
        self.utterances: list[Utterance] = []
        self.facts: list[Fact] = []
        self._counter: dict[int, int] = {}

    def say(
        self,
        day: int,
        hour: int,
        minute: int,
        speaker: str,
        text: str,
        fact_refs: Iterable[str] = (),
    ) -> str:
        n = self._counter.get(day, 0) + 1
        self._counter[day] = n
        utt_id = f"{self.scenario[:3]}-d{day:02d}-{n:02d}"
        self.utterances.append(
            Utterance(
                utt_id=utt_id,
                scenario=self.scenario,
                day=day,
                ts=ts(day, hour, minute),
                speaker=speaker,
                text=text,
                fact_refs=list(fact_refs),
            )
        )
        return utt_id

    def fact(self, **kwargs) -> Fact:
        f = Fact(scenario=self.scenario, **kwargs)
        self.facts.append(f)
        return f

    def finalize(self) -> None:
        """按时间排序，并把 utterance 上的 fact_refs 回填成事实的 evidence。"""
        self.utterances.sort(key=lambda u: (u.ts, u.utt_id))
        by_id = {f.fact_id: f for f in self.facts}
        for u in self.utterances:
            for fid in u.fact_refs:
                if fid not in by_id:
                    raise KeyError(f"{u.utt_id} 引用了不存在的事实 {fid}")
                by_id[fid].evidence.append(u.utt_id)
        for f in self.facts:
            if not f.evidence:
                raise ValueError(f"事实 {f.fact_id} 没有任何对话证据支撑")


# --------------------------------------------------------------------------
# 场景一：养老院长者日常
# --------------------------------------------------------------------------

ELDER_FILLER = [
    (8, 30, "护工-李梅", "王奶奶早，今天精神看着不错，先把早饭吃了吧。"),
    (8, 45, "王秀兰", "小梅啊，今天的粥熬得挺稠，我喜欢。"),
    (10, 15, "护工-李梅", "王奶奶，扶您到院子里晒会儿太阳，风不大。"),
    (10, 40, "王秀兰", "院子里那几盆月季又开花了，比上回开得旺。"),
    (14, 20, "王秀兰", "下午我想听会儿评剧，你帮我把收音机调一下。"),
    (15, 10, "护工-李梅", "隔壁房的赵爷爷下棋又赢了，正得意呢。"),
    (16, 5, "王秀兰", "我年轻那会儿在纺织厂上班，一个班站八个钟头都不觉得累。"),
    (16, 30, "护工-李梅", "王奶奶，晚饭想吃面条还是米饭？"),
    (19, 20, "王秀兰", "电视里这个节目我看过，讲的是老北京的胡同。"),
    (19, 45, "护工-李梅", "王奶奶，睡前记得少喝点水，免得夜里起夜。"),
    (9, 30, "王秀兰", "今天天气比昨天暖和，窗户开一条缝就行。"),
    (11, 0, "护工-李梅", "王奶奶，一会儿量个血压，例行的。"),
    (13, 40, "王秀兰", "午觉睡得沉，做了个梦，梦见回老家了。"),
    (17, 15, "护工-李梅", "食堂今天有您爱吃的南瓜羹，我给您留了一份。"),
    (20, 10, "王秀兰", "我这记性不如从前了，刚说过的话转脸就忘。"),
]


def build_eldercare(rng: random.Random) -> Builder:
    b = Builder("eldercare", rng)
    ELDER, NURSE, RN, DOC = "王秀兰", "护工-李梅", "张护士", "陈医生"
    SON, GRANDDAUGHTER = "儿子-王建国", "孙女-王小雨"

    # ---- 状态事实：降压药方案（第10天更换）----
    b.fact(
        fact_id="eldercare.med.bp.v1",
        kind="state",
        subject=ELDER,
        attribute="降压药方案",
        value="氨氯地平 5mg，每日一次，早饭后服用",
        value_keywords=["氨氯地平"],
        valid_from_day=1,
        valid_to_day=10,
        superseded_by="eldercare.med.bp.v2",
    )
    b.fact(
        fact_id="eldercare.med.bp.v2",
        kind="state",
        subject=ELDER,
        attribute="降压药方案",
        value="缬沙坦 80mg，每日一次，早饭后服用",
        value_keywords=["缬沙坦"],
        valid_from_day=10,
        valid_to_day=None,
        supersedes="eldercare.med.bp.v1",
    )
    b.say(1, 8, 5, NURSE, "王奶奶，早饭后的氨氯地平吃了没有？一天就这一片，别落下。", ["eldercare.med.bp.v1"])
    b.say(1, 8, 8, ELDER, "吃了，那个白色的小片，我记着呢。", ["eldercare.med.bp.v1"])
    b.say(5, 9, 0, RN, "今天血压 152/94，还是偏高，氨氯地平吃了快一周了，效果一般。", ["eldercare.med.bp.v1"])
    b.say(9, 9, 10, RN, "连着三天高压都在 150 上下，我跟陈医生反映一下，看要不要换药。", ["eldercare.med.bp.v1"])
    b.say(
        10, 10, 30, DOC,
        "王奶奶的氨氯地平从今天停掉，换成缬沙坦 80mg，还是每天早饭后一片，两周后复查。",
        ["eldercare.med.bp.v1", "eldercare.med.bp.v2"],
    )
    b.say(10, 10, 35, NURSE, "记下了，明天开始给王奶奶发缬沙坦，原来那个氨氯地平我收走。", ["eldercare.med.bp.v2"])
    b.say(14, 8, 10, NURSE, "王奶奶，缬沙坦一片，早饭后吃。", ["eldercare.med.bp.v2"])
    b.say(24, 9, 5, RN, "换缬沙坦两周了，今天血压 132/82，降下来了，方案先不动。", ["eldercare.med.bp.v2"])
    b.say(29, 8, 15, NURSE, "王奶奶，缬沙坦记得吃，就那一片。", ["eldercare.med.bp.v2"])

    # ---- 状态事实：安眠药（第18天新增，此前没有）----
    b.fact(
        fact_id="eldercare.med.sleep.v1",
        kind="state",
        subject=ELDER,
        attribute="助眠用药",
        value="右佐匹克隆 1mg，睡前服用，仅在夜里睡不着时使用",
        value_keywords=["右佐匹克隆"],
        valid_from_day=18,
        valid_to_day=None,
    )
    b.say(17, 21, 30, ELDER, "这几天夜里翻来覆去睡不着，天快亮了才迷糊一会儿。", [])
    b.say(
        18, 10, 0, DOC,
        "针对失眠加一个右佐匹克隆 1mg，睡前吃，睡得着就别吃，别每天都用。",
        ["eldercare.med.sleep.v1"],
    )
    b.say(21, 20, 50, NURSE, "王奶奶今晚要右佐匹克隆吗？睡得着就先不吃。", ["eldercare.med.sleep.v1"])

    # ---- 状态事实：书法班时间（第18天起从周三下午改到周四下午）----
    b.fact(
        fact_id="eldercare.activity.calligraphy.v1",
        kind="state",
        subject=ELDER,
        attribute="书法班时间",
        value="每周三下午两点半，在二楼活动室",
        value_keywords=["周三"],
        valid_from_day=1,
        valid_to_day=18,
        superseded_by="eldercare.activity.calligraphy.v2",
    )
    b.fact(
        fact_id="eldercare.activity.calligraphy.v2",
        kind="state",
        subject=ELDER,
        attribute="书法班时间",
        value="每周四下午两点半，在二楼活动室",
        value_keywords=["周四"],
        valid_from_day=18,
        valid_to_day=None,
        supersedes="eldercare.activity.calligraphy.v1",
    )
    for d in (3, 10):
        b.say(d, 14, 30, NURSE, "王奶奶，周三下午的书法班开始了，我陪您上二楼活动室。", ["eldercare.activity.calligraphy.v1"])
    b.say(17, 15, 0, NURSE, "通知一下，书法班的周老师调课，从这周起改到周四下午两点半，还是二楼活动室。",
          ["eldercare.activity.calligraphy.v1", "eldercare.activity.calligraphy.v2"])
    for d in (18, 25):
        b.say(d, 14, 30, NURSE, "王奶奶，周四下午的书法班到点了，咱们上二楼。", ["eldercare.activity.calligraphy.v2"])

    # ---- 稳定事实：饮食禁忌 ----
    b.fact(
        fact_id="eldercare.diet.dislike",
        kind="state",
        subject=ELDER,
        attribute="饮食忌口",
        value="不吃香菜",
        value_keywords=["香菜"],
        valid_from_day=1,
        valid_to_day=None,
    )
    b.say(2, 11, 50, ELDER, "小梅，我这碗汤里别放香菜，我一点都闻不得那个味儿。", ["eldercare.diet.dislike"])
    b.say(20, 12, 0, NURSE, "食堂师傅，王奶奶的菜不要香菜，她忌口。", ["eldercare.diet.dislike"])

    # ---- 事件：身体不适 ----
    for fid, day, hour, minute, part, detail, kw in [
        ("eldercare.symptom.knee", 3, 10, 20, "膝盖", "右腿膝盖发酸，上下楼梯的时候疼得厉害", "膝盖"),
        ("eldercare.symptom.cough", 12, 22, 15, "咳嗽", "夜里咳嗽，躺下就咳，坐起来好一些", "咳嗽"),
        ("eldercare.symptom.dizzy", 21, 9, 40, "头晕", "早上起床猛一站起来眼前发黑、头晕", "头晕"),
    ]:
        b.fact(
            fact_id=fid,
            kind="event",
            subject=ELDER,
            attribute="身体不适",
            value=detail,
            value_keywords=[kw],
            valid_from_day=day,
            valid_to_day=day + 1,
        )
        b.say(day, hour, minute, ELDER, f"我{detail}。", [fid])
        b.say(day, hour, minute + 5, NURSE, f"我记一下，王奶奶今天说{part}不舒服，回头跟护士站说。", [fid])

    # ---- 事件：家属探视 ----
    visits = [
        ("eldercare.visit.d02", 2, 15, 0, SON, "带了两盒您爱吃的核桃酥"),
        ("eldercare.visit.d09", 9, 14, 30, GRANDDAUGHTER, "带了一束满天星，还给您念了会儿报纸"),
        ("eldercare.visit.d16", 16, 15, 20, SON, "带来一件新棉马甲，试了正合身"),
        ("eldercare.visit.d23", 23, 14, 50, f"{SON}、{GRANDDAUGHTER}", "爷孙俩一起来的，带了一台新收音机"),
        ("eldercare.visit.d28", 28, 16, 0, SON, "陪着聊了一个多小时，说下月接您回家住几天"),
    ]
    for fid, day, hour, minute, who, what in visits:
        b.fact(
            fact_id=fid,
            kind="event",
            subject=ELDER,
            attribute="家属探视",
            value=f"{who} 来探视，{what}",
            value_keywords=[who.split('-')[-1]],
            valid_from_day=day,
            valid_to_day=day + 1,
        )
        b.say(day, hour, minute, NURSE, f"王奶奶，{who}来看您了，{what}。", [fid])
        b.say(day, hour, minute + 10, ELDER, "来了就好，别老惦记着买东西，钱留着自己花。", [fid])

    _add_filler(b, ELDER_FILLER, rng, per_day=(3, 5))
    b.finalize()
    return b


# --------------------------------------------------------------------------
# 场景二：宠物主人日常
# --------------------------------------------------------------------------

PET_FILLER = [
    (7, 20, "陈屿", "豆包今天起得比我还早，在门口坐着等遛弯。"),
    (7, 50, "陈屿", "早上遛了四十分钟，绕着小区走了两圈。"),
    (12, 30, "陈屿", "豆包又把玩具球叼到沙发底下去了，够了半天才拿出来。"),
    (13, 10, "陈屿", "中午太阳好，它趴在阳台上睡了一下午。"),
    (18, 40, "陈屿", "晚上遛弯遇到楼下那只金毛，两个狗玩疯了。"),
    (19, 30, "陈屿", "给豆包梳了梳毛，掉毛季地上全是。"),
    (21, 0, "陈屿", "豆包今天挺乖的，没拆家。"),
    (8, 15, "陈屿", "今天出门前给它留了个漏食球，回来发现已经空了。"),
    (16, 20, "陈屿", "带豆包去了趟宠物店剪指甲，它全程发抖。"),
    (20, 15, "陈屿", "刷牙的时候它躲，只刷了前面几颗。"),
    (9, 40, "陈屿", "它最近特别喜欢趴在空调出风口底下。"),
    (17, 30, "陈屿", "邻居家小孩来摸它，它很温顺，一动不动。"),
    (22, 10, "陈屿", "豆包睡在它自己的窝里，打呼噜。"),
]


def build_pet(rng: random.Random) -> Builder:
    b = Builder("pet", rng)
    OWNER, VET = "陈屿", "刘医生"
    DOG = "豆包"

    # ---- 状态事实：狗粮品牌（第10天更换）----
    b.fact(
        fact_id="pet.food.brand.v1",
        kind="state",
        subject=DOG,
        attribute="主粮品牌",
        value="皇家幼犬粮",
        value_keywords=["皇家"],
        valid_from_day=1,
        valid_to_day=10,
        superseded_by="pet.food.brand.v2",
    )
    b.fact(
        fact_id="pet.food.brand.v2",
        kind="state",
        subject=DOG,
        attribute="主粮品牌",
        value="渴望成犬粮",
        value_keywords=["渴望"],
        valid_from_day=10,
        valid_to_day=None,
        supersedes="pet.food.brand.v1",
    )
    b.say(1, 7, 40, OWNER, "豆包早饭吃的还是皇家幼犬粮，一直吃这个牌子。", ["pet.food.brand.v1"])
    b.say(6, 21, 20, OWNER, "这两天便便有点软，不知道是不是皇家这袋粮不对付。", ["pet.food.brand.v1"])
    b.say(
        10, 11, 0, OWNER,
        "决定换粮了，皇家幼犬粮停掉，改喂渴望成犬粮，按七天过渡法慢慢换。",
        ["pet.food.brand.v1", "pet.food.brand.v2"],
    )
    b.say(17, 7, 45, OWNER, "过渡期结束，现在豆包吃的是纯渴望成犬粮了。", ["pet.food.brand.v2"])
    b.say(27, 7, 40, OWNER, "渴望这个粮吃了半个多月，便便一直是成型的，挺合适。", ["pet.food.brand.v2"])

    # ---- 状态事实：喂食量（第20天调整）----
    b.fact(
        fact_id="pet.food.amount.v1",
        kind="state",
        subject=DOG,
        attribute="每日喂食量",
        value="每天两顿，每顿 80 克",
        value_keywords=["80"],
        valid_from_day=1,
        valid_to_day=20,
        superseded_by="pet.food.amount.v2",
    )
    b.fact(
        fact_id="pet.food.amount.v2",
        kind="state",
        subject=DOG,
        attribute="每日喂食量",
        value="每天两顿，每顿 100 克",
        value_keywords=["100"],
        valid_from_day=20,
        valid_to_day=None,
        supersedes="pet.food.amount.v1",
    )
    b.say(2, 7, 30, OWNER, "一直是早晚两顿，一顿 80 克，用量杯量的。", ["pet.food.amount.v1"])
    b.say(20, 19, 10, OWNER, "刘医生说豆包偏瘦，让加量，从今天起每顿从 80 克加到 100 克。",
          ["pet.food.amount.v1", "pet.food.amount.v2"])
    b.say(25, 7, 35, OWNER, "现在一顿 100 克，它吃完还盯着碗看。", ["pet.food.amount.v2"])

    # ---- 状态事实：体重（三次记录，逐次更新）----
    weights = [
        ("pet.weight.v1", 8, "9.2", None, "pet.weight.v2", 19),
        ("pet.weight.v2", 19, "9.5", "pet.weight.v1", "pet.weight.v3", 26),
        ("pet.weight.v3", 26, "10.1", "pet.weight.v2", None, None),
    ]
    for fid, day, kg, prev, nxt, to_day in weights:
        b.fact(
            fact_id=fid,
            kind="state",
            subject=DOG,
            attribute="体重",
            value=f"{kg} 公斤",
            value_keywords=[kg],
            valid_from_day=day,
            valid_to_day=to_day,
            supersedes=prev,
            superseded_by=nxt,
        )

    # ---- 稳定事实：过敏 ----
    b.fact(
        fact_id="pet.allergy.chicken",
        kind="state",
        subject=DOG,
        attribute="过敏源",
        value="对鸡肉过敏，吃了会全身起红点、挠个不停",
        value_keywords=["鸡肉"],
        valid_from_day=1,
        valid_to_day=None,
    )
    b.say(4, 18, 30, OWNER, "买零食的时候得看配料，豆包对鸡肉过敏，吃了浑身起红点。", ["pet.allergy.chicken"])
    b.say(23, 15, 0, OWNER, "同事想喂豆包鸡胸肉干，我赶紧拦下了，它鸡肉过敏。", ["pet.allergy.chicken"])

    # ---- 事件：就医 ----
    b.fact(
        fact_id="pet.vet.d08",
        kind="event",
        subject=DOG,
        attribute="就医记录",
        value="因软便到安康动物医院就诊，刘医生诊断为换粮不当引起的肠胃不适，开了益生菌",
        value_keywords=["软便", "益生菌"],
        valid_from_day=8,
        valid_to_day=9,
    )
    b.say(8, 10, 30, OWNER, "带豆包去安康动物医院看软便了，挂的刘医生。", ["pet.vet.d08"])
    b.say(8, 11, 0, VET, "肠胃有点敏感，不是感染，先吃一周益生菌，粮也可以考虑换个低敏的。", ["pet.vet.d08"])
    b.say(8, 11, 10, VET, "顺便称了一下，9.2 公斤，这个月龄偏瘦一点点。", ["pet.weight.v1"])

    b.fact(
        fact_id="pet.vet.d19",
        kind="event",
        subject=DOG,
        attribute="就医记录",
        value="到安康动物医院打狂犬疫苗年度加强针",
        value_keywords=["狂犬", "疫苗"],
        valid_from_day=19,
        valid_to_day=20,
    )
    b.say(19, 10, 0, OWNER, "今天带豆包去打狂犬疫苗，一年一次的加强针。", ["pet.vet.d19"])
    b.say(19, 10, 20, VET, "疫苗打完了，观察半小时没反应就可以走。体重 9.5 公斤，比上次长了点。",
          ["pet.vet.d19", "pet.weight.v2"])

    b.fact(
        fact_id="pet.vet.d26",
        kind="event",
        subject=DOG,
        attribute="就医记录",
        value="到安康动物医院复查肠胃，刘医生说恢复良好，益生菌可以停",
        value_keywords=["复查"],
        valid_from_day=26,
        valid_to_day=27,
    )
    b.say(26, 10, 15, OWNER, "带豆包回安康动物医院复查肠胃。", ["pet.vet.d26"])
    b.say(26, 10, 40, VET, "恢复得不错，益生菌可以停了。体重 10.1 公斤，这一个月长了差不多一公斤。",
          ["pet.vet.d26", "pet.weight.v3"])

    # ---- 事件：行为 ----
    for fid, day, hour, minute, desc, kw in [
        ("pet.behavior.chew", 5, 20, 30, "趁我不在家把沙发角咬烂了一块，海绵都掏出来了", "沙发"),
        ("pet.behavior.thunder", 14, 22, 40, "打雷的时候钻到床底下不肯出来，抖得厉害", "打雷"),
        ("pet.behavior.shakehand", 22, 19, 0, "学会握手了，教了三天，一说'握手'就把爪子抬起来", "握手"),
    ]:
        b.fact(
            fact_id=fid,
            kind="event",
            subject=DOG,
            attribute="行为记录",
            value=desc,
            value_keywords=[kw],
            valid_from_day=day,
            valid_to_day=day + 1,
        )
        b.say(day, hour, minute, OWNER, f"豆包今天{desc}。", [fid])

    _add_filler(b, PET_FILLER, rng, per_day=(3, 5))
    b.finalize()
    return b


# --------------------------------------------------------------------------
# 场景三：机器人任务日志
# --------------------------------------------------------------------------

ROBOT_FILLER = [
    (8, 10, "RB-07", "系统自检完成，电量 98%，导航地图版本 v3.2。"),
    (9, 5, "RB-07", "已从充电桩脱离，进入待命状态。"),
    (11, 20, "RB-07", "在 1F 大厅巡航，识别到 3 名行人，已减速避让。"),
    (12, 40, "RB-07", "午间低峰，返回待命点待命。"),
    (14, 0, "RB-07", "电梯调度成功，从 1F 前往 2F。"),
    (15, 45, "RB-07", "路径被临时堆放的纸箱阻挡，已重新规划绕行。"),
    (17, 30, "RB-07", "今日累计行驶 4.2 公里，任务完成率 100%。"),
    (18, 10, "RB-07", "返回充电桩，开始充电，当前电量 23%。"),
    (10, 30, "前台-苏婷", "RB-07 今天看着挺利索的，没在门口卡住。"),
    (16, 50, "工程师-郑昊", "看了下日志，今天没有异常告警。"),
    (13, 25, "RB-07", "接收到清洁机器人的避让请求，已让行。"),
    (9, 50, "前台-苏婷", "有访客问这个机器人是干嘛的，我说是送文件的。"),
]


def build_robot(rng: random.Random) -> Builder:
    b = Builder("robot", rng)
    BOT, RECEP, ENG, GUEST = "RB-07", "前台-苏婷", "工程师-郑昊", "访客-林工"

    # ---- 状态事实：充电桩位置（第13天迁移）----
    b.fact(
        fact_id="robot.dock.location.v1",
        kind="state",
        subject=BOT,
        attribute="充电桩位置",
        value="B1 层东侧，靠近货梯",
        value_keywords=["东侧"],
        valid_from_day=1,
        valid_to_day=13,
        superseded_by="robot.dock.location.v2",
    )
    b.fact(
        fact_id="robot.dock.location.v2",
        kind="state",
        subject=BOT,
        attribute="充电桩位置",
        value="B1 层西侧，靠近楼梯间",
        value_keywords=["西侧"],
        valid_from_day=13,
        valid_to_day=None,
        supersedes="robot.dock.location.v1",
    )
    b.say(1, 18, 20, BOT, "任务结束，返回 B1 东侧充电桩，开始充电。", ["robot.dock.location.v1"])
    b.say(7, 18, 15, BOT, "返回 B1 东侧充电桩，开始充电。", ["robot.dock.location.v1"])
    b.say(13, 9, 0, ENG, "B1 东侧要做管道改造，充电桩今天挪到 B1 西侧楼梯间旁边，地图已经更新。",
          ["robot.dock.location.v1", "robot.dock.location.v2"])
    b.say(13, 18, 30, BOT, "返回 B1 西侧充电桩，开始充电。", ["robot.dock.location.v2"])
    b.say(28, 18, 25, BOT, "返回 B1 西侧充电桩，开始充电，当前电量 19%。", ["robot.dock.location.v2"])

    # ---- 状态事实：耗材库房（第20天搬迁）----
    b.fact(
        fact_id="robot.storage.location.v1",
        kind="state",
        subject="打印耗材",
        attribute="存放位置",
        value="3F 储物间",
        value_keywords=["3F"],
        valid_from_day=1,
        valid_to_day=20,
        superseded_by="robot.storage.location.v2",
    )
    b.fact(
        fact_id="robot.storage.location.v2",
        kind="state",
        subject="打印耗材",
        attribute="存放位置",
        value="2F 储物间",
        value_keywords=["2F"],
        valid_from_day=20,
        valid_to_day=None,
        supersedes="robot.storage.location.v1",
    )
    b.say(4, 10, 10, RECEP, "打印纸和硒鼓都在 3F 储物间，需要的时候让 RB-07 去取。", ["robot.storage.location.v1"])
    b.say(20, 9, 30, RECEP, "行政把耗材从 3F 储物间搬到 2F 储物间了，以后取件去 2F。",
          ["robot.storage.location.v1", "robot.storage.location.v2"])
    b.say(24, 11, 15, BOT, "前往 2F 储物间取硒鼓一只。", ["robot.storage.location.v2"])

    # ---- 状态事实：郑昊工位（第11天调整）----
    b.fact(
        fact_id="robot.person.zhenghao.desk.v1",
        kind="state",
        subject=ENG,
        attribute="工位",
        value="2F-A12",
        value_keywords=["A12"],
        valid_from_day=1,
        valid_to_day=11,
        superseded_by="robot.person.zhenghao.desk.v2",
    )
    b.fact(
        fact_id="robot.person.zhenghao.desk.v2",
        kind="state",
        subject=ENG,
        attribute="工位",
        value="3F-C03",
        value_keywords=["C03"],
        valid_from_day=11,
        valid_to_day=None,
        supersedes="robot.person.zhenghao.desk.v1",
    )
    b.say(2, 14, 10, BOT, "配送任务：文件袋一个，收件人 工程师-郑昊，送达 2F-A12。", ["robot.person.zhenghao.desk.v1"])
    b.say(11, 9, 20, ENG, "我搬工位了，从 2F-A12 换到 3F-C03，以后东西送 3F。",
          ["robot.person.zhenghao.desk.v1", "robot.person.zhenghao.desk.v2"])
    b.say(15, 14, 5, BOT, "配送任务：备件盒一个，收件人 工程师-郑昊，送达 3F-C03。", ["robot.person.zhenghao.desk.v2"])
    b.say(27, 10, 40, BOT, "配送任务：签字文件一份，收件人 工程师-郑昊，送达 3F-C03。", ["robot.person.zhenghao.desk.v2"])

    # ---- 事件：故障 ----
    for fid, day, hour, minute, code, desc, kw in [
        ("robot.fault.d07", 7, 11, 30, "E-204", "左前轮打滑，在 1F 大厅湿滑地面上原地空转了约 5 秒", "左前轮"),
        ("robot.fault.d15", 15, 16, 20, "E-311", "激光雷达镜面积尘，测距置信度下降到 0.62，触发降速", "激光雷达"),
        ("robot.fault.d24", 24, 8, 40, "E-508", "电池健康度告警，容量衰减至标称值的 81%", "电池健康度"),
    ]:
        b.fact(
            fact_id=fid,
            kind="event",
            subject=BOT,
            attribute="故障记录",
            value=f"{code}：{desc}",
            value_keywords=[code, kw],
            valid_from_day=day,
            valid_to_day=day + 1,
        )
        b.say(day, hour, minute, BOT, f"告警 {code}：{desc}。", [fid])
        b.say(day, hour, minute + 20, ENG, f"收到 {code} 告警，已现场处理并记录。", [fid])

    # ---- 事件：访客接待 ----
    b.fact(
        fact_id="robot.visit.d06",
        kind="event",
        subject=GUEST,
        attribute="访客接待",
        value="访客-林工 来访，由 RB-07 从 1F 前台引导至 2F 会议室",
        value_keywords=["林工"],
        valid_from_day=6,
        valid_to_day=7,
    )
    b.say(6, 10, 0, RECEP, "林工到了，RB-07 带他去 2F 会议室。", ["robot.visit.d06"])
    b.say(6, 10, 5, BOT, "引导任务开始：访客-林工，1F 前台 → 2F 会议室。", ["robot.visit.d06"])

    b.fact(
        fact_id="robot.visit.d18",
        kind="event",
        subject=GUEST,
        attribute="访客接待",
        value="访客-林工 第二次来访，由 RB-07 引导至 3F-C03 找郑昊",
        value_keywords=["林工"],
        valid_from_day=18,
        valid_to_day=19,
    )
    b.say(18, 14, 30, RECEP, "林工又来了，这次找郑昊，RB-07 带他上 3F。", ["robot.visit.d18"])
    b.say(18, 14, 35, BOT, "引导任务开始：访客-林工，1F 前台 → 3F-C03。",
          ["robot.visit.d18", "robot.person.zhenghao.desk.v2"])

    _add_filler(b, ROBOT_FILLER, rng, per_day=(3, 5))
    b.finalize()
    return b


# --------------------------------------------------------------------------
# 闲聊填充
# --------------------------------------------------------------------------


def _add_filler(
    b: Builder, pool: list[tuple[int, int, str, str]], rng: random.Random, per_day: tuple[int, int]
) -> None:
    """给每一天补充若干条与事实无关的闲聊，制造检索噪声。

    闲聊语料是人工筛选过的：不与任何事实冲突，也不包含"拒答类"问题涉及的话题。
    """
    lo, hi = per_day
    for day in range(1, NUM_DAYS + 1):
        n = rng.randint(lo, hi)
        for hour, minute, speaker, text in rng.sample(pool, n):
            jitter = rng.randint(-7, 7)
            m = max(0, min(59, minute + jitter))
            b.say(day, hour, m, speaker, text, [])


# --------------------------------------------------------------------------
# 输出与自检
# --------------------------------------------------------------------------

BUILDERS = {
    "eldercare": build_eldercare,
    "pet": build_pet,
    "robot": build_robot,
}

SCENARIO_TITLES = {
    "eldercare": "养老院长者日常对话",
    "pet": "宠物主人日常记录",
    "robot": "机器人任务日志",
}


def self_check(builders: dict[str, Builder]) -> list[str]:
    """一致性自检。返回问题清单，为空表示通过。"""
    problems: list[str] = []
    for name, b in builders.items():
        days = {u.day for u in b.utterances}
        missing = set(range(1, NUM_DAYS + 1)) - days
        if missing:
            problems.append(f"[{name}] 缺少这些天的数据: {sorted(missing)}")

        # 状态型事实的版本链必须首尾相接、无空洞、无重叠
        chains: dict[tuple[str, str], list[Fact]] = {}
        for f in b.facts:
            if f.kind == "state":
                chains.setdefault((f.subject, f.attribute), []).append(f)
        for key, fs in chains.items():
            fs.sort(key=lambda f: f.valid_from_day)
            for a, c in zip(fs, fs[1:]):
                if a.valid_to_day != c.valid_from_day:
                    problems.append(
                        f"[{name}] {key} 版本链不连续: {a.fact_id} 到 {a.valid_to_day}，"
                        f"{c.fact_id} 从 {c.valid_from_day} 起"
                    )
                if a.superseded_by != c.fact_id or c.supersedes != a.fact_id:
                    problems.append(f"[{name}] {key} 的 supersedes/superseded_by 指针不对称")
            if fs[-1].valid_to_day is not None and fs[-1].superseded_by is None:
                problems.append(f"[{name}] {fs[-1].fact_id} 有失效日但没有后继版本")

        # 每条事实的证据必须落在其生效窗口内（允许"公布变更"的那条跨界引用）
        by_id = {u.utt_id: u for u in b.utterances}
        for f in b.facts:
            for uid in f.evidence:
                u = by_id[uid]
                if u.day < f.valid_from_day - 1:
                    problems.append(f"[{name}] {f.fact_id} 的证据 {uid} 早于生效日 {f.valid_from_day}")
    return problems


def write_out(builders: dict[str, Builder], out_dir: Path) -> dict:
    manifest = {
        "seed": SEED,
        "start_date": START_DATE.isoformat(),
        "end_date": day_to_date(NUM_DAYS).isoformat(),
        "num_days": NUM_DAYS,
        "note": "模拟数据，非真实录音转写。真实数据到位后需重测关键项。",
        "scenarios": {},
    }
    for name, b in builders.items():
        sdir = out_dir / name
        sdir.mkdir(parents=True, exist_ok=True)
        by_day: dict[int, list[Utterance]] = {}
        for u in b.utterances:
            by_day.setdefault(u.day, []).append(u)
        for day in range(1, NUM_DAYS + 1):
            path = sdir / f"day_{day:02d}.jsonl"
            with path.open("w", encoding="utf-8") as fh:
                for u in by_day.get(day, []):
                    fh.write(json.dumps(asdict(u), ensure_ascii=False) + "\n")
        (sdir / "facts_timeline.json").write_text(
            json.dumps([f.to_json() for f in b.facts], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        manifest["scenarios"][name] = {
            "title": SCENARIO_TITLES[name],
            "utterances": len(b.utterances),
            "facts": len(b.facts),
            "state_facts": sum(1 for f in b.facts if f.kind == "state"),
            "event_facts": sum(1 for f in b.facts if f.kind == "event"),
            "superseded_facts": sum(1 for f in b.facts if f.superseded_by),
        }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def build_all() -> dict[str, Builder]:
    return {name: fn(random.Random(SEED + i)) for i, (name, fn) in enumerate(BUILDERS.items())}


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 memory-bench 模拟测试数据")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "data"))
    ap.add_argument("--check", action="store_true", help="只做自检，不写文件")
    args = ap.parse_args()

    builders = build_all()
    problems = self_check(builders)
    if problems:
        print("自检未通过:")
        for p in problems:
            print("  -", p)
        return 1
    print("自检通过。")

    if args.check:
        return 0

    manifest = write_out(builders, Path(args.out))
    print(f"数据已写入 {args.out}")
    print(f"时间跨度 {manifest['start_date']} ~ {manifest['end_date']}（{NUM_DAYS} 天）")
    for name, s in manifest["scenarios"].items():
        print(
            f"  {name:10s} {s['utterances']:4d} 条对话 / {s['facts']:2d} 条事实"
            f"（状态 {s['state_facts']}，事件 {s['event_facts']}，发生变更 {s['superseded_facts']}）"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
