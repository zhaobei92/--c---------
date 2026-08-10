#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成并校验 question_set.json。

问题以声明式表格写在本文件里，但**所有问题都会对着已生成的语料自动校验**：

* 证据用「第几天 + 原文片段」定位，脚本自己解析成 utt_id；片段必须在那一天里
  唯一命中一条对话，命中 0 条或多条都算校验失败（避免人工誊抄 id 出错）；
* 判分关键词必须真的能在证据原文中找到（``derived`` 里声明的推导型答案除外，
  比如"一共 5 次""涨了 0.9 公斤"这类计算结果本来就不会逐字出现在对话里）；
* "拒答类"问题声明的 ``probes`` 必须在整个场景语料中一次都不出现，
  ``cooccur`` 里的词组不得出现在同一条对话中——这才能保证"数据里确实没有这件
  事"，而不是我以为没有。

任何一条校验不过，脚本直接非零退出，不产出文件。

用法::

    python3 scripts/gen_question_set.py            # 校验并写出 question_set.json
    python3 scripts/gen_question_set.py --check    # 只校验
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT_PATH = ROOT / "question_set.json"

START_DATE = dt.date(2026, 3, 2)
NUM_DAYS = 30
SCENARIOS = ["eldercare", "pet", "robot"]

QUESTION_TYPES = {
    "fact_recall": "事实回忆：某人某天说过/发生过什么",
    "temporal_locate": "时间定位：按（可能是相对的）时间锚点检索当时的内容",
    "fact_change": "事实变更：考察新旧事实覆盖，含按历史时点回溯提问",
    "cross_session": "跨会话综合：需要拼接多天信息才能回答",
    "refusal": "拒答/防编造：数据中不存在的事，看框架会不会瞎编",
}

# 判分时认可的"拒答"表述
REFUSAL_MARKERS = [
    "不知道", "不清楚", "没有提到", "未提到", "没有提及", "未提及", "没有记录",
    "无记录", "没有相关", "无相关", "没有找到", "未找到", "无法确定", "不确定",
    "数据中没有", "记忆中没有", "没有信息", "无此信息", "没有这方面", "无从得知",
]

# 判定"已知这是旧事实"的转折标记——旧值只有在这些标记陪同下出现才不算答错
CHANGE_MARKERS = [
    "之前", "原来", "原先", "此前", "先前", "以前", "早期", "已停", "停用", "停掉",
    "改为", "改成", "换成", "换为", "更换", "调整为", "调整成", "不再", "旧", "曾",
    "变更", "→", "->",
]


def day_to_date(day: int) -> dt.date:
    return START_DATE + dt.timedelta(days=day - 1)


def as_of_iso(day: int, hour: int = 20) -> str:
    d = day_to_date(day)
    return dt.datetime(d.year, d.month, d.day, hour, 0).isoformat()


# --------------------------------------------------------------------------
# 语料装载
# --------------------------------------------------------------------------


def load_corpus():
    """返回 (utt_id -> utterance, (scenario, day) -> [utterance])。"""
    utts: dict[str, dict] = {}
    by_day: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for s in SCENARIOS:
        sdir = DATA_DIR / s
        if not sdir.exists():
            sys.exit(f"找不到 {sdir}，请先运行 scripts/gen_data.py")
        for day in range(1, NUM_DAYS + 1):
            for line in (sdir / f"day_{day:02d}.jsonl").read_text(encoding="utf-8").splitlines():
                if line.strip():
                    u = json.loads(line)
                    utts[u["utt_id"]] = u
                    by_day[(s, day)].append(u)
    return utts, by_day


# --------------------------------------------------------------------------
# 问题声明
#
# 字段约定：
#   ev        证据定位器列表，每项为 [第几天, 原文片段]，须在当天唯一命中
#   inc       must_include，全部必须出现在框架回答中
#   any       any_of，每个子列表至少命中一个
#   stale     旧值关键词，出现时必须伴随转折标记，否则判错（变更题专用）
#   derived   推导型答案词（计数、差值、由时间戳得出的日期等），跳过证据原文校验
#   as_of_day 提问时点，默认第 30 天
# --------------------------------------------------------------------------

FACT_RECALL = [
    dict(
        scenario="eldercare",
        q="3月11日陈医生对王秀兰的降压用药做了什么交代？",
        gold="停用氨氯地平，改为缬沙坦 80mg，每天早饭后一片，两周后复查。",
        ev=[[10, "换成缬沙坦"]],
        inc=["缬沙坦"],
        any=[["80"], ["氨氯地平"]],
    ),
    dict(
        scenario="eldercare",
        q="3月4日王秀兰说自己身体哪里不舒服？",
        gold="右腿膝盖发酸，上下楼梯时疼得厉害。",
        ev=[[3, "膝盖发酸"], [3, "膝盖不舒服"]],
        inc=["膝盖"],
    ),
    dict(
        scenario="eldercare",
        q="3月19日陈医生给王秀兰新加了什么药，怎么吃？",
        gold="加了右佐匹克隆 1mg，睡前服用，睡得着就不吃，不要每天用。",
        ev=[[18, "右佐匹克隆"]],
        inc=["右佐匹克隆"],
        any=[["睡前", "1mg"]],
    ),
    dict(
        scenario="eldercare",
        q="3月3日王秀兰对护工提了什么饮食要求？",
        gold="汤里不要放香菜，她闻不得那个味道。",
        ev=[[2, "别放香菜"]],
        inc=["香菜"],
    ),
    dict(
        scenario="pet",
        q="3月11日陈屿决定把豆包的主粮换成什么？",
        gold="换成渴望成犬粮，按七天过渡法慢慢换。",
        ev=[[10, "渴望成犬粮"]],
        inc=["渴望"],
    ),
    dict(
        scenario="pet",
        q="3月9日刘医生对豆包的诊断和处置是什么？",
        gold="肠胃敏感、不是感染，先吃一周益生菌，建议换低敏粮。",
        ev=[[8, "益生菌"]],
        inc=["益生菌"],
        any=[["肠胃", "敏感"]],
    ),
    dict(
        scenario="pet",
        q="3月5日陈屿提到豆包对什么食物过敏？",
        gold="对鸡肉过敏，吃了浑身起红点。",
        ev=[[4, "鸡肉过敏"]],
        inc=["鸡肉"],
    ),
    dict(
        scenario="pet",
        q="3月20日豆包去医院做了什么？",
        gold="打狂犬疫苗的年度加强针。",
        ev=[[19, "狂犬疫苗"], [19, "疫苗打完了"]],
        inc=["疫苗"],
        any=[["狂犬"]],
    ),
    dict(
        scenario="robot",
        q="3月14日郑昊通知了充电桩的什么变动？",
        gold="因为 B1 东侧做管道改造，充电桩挪到 B1 西侧楼梯间旁边，地图已更新。",
        ev=[[13, "西侧楼梯间"]],
        inc=["西侧"],
        any=[["B1"]],
    ),
    dict(
        scenario="robot",
        q="3月8日 RB-07 报了什么故障？",
        gold="E-204 告警：左前轮在 1F 大厅湿滑地面打滑，原地空转约 5 秒。",
        ev=[[7, "告警 E-204"], [7, "收到 E-204"]],
        any=[["E-204", "左前轮", "打滑"]],
    ),
    dict(
        scenario="robot",
        q="3月12日郑昊说自己的工位有什么变化？",
        gold="从 2F-A12 搬到 3F-C03，以后东西送 3F。",
        ev=[[11, "我搬工位了"]],
        inc=["C03"],
        any=[["A12"]],
    ),
    dict(
        scenario="robot",
        q="3月21日前台苏婷通知打印耗材放到哪里了？",
        gold="行政把耗材从 3F 储物间搬到了 2F 储物间，以后取件去 2F。",
        ev=[[20, "搬到 2F 储物间"]],
        inc=["2F"],
        any=[["储物间"]],
    ),
]

TEMPORAL_LOCATE = [
    dict(
        scenario="eldercare",
        q="上周三下午护工通知了什么事？",
        gold="书法班的周老师调课，从当周起改到周四下午两点半，地点仍是二楼活动室。",
        ev=[[17, "周老师调课"]],
        inc=["书法"],
        any=[["周四"]],
        as_of_day=24,
        note="按本项目约定，'上周三'= 提问日所在自然周（周一为首）的前一周的周三，此处解析为 3 月 18 日。",
    ),
    dict(
        scenario="eldercare",
        q="上周三下午王秀兰参加了什么活动？",
        gold="参加书法班，在二楼活动室。",
        ev=[[3, "周三下午的书法班"]],
        inc=["书法"],
        as_of_day=12,
        note="'上周三'解析为 3 月 4 日。",
    ),
    dict(
        scenario="eldercare",
        q="3月24日下午王秀兰这边发生了什么？",
        gold="儿子王建国和孙女王小雨一起来探视，带了一台新收音机。",
        ev=[[23, "新收音机"]],
        inc=["收音机"],
        any=[["儿子", "建国"], ["孙女", "小雨"]],
    ),
    dict(
        scenario="eldercare",
        q="3月13日夜里王秀兰报告了什么身体状况？",
        gold="夜里咳嗽，躺下就咳，坐起来好一些。",
        ev=[[12, "躺下就咳"], [12, "咳嗽不舒服"]],
        inc=["咳嗽"],
    ),
    dict(
        scenario="pet",
        q="上周三早上陈屿记录了豆包吃粮的什么情况？",
        gold="七天过渡期结束，豆包已经在吃纯渴望成犬粮。",
        ev=[[17, "过渡期结束"]],
        inc=["渴望"],
        any=[["过渡", "纯"]],
        as_of_day=26,
        note="'上周三'解析为 3 月 18 日。",
    ),
    dict(
        scenario="pet",
        q="3月21日晚上陈屿对豆包的喂食量做了什么调整？",
        gold="因为刘医生说偏瘦，每顿从 80 克加到 100 克。",
        ev=[[20, "加到 100 克"]],
        inc=["100"],
        any=[["80"]],
    ),
    dict(
        scenario="pet",
        q="3月23日晚上豆包做了什么？",
        gold="学会了握手，教了三天，一说'握手'就抬爪子。",
        ev=[[22, "学会握手"]],
        inc=["握手"],
    ),
    dict(
        scenario="pet",
        q="3月9日上午豆包在医院称的体重是多少？",
        gold="9.2 公斤。",
        ev=[[8, "9.2 公斤"]],
        inc=["9.2"],
    ),
    dict(
        scenario="robot",
        q="3月16日下午 RB-07 报了什么告警？",
        gold="E-311 告警：激光雷达镜面积尘，测距置信度降到 0.62，触发降速。",
        ev=[[15, "告警 E-311"], [15, "收到 E-311"]],
        any=[["E-311", "激光雷达"]],
    ),
    dict(
        scenario="robot",
        q="上周三上午 RB-07 报了什么告警？",
        gold="E-508 告警：电池健康度告警，容量衰减至标称值的 81%。",
        ev=[[24, "告警 E-508"], [24, "收到 E-508"]],
        any=[["E-508", "电池"]],
        as_of_day=30,
        note="'上周三'解析为 3 月 25 日。",
    ),
    dict(
        scenario="robot",
        q="3月19日下午前台安排 RB-07 做了什么？",
        gold="引导第二次来访的林工从 1F 前台去 3F-C03 找郑昊。",
        ev=[[18, "林工又来了"], [18, "3F-C03"]],
        inc=["林工"],
        any=[["3F", "C03"]],
    ),
    dict(
        scenario="robot",
        q="3月5日上午前台苏婷说打印耗材放在哪里？",
        gold="打印纸和硒鼓都在 3F 储物间。",
        ev=[[4, "3F 储物间"]],
        inc=["3F"],
    ),
]

FACT_CHANGE = [
    dict(
        scenario="eldercare",
        q="王秀兰现在的降压药方案是什么？",
        gold="缬沙坦 80mg，每日一次，早饭后服用。",
        ev=[[10, "换成缬沙坦"], [24, "换缬沙坦两周了"]],
        inc=["缬沙坦"],
        stale=["氨氯地平"],
    ),
    dict(
        scenario="eldercare",
        q="王秀兰的书法班现在是周几下午？",
        gold="周四下午两点半，二楼活动室。",
        ev=[[17, "周老师调课"], [25, "周四下午的书法班"]],
        inc=["周四"],
        stale=["周三"],
    ),
    dict(
        scenario="eldercare",
        q="截至3月9日，王秀兰吃的降压药是什么？",
        gold="氨氯地平 5mg，每日一次（3 月 11 日才换成缬沙坦，此时尚未更换）。",
        ev=[[1, "氨氯地平吃了没有"], [5, "氨氯地平吃了快一周"]],
        inc=["氨氯地平"],
        stale=["缬沙坦"],
        as_of_day=8,
        requires_as_of=True,
        note="按历史时点回溯提问。只保留'最新事实'的框架会答成缬沙坦，属于典型失分点。",
    ),
    dict(
        scenario="eldercare",
        q="王秀兰的助眠药是什么？从哪天开始吃的？",
        gold="右佐匹克隆 1mg 睡前服用，从 3 月 19 日开始，且只在睡不着时吃。",
        ev=[[18, "右佐匹克隆"]],
        inc=["右佐匹克隆"],
        any=[["3月19", "19日"]],
        derived=["3月19", "19日"],
    ),
    dict(
        scenario="pet",
        q="豆包现在吃的是什么牌子的粮？",
        gold="渴望成犬粮。",
        ev=[[10, "渴望成犬粮"], [27, "渴望这个粮"]],
        inc=["渴望"],
        stale=["皇家"],
    ),
    dict(
        scenario="pet",
        q="豆包现在每顿喂多少克？",
        gold="每顿 100 克，每天两顿。",
        ev=[[20, "加到 100 克"], [25, "一顿 100 克"]],
        inc=["100"],
        stale=["80"],
    ),
    dict(
        scenario="pet",
        q="豆包最新一次称重是多少？",
        gold="10.1 公斤（3 月 27 日复查时称的）。",
        ev=[[26, "10.1 公斤"]],
        inc=["10.1"],
        stale=["9.2", "9.5"],
    ),
    dict(
        scenario="pet",
        q="截至3月16日，豆包每顿的喂食量是多少？",
        gold="每顿 80 克（3 月 21 日才加到 100 克）。",
        ev=[[2, "一顿 80 克"]],
        inc=["80"],
        stale=["100"],
        as_of_day=15,
        requires_as_of=True,
    ),
    dict(
        scenario="robot",
        q="RB-07 现在的充电桩在什么位置？",
        gold="B1 层西侧，靠近楼梯间。",
        ev=[[13, "西侧楼梯间"], [28, "B1 西侧充电桩"]],
        inc=["西侧"],
        stale=["东侧"],
    ),
    dict(
        scenario="robot",
        q="打印耗材现在存放在哪里？",
        gold="2F 储物间。",
        ev=[[20, "搬到 2F 储物间"], [24, "前往 2F 储物间"]],
        inc=["2F"],
        stale=["3F"],
    ),
    dict(
        scenario="robot",
        q="郑昊现在的工位在哪里？",
        gold="3F-C03。",
        ev=[[11, "我搬工位了"], [27, "签字文件"]],
        inc=["C03"],
        stale=["A12"],
    ),
    dict(
        scenario="robot",
        q="截至3月10日，RB-07 的充电桩在什么位置？",
        gold="B1 层东侧，靠近货梯（3 月 14 日才挪到西侧）。",
        ev=[[1, "B1 东侧充电桩"], [7, "B1 东侧充电桩"]],
        inc=["东侧"],
        stale=["西侧"],
        as_of_day=9,
        requires_as_of=True,
    ),
]

CROSS_SESSION = [
    dict(
        scenario="eldercare",
        q="这一个月里王秀兰的家属一共来看过几次？分别是谁来的？",
        gold="5 次：3 月 3 日儿子王建国、3 月 10 日孙女王小雨、3 月 17 日儿子、3 月 24 日儿子和孙女同来、3 月 31 日儿子。",
        ev=[[2, "核桃酥"], [9, "满天星"], [16, "棉马甲"], [23, "新收音机"], [28, "接您回家"]],
        inc=["5"],
        any=[["建国", "儿子"], ["小雨", "孙女"]],
        derived=["5"],
    ),
    dict(
        scenario="eldercare",
        q="王秀兰的降压药换过几次？从什么换成了什么？为什么换？",
        gold="换过 1 次：3 月 11 日因血压持续偏高（150 上下），从氨氯地平 5mg 换成缬沙坦 80mg。",
        ev=[[5, "152/94"], [9, "跟陈医生反映"], [10, "换成缬沙坦"]],
        inc=["氨氯地平", "缬沙坦"],
        any=[["血压", "偏高", "150"]],
    ),
    dict(
        scenario="eldercare",
        q="王秀兰这个月总共报告过哪些身体不适？分别是哪天？",
        gold="3 次：3 月 4 日膝盖疼、3 月 13 日夜间咳嗽、3 月 22 日头晕。",
        ev=[[3, "膝盖发酸"], [12, "躺下就咳"], [21, "眼前发黑"]],
        inc=["膝盖", "咳嗽", "头晕"],
    ),
    dict(
        scenario="eldercare",
        q="王秀兰换降压药前后，血压读数有什么变化？",
        gold="换药前 3 月 6 日 152/94、3 月 10 日连续三天 150 上下；换药后 3 月 25 日降到 132/82。",
        ev=[[5, "152/94"], [9, "150 上下"], [24, "132/82"]],
        inc=["132"],
        any=[["152", "150"]],
    ),
    dict(
        scenario="pet",
        q="豆包从第一次称重到最后一次称重，体重长了多少？",
        gold="从 9.2 公斤长到 10.1 公斤，涨了约 0.9 公斤。",
        ev=[[8, "9.2 公斤"], [19, "9.5 公斤"], [26, "10.1 公斤"]],
        inc=["9.2", "10.1"],
        any=[["0.9", "0.90"]],
        derived=["0.9", "0.90"],
    ),
    dict(
        scenario="pet",
        q="豆包为什么换粮？换粮前后分别吃的是什么？",
        gold="因为软便、肠胃敏感（3 月 9 日就诊后刘医生建议换低敏粮），从皇家幼犬粮换成渴望成犬粮。",
        ev=[[6, "便便有点软"], [8, "益生菌"], [10, "渴望成犬粮"]],
        inc=["皇家", "渴望"],
        any=[["软便", "肠胃", "便便"]],
    ),
    dict(
        scenario="pet",
        q="豆包这个月去过几次宠物医院？分别是为什么？",
        gold="3 次：3 月 9 日看软便、3 月 20 日打狂犬疫苗、3 月 27 日复查肠胃。",
        ev=[[8, "看软便"], [19, "狂犬疫苗"], [26, "复查肠胃"]],
        inc=["3"],
        any=[["软便", "肠胃"], ["疫苗"], ["复查"]],
        derived=["3"],
    ),
    dict(
        scenario="pet",
        q="豆包的喂食量调整和它的体重变化之间有什么关系？",
        gold="3 月 21 日因偏瘦把每顿 80 克加到 100 克；此前 3 月 20 日体重 9.5 公斤，加量后 3 月 27 日复查涨到 10.1 公斤。",
        ev=[[20, "加到 100 克"], [19, "9.5 公斤"], [26, "10.1 公斤"]],
        inc=["100", "10.1"],
        any=[["9.5"]],
    ),
    dict(
        scenario="robot",
        q="RB-07 这个月出现过几次告警？分别是什么？",
        gold="3 次：3 月 8 日 E-204 左前轮打滑、3 月 16 日 E-311 激光雷达积尘、3 月 25 日 E-508 电池健康度告警。",
        ev=[[7, "告警 E-204"], [15, "告警 E-311"], [24, "告警 E-508"]],
        inc=["E-204", "E-311", "E-508"],
    ),
    dict(
        scenario="robot",
        q="访客林工来过几次？每次分别去了哪里？",
        gold="2 次：3 月 7 日由 RB-07 从 1F 前台引导到 2F 会议室；3 月 19 日引导到 3F-C03 找郑昊。",
        ev=[[6, "林工到了"], [6, "引导任务开始"], [18, "林工又来了"], [18, "3F-C03"]],
        inc=["林工"],
        any=[["会议室"], ["C03", "3F"]],
    ),
    dict(
        scenario="robot",
        q="给郑昊送东西的地点前后有什么不同？为什么会变？",
        gold="3 月 3 日送到 2F-A12；3 月 12 日他搬工位后，3 月 16 日和 3 月 28 日都送到 3F-C03。",
        ev=[[2, "文件袋"], [11, "我搬工位了"], [15, "备件盒"], [27, "签字文件"]],
        inc=["A12", "C03"],
        any=[["搬", "换", "调整"]],
    ),
    dict(
        scenario="robot",
        q="这个月办公楼里一共发生了哪些位置变更？",
        gold="3 项：3 月 12 日郑昊工位 2F-A12→3F-C03；3 月 14 日充电桩 B1 东侧→西侧；3 月 21 日打印耗材 3F 储物间→2F 储物间。",
        ev=[[11, "我搬工位了"], [13, "西侧楼梯间"], [20, "搬到 2F 储物间"]],
        inc=["充电桩", "耗材"],
        any=[["工位", "A12", "C03"]],
    ),
]

REFUSAL = [
    dict(
        scenario="eldercare",
        q="王秀兰的糖尿病用的是什么药？",
        gold="数据中没有王秀兰患糖尿病或使用糖尿病药物的任何记录，应明确表示不知道。",
        probes=["糖尿病", "二甲双胍", "胰岛素", "血糖"],
    ),
    dict(
        scenario="eldercare",
        q="王秀兰的女儿上次是什么时候来看她的？",
        gold="数据中只出现过儿子王建国和孙女王小雨，没有女儿，应明确指出没有相关记录。",
        probes=["女儿"],
    ),
    dict(
        scenario="eldercare",
        q="王秀兰住在几号房间？",
        gold="数据中从未提到过房间号或床位号，应明确表示不知道。",
        probes=["房间", "床位", "号房"],
    ),
    dict(
        scenario="eldercare",
        q="王秀兰对青霉素过敏吗？",
        gold="数据中没有王秀兰任何药物过敏的记录，应明确表示无相关信息，不得臆断为'不过敏'。",
        probes=["青霉素", "过敏"],
    ),
    dict(
        scenario="pet",
        q="豆包做过绝育手术吗？",
        gold="数据中从未提到绝育，应明确表示没有相关记录。",
        probes=["绝育", "手术"],
    ),
    dict(
        scenario="pet",
        q="豆包用的驱虫药是什么牌子？",
        gold="数据中从未提到驱虫，应明确表示不知道。",
        probes=["驱虫", "跳蚤"],
    ),
    dict(
        scenario="pet",
        q="陈屿家除了豆包还养了别的宠物吗？",
        gold="数据中只出现过豆包一只狗，没有提到其他宠物，应说明没有相关记录。",
        probes=["猫", "仓鼠", "另一只"],
    ),
    dict(
        scenario="pet",
        q="豆包上次洗澡是什么时候？",
        gold="数据中提到过剪指甲、梳毛、刷牙，但从未提到洗澡，应明确表示没有记录。",
        probes=["洗澡", "沐浴"],
    ),
    dict(
        scenario="robot",
        q="RB-07 在 4 楼执行过哪些任务？",
        gold="这栋楼在数据中只出现过 B1、1F、2F、3F，没有 4 楼，应指出无相关记录。",
        probes=["4F", "4 楼", "4楼", "四楼"],
    ),
    dict(
        scenario="robot",
        q="RB-07 的固件版本号是多少？",
        gold="数据中只提到导航地图版本 v3.2，从未提到固件版本，应明确表示不知道，不得拿地图版本冒充。",
        probes=["固件"],
        forbidden=["v3.2", "3.2"],
        note="干扰项：语料里有'导航地图版本 v3.2'，答成 v3.2 属于典型的张冠李戴式编造。",
    ),
    dict(
        scenario="robot",
        q="前台苏婷的工位编号是多少？",
        gold="数据中只给出了郑昊的工位编号，没有苏婷的，应明确表示没有记录。",
        cooccur=[["苏婷", "工位"]],
        forbidden=["A12", "C03"],
    ),
    dict(
        scenario="robot",
        q="RB-07 这个月更换过电池吗？",
        gold="数据中只有 3 月 25 日的电池健康度告警（E-508），没有任何更换电池的记录，应说明未见更换记录。",
        cooccur=[["更换", "电池"], ["换", "电池"]],
    ),
]


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------

GROUPS = [
    ("fact_recall", FACT_RECALL),
    ("temporal_locate", TEMPORAL_LOCATE),
    ("fact_change", FACT_CHANGE),
    ("cross_session", CROSS_SESSION),
    ("refusal", REFUSAL),
]


def resolve_evidence(scenario, locators, by_day, qid, problems) -> list[str]:
    """把 [第几天, 原文片段] 解析成 utt_id，要求当天唯一命中。"""
    ids: list[str] = []
    for day, sub in locators:
        cands = [u for u in by_day.get((scenario, day), []) if sub in u["text"]]
        if len(cands) != 1:
            problems.append(
                f"{qid} 的证据定位器 [第{day}天, 「{sub}」] 命中 {len(cands)} 条，要求恰好 1 条"
            )
            continue
        ids.append(cands[0]["utt_id"])
    return ids


def build_questions(by_day, problems) -> list[dict]:
    out: list[dict] = []
    n = 0
    for qtype, items in GROUPS:
        for it in items:
            n += 1
            qid = f"Q{n:03d}"
            as_of_day = it.get("as_of_day", NUM_DAYS)
            q = {
                "qid": qid,
                "scenario": it["scenario"],
                "type": qtype,
                "question": it["q"],
                "as_of": as_of_iso(as_of_day),
                "as_of_day": as_of_day,
                "gold_answer": it["gold"],
                "gold_evidence": resolve_evidence(
                    it["scenario"], it.get("ev", []), by_day, qid, problems
                ),
            }
            if qtype == "refusal":
                q["scoring"] = {
                    "rule": "refusal",
                    "refusal_markers": REFUSAL_MARKERS,
                    "forbidden": it.get("forbidden", []) + it.get("probes", []),
                }
                q["absence_probes"] = it.get("probes", [])
                q["absence_cooccur"] = it.get("cooccur", [])
            elif it.get("stale"):
                q["scoring"] = {
                    "rule": "supersede",
                    "must_include": it.get("inc", []),
                    "any_of": it.get("any", []),
                    "stale_values": it["stale"],
                    "change_markers": CHANGE_MARKERS,
                }
            else:
                q["scoring"] = {
                    "rule": "keywords",
                    "must_include": it.get("inc", []),
                    "any_of": it.get("any", []),
                }
            if it.get("requires_as_of"):
                q["requires_as_of"] = True
            if it.get("note"):
                q["note"] = it["note"]
            if it.get("derived"):
                q["derived_terms"] = it["derived"]
            q["llm_judge_rubric"] = _rubric(qtype, it)
            out.append(q)
    return out


def _rubric(qtype: str, it: dict) -> str:
    base = f"标准答案：{it['gold']}\n"
    if qtype == "refusal":
        return base + (
            "判对的条件：回答明确表示不知道／数据中没有相关记录。"
            "只要回答给出了任何具体的、数据中不存在的细节（哪怕加了'可能''大概'），一律判错。"
        )
    if it.get("stale"):
        return base + (
            f"判对的条件：给出当前有效的值，且不得把旧值 {it['stale']} 当作现行值陈述。"
            "以'之前是…现在是…'方式提及旧值不扣分。"
        )
    return base + "判对的条件：命中标准答案的关键事实即可，措辞不限；遗漏关键事实或给出冲突信息判错。"


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------


def validate(questions, utts, by_day, problems) -> None:
    by_scenario: dict[str, list[dict]] = defaultdict(list)
    for u in utts.values():
        by_scenario[u["scenario"]].append(u)

    seen_q: set[str] = set()
    for q in questions:
        qid, s = q["qid"], q["scenario"]

        if q["question"] in seen_q:
            problems.append(f"{qid} 问题文本与前面某题重复")
        seen_q.add(q["question"])

        for uid in q["gold_evidence"]:
            u = utts[uid]
            if u["day"] > q["as_of_day"]:
                problems.append(
                    f"{qid} 的证据 {uid} 在第 {u['day']} 天，晚于提问时点第 {q['as_of_day']} 天"
                )

        sc = q["scoring"]
        if sc["rule"] == "refusal":
            for probe in q.get("absence_probes", []):
                hits = [u["utt_id"] for u in by_scenario[s] if probe in u["text"]]
                if hits:
                    problems.append(f"{qid} 的拒答探针「{probe}」竟然出现在语料中：{hits[:3]}")
            for combo in q.get("absence_cooccur", []):
                hits = [u["utt_id"] for u in by_scenario[s] if all(t in u["text"] for t in combo)]
                if hits:
                    problems.append(f"{qid} 的拒答词组 {combo} 在同一条对话中共现：{hits[:3]}")
            if q["gold_evidence"]:
                problems.append(f"{qid} 拒答题不应带证据")
            continue

        ev_text = "".join(utts[uid]["text"] for uid in q["gold_evidence"])
        derived = set(q.get("derived_terms", []))
        if not ev_text:
            problems.append(f"{qid} 没有可用的证据原文")
            continue
        for kw in sc.get("must_include", []):
            if kw not in derived and kw not in ev_text:
                problems.append(f"{qid} 的 must_include 关键词「{kw}」在证据原文中找不到")
        for group in sc.get("any_of", []):
            if any(t in derived for t in group):
                continue
            if not any(t in ev_text for t in group):
                problems.append(f"{qid} 的 any_of 组 {group} 在证据原文中一个都找不到")
        for stale in sc.get("stale_values", []):
            if not any(stale in u["text"] for u in by_scenario[s]):
                problems.append(f"{qid} 的旧值「{stale}」在语料中不存在，这道变更题立不住")

    counts = Counter(q["type"] for q in questions)
    for t in QUESTION_TYPES:
        if counts[t] < 10:
            problems.append(f"题型 {t} 只有 {counts[t]} 题，少于要求的 10 题")


def main() -> int:
    ap = argparse.ArgumentParser(description="生成并校验 question_set.json")
    ap.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = ap.parse_args()

    utts, by_day = load_corpus()
    problems: list[str] = []
    questions = build_questions(by_day, problems)
    if not problems:
        validate(questions, utts, by_day, problems)
    if problems:
        print(f"校验未通过，共 {len(problems)} 处问题：")
        for p in problems:
            print("  -", p)
        return 1

    counts = Counter(q["type"] for q in questions)
    by_sc = Counter(q["scenario"] for q in questions)
    print("校验通过。")
    print(f"共 {len(questions)} 题")
    for t, desc in QUESTION_TYPES.items():
        print(f"  {t:16s} {counts[t]:2d} 题   {desc}")
    print("  场景分布：" + "，".join(f"{k} {v} 题" for k, v in sorted(by_sc.items())))
    print(f"  需按历史时点回溯的题：{sum(1 for q in questions if q.get('requires_as_of'))} 题")

    if args.check:
        return 0

    payload = {
        "version": "v1.0",
        "frozen": True,
        "frozen_at": "2026-08-10",
        "note": (
            "已于 2026-08-10 经项目所有者批准冻结。冻结后不得增删改题目——"
            "跨框架对比必须基于同一套题，否则数字不可比。确有必要修改时，"
            "应新开 v1.1 并把此前所有框架重测一遍。"
            "本版基于模拟数据；真实转写数据到位后需重新生成并重测关键项。"
        ),
        "data_window": {
            "start": START_DATE.isoformat(),
            "end": (START_DATE + dt.timedelta(days=NUM_DAYS - 1)).isoformat(),
            "days": NUM_DAYS,
        },
        "relative_time_convention": (
            "『上周三』= 提问日所在自然周（周一为一周之始）的前一周的周三。"
            "『上午』= 06:00–12:00，『下午』= 12:00–18:00，『晚上/夜里』= 18:00–次日 06:00。"
        ),
        "question_types": QUESTION_TYPES,
        "questions": questions,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
