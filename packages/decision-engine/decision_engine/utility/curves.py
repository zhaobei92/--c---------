"""效用曲线：把标准上的原始表现 x∈[0,1] 映射为真实效用 u∈[0,1]。

direction=lower_better 的标准应在调用前先做 x -> 1-x 翻转。
"""

import math
from collections.abc import Callable

CurveFn = Callable[[float], float]


def _clip(x: float) -> float:
    return min(1.0, max(0.0, x))


def linear(x: float) -> float:
    return _clip(x)


def diminishing(x: float) -> float:
    """边际效用递减：前段增益大，后段趋平。"""
    x = _clip(x)
    return (1 - math.exp(-3 * x)) / (1 - math.exp(-3))


def threshold(x: float, cutoff: float = 0.4) -> float:
    """阈值型：低于 cutoff 几乎无效用，之上线性。"""
    x = _clip(x)
    if x < cutoff:
        return 0.05 * (x / cutoff) if cutoff > 0 else 0.0
    return 0.05 + 0.95 * (x - cutoff) / (1 - cutoff)


def s_curve(x: float, midpoint: float = 0.5, steepness: float = 8.0) -> float:
    """S型：中段敏感，两端不敏感。归一化到 [0,1]。"""
    x = _clip(x)
    raw = 1 / (1 + math.exp(-steepness * (x - midpoint)))
    low = 1 / (1 + math.exp(steepness * midpoint))
    high = 1 / (1 + math.exp(-steepness * (1 - midpoint)))
    return (raw - low) / (high - low)


def loss_averse(x: float, reference: float = 0.5, loss_factor: float = 2.25) -> float:
    """损失厌恶：低于参照点的损失被放大（Kahneman-Tversky λ≈2.25）。"""
    x = _clip(x)
    if x >= reference:
        gain = (x - reference) / (1 - reference) if reference < 1 else 0.0
        return 0.5 + 0.5 * gain
    loss = (reference - x) / reference if reference > 0 else 0.0
    return _clip(0.5 - 0.5 * loss_factor * loss)


def categorical(x: float, levels: int = 5) -> float:
    """离散等级型：把连续值量化到 levels 档。"""
    x = _clip(x)
    if levels < 2:
        return x
    step = round(x * (levels - 1))
    return step / (levels - 1)


CURVES: dict[str, CurveFn] = {
    "linear": linear,
    "diminishing": diminishing,
    "threshold": threshold,
    "s_curve": s_curve,
    "loss_averse": loss_averse,
    "categorical": categorical,
}


def apply_curve(curve_type: str, x: float) -> float:
    fn = CURVES.get(curve_type, linear)
    return fn(x)
