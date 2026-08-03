"""确定性风险分级（安全底线，不依赖 LLM）。

关键词命中即分级，宁可误报也不漏报；LLM 精细化分级在后续阶段
只允许把等级调高，永远不允许把确定性规则命中的等级调低。
"""

from shared_schemas import RiskLevel

# 受限制类别：只允许引导性梳理，并提示寻求专业帮助
_RESTRICTED_KEYWORDS = [
    "自杀", "自残", "自伤", "不想活", "轻生", "伤害自己", "伤害他人",
    "报复他", "杀了", "违法", "犯罪", "吸毒", "家暴", "殴打",
]

# 高风险普通决策：只梳理，不直接拍板
_HIGH_RISK_KEYWORDS = [
    "离婚", "辞职", "裸辞", "辞掉", "停药", "手术", "化疗", "诉讼",
    "起诉", "打官司", "全部积蓄", "全仓", "梭哈", "抵押", "借贷",
    "贷款投", "炒股", "期货", "加杠杆", "移民",
]

_MEDIUM_RISK_KEYWORDS = [
    "分手", "换工作", "跳槽", "买房", "卖房", "创业",
]


def triage_text(text: str) -> tuple[RiskLevel, list[str]]:
    """返回风险等级与命中的关键词（写入审计日志）。"""
    hits = [kw for kw in _RESTRICTED_KEYWORDS if kw in text]
    if hits:
        return RiskLevel.RESTRICTED, hits
    hits = [kw for kw in _HIGH_RISK_KEYWORDS if kw in text]
    if hits:
        return RiskLevel.HIGH, hits
    hits = [kw for kw in _MEDIUM_RISK_KEYWORDS if kw in text]
    if hits:
        return RiskLevel.MEDIUM, hits
    return RiskLevel.LOW, []
