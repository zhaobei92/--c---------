"""MAUT 效用计算 + 蒙特卡洛稳定性模拟（numpy，固定种子可复现）。"""

import numpy as np

from decision_engine.types import CriterionSpec, EvaluationSpec, OptionSpec
from decision_engine.utility.curves import apply_curve

DEFAULT_ITERATIONS = 1000


def _matrices(
    options: list[OptionSpec],
    criteria: list[CriterionSpec],
    evaluations: list[EvaluationSpec],
) -> tuple[np.ndarray, np.ndarray]:
    """返回 (期望值矩阵, 不确定度矩阵)，形状 (n_options, n_criteria)。

    缺失评估按中性 0.5、高不确定 0.3 处理。lower_better 已翻转。
    """
    eval_map = {(e.option_key, e.criterion_key): e for e in evaluations}
    means = np.full((len(options), len(criteria)), 0.5)
    stds = np.full((len(options), len(criteria)), 0.3)
    for i, option in enumerate(options):
        for j, criterion in enumerate(criteria):
            ev = eval_map.get((option.key, criterion.key))
            if ev is None:
                continue
            value = ev.expected_value
            if criterion.direction == "lower_better":
                value = 1.0 - value
            means[i, j] = np.clip(value, 0.0, 1.0)
            stds[i, j] = np.clip(ev.uncertainty, 0.01, 0.5)
    return means, stds


def _apply_curves(criteria: list[CriterionSpec], raw: np.ndarray) -> np.ndarray:
    out = np.empty_like(raw)
    for j, criterion in enumerate(criteria):
        col = raw[..., j]
        flat = np.clip(col, 0.0, 1.0).ravel()
        out[..., j] = np.array(
            [apply_curve(criterion.curve_type, float(x)) for x in flat]
        ).reshape(col.shape)
    return out


def deterministic_utilities(
    options: list[OptionSpec],
    criteria: list[CriterionSpec],
    evaluations: list[EvaluationSpec],
) -> dict[str, float]:
    """U(a) = Σ w_k · u_k(x_ak)，权重归一化。"""
    if not options or not criteria:
        return {o.key: 0.0 for o in options}
    means, _ = _matrices(options, criteria, evaluations)
    utils = _apply_curves(criteria, means)
    weights = np.array([max(c.weight, 0.0) for c in criteria])
    weights = weights / weights.sum() if weights.sum() > 0 else np.full(len(criteria), 1 / len(criteria))
    scores = utils @ weights
    return {o.key: float(s) for o, s in zip(options, scores)}


def simulate(
    options: list[OptionSpec],
    criteria: list[CriterionSpec],
    evaluations: list[EvaluationSpec],
    n_iterations: int = DEFAULT_ITERATIONS,
    seed: int = 42,
) -> dict:
    """采样权重与表现分布，输出胜率、稳定性、期望效用差、关键变量、后悔值。"""
    rng = np.random.default_rng(seed)
    n_opt, n_crit = len(options), len(criteria)
    if n_opt == 0 or n_crit == 0:
        return {
            "winner_probability": {},
            "ranking_stability": 0.0,
            "expected_utility_gap": 0.0,
            "critical_variables": [],
            "max_regret": {},
            "minimax_regret_option": None,
        }

    means, stds = _matrices(options, criteria, evaluations)
    w_mean = np.array([max(c.weight, 1e-6) for c in criteria])
    w_mean = w_mean / w_mean.sum()
    w_std = np.array([np.clip(c.weight_uncertainty, 0.01, 0.5) for c in criteria])

    # 权重采样：截断正态 + 归一化
    w_samples = np.clip(
        rng.normal(w_mean, w_std * w_mean + 1e-6, size=(n_iterations, n_crit)),
        1e-6,
        None,
    )
    w_samples = w_samples / w_samples.sum(axis=1, keepdims=True)

    # 表现采样：截断正态到 [0,1]，再过效用曲线
    s_samples = np.clip(
        rng.normal(means, stds, size=(n_iterations, n_opt, n_crit)), 0.0, 1.0
    )
    u_samples = _apply_curves(criteria, s_samples)

    utilities = np.einsum("iok,ik->io", u_samples, w_samples)  # (iter, option)
    winners = utilities.argmax(axis=1)

    counts = np.bincount(winners, minlength=n_opt)
    winner_probability = {
        options[i].key: float(counts[i]) / n_iterations for i in range(n_opt)
    }
    top = int(counts.argmax())
    ranking_stability = float(counts[top]) / n_iterations

    mean_utils = utilities.mean(axis=0)
    order = np.argsort(-mean_utils)
    expected_utility_gap = (
        float(mean_utils[order[0]] - mean_utils[order[1]]) if n_opt > 1 else 1.0
    )

    # 关键变量：与"最优方案获胜"指示变量相关性最高的权重/表现变量
    indicator = (winners == top).astype(float)
    critical: list[tuple[float, str]] = []
    if indicator.std() > 1e-9:
        with np.errstate(invalid="ignore", divide="ignore"):
            for j in range(n_crit):
                corr = np.corrcoef(w_samples[:, j], indicator)[0, 1]
                if np.isfinite(corr):
                    critical.append((abs(float(corr)), f"weight:{criteria[j].name}"))
            for i in range(n_opt):
                for j in range(n_crit):
                    if stds[i, j] < 0.02:
                        continue
                    corr = np.corrcoef(s_samples[:, i, j], indicator)[0, 1]
                    if np.isfinite(corr):
                        critical.append(
                            (abs(float(corr)), f"score:{options[i].name}:{criteria[j].name}")
                        )
    critical.sort(reverse=True)
    critical_variables = [name for _, name in critical[:3]]

    # 最小最大后悔：每个场景下与最优的效用差，取最坏场景
    regret = utilities.max(axis=1, keepdims=True) - utilities
    max_regret = {
        options[i].key: float(regret[:, i].max()) for i in range(n_opt)
    }
    minimax_idx = int(np.argmin([max_regret[o.key] for o in options]))

    return {
        "winner_probability": winner_probability,
        "ranking_stability": ranking_stability,
        "expected_utility_gap": expected_utility_gap,
        "critical_variables": critical_variables,
        "max_regret": max_regret,
        "minimax_regret_option": options[minimax_idx].key,
    }
