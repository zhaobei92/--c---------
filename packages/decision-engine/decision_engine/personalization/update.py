"""偏好后验更新（方案 6.11）。

原则：偏好只能从多次结果中逐渐确认——
- 均值向观测值渐进移动（带伪先验锚定，单次观测不能大幅改写）；
- 不确定度随证据增加收缩，但有下限；
- 应用侧只使用 evidence_count >= MIN_EVIDENCE_TO_APPLY 的后验。
"""

from dataclasses import dataclass

# 伪先验观测数：锚定均值，防止单次观测覆盖
PSEUDO_PRIOR_COUNT = 2
MIN_STD = 0.08
INITIAL_STD = 0.25
# 少于这么多次独立证据的后验不得用于影响新决策
MIN_EVIDENCE_TO_APPLY = 2


@dataclass(frozen=True)
class Posterior:
    mean: float
    std: float
    evidence_count: int


def update_posterior(prior: Posterior | None, observed_weight: float) -> Posterior:
    """用一次新观测更新后验。prior 为 None 时创建初始后验。"""
    observed = min(1.0, max(0.0, observed_weight))
    if prior is None:
        # 初始：观测值与全局中性先验各占权重（伪先验锚定）
        mean = (0.5 * PSEUDO_PRIOR_COUNT + observed) / (PSEUDO_PRIOR_COUNT + 1)
        return Posterior(mean=round(mean, 4), std=INITIAL_STD, evidence_count=1)

    effective_n = prior.evidence_count + PSEUDO_PRIOR_COUNT
    mean = (prior.mean * effective_n + observed) / (effective_n + 1)
    std = max(MIN_STD, prior.std * (effective_n / (effective_n + 1)) ** 0.5)
    return Posterior(
        mean=round(mean, 4),
        std=round(std, 4),
        evidence_count=prior.evidence_count + 1,
    )


def applicable(posterior: Posterior) -> bool:
    return posterior.evidence_count >= MIN_EVIDENCE_TO_APPLY


def blend_with_default(posterior: Posterior, default_weight: float) -> float:
    """把后验与默认权重融合（后验证据越多占比越高，上限0.7）。"""
    if not applicable(posterior):
        return default_weight
    alpha = min(0.7, 0.3 + 0.1 * posterior.evidence_count)
    return round(alpha * posterior.mean + (1 - alpha) * default_weight, 4)
