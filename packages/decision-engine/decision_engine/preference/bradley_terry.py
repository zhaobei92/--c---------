"""Bradley–Terry 成对偏好学习（MLE，Zermelo 迭代）。

- 每次比较更新权重；
- "差不多"记为各半胜；"无法比较"不参与拟合；
- strength(0-1) 决定胜方得到的胜权重（0.5 + 0.5*strength）；
- 输出归一化权重、每项不确定度、以及回答冲突检测；
- 冲突多时不确定度升高，绝不强行输出伪高置信结果。
"""

from dataclasses import dataclass

# 平滑：每对项之间加极小的虚拟对局，避免全胜项发散
_SMOOTHING = 0.1
_MAX_ITER = 200
_TOL = 1e-8

Choice = str  # "left" | "right" | "equal" | "incomparable"


@dataclass(frozen=True)
class Comparison:
    left: int
    right: int
    choice: Choice
    strength: float = 0.5  # 0-1


@dataclass
class PreferenceFit:
    weights: list[float]  # 归一化，和为1
    uncertainty: list[float]  # 每项 0-1，比较次数越多越低
    comparisons_used: int
    conflict_pairs: int  # 同一对出现相反回答的对数
    consistency: float  # 1 - 冲突率


def _win_matrix(n_items: int, comparisons: list[Comparison]) -> tuple[list[list[float]], int]:
    wins = [[_SMOOTHING] * n_items for _ in range(n_items)]
    used = 0
    for c in comparisons:
        if c.choice == "incomparable" or c.left == c.right:
            continue
        if not (0 <= c.left < n_items and 0 <= c.right < n_items):
            continue
        used += 1
        strength = min(max(c.strength, 0.0), 1.0)
        if c.choice == "left":
            w = 0.5 + 0.5 * strength
            wins[c.left][c.right] += w
            wins[c.right][c.left] += 1.0 - w
        elif c.choice == "right":
            w = 0.5 + 0.5 * strength
            wins[c.right][c.left] += w
            wins[c.left][c.right] += 1.0 - w
        else:  # equal
            wins[c.left][c.right] += 0.5
            wins[c.right][c.left] += 0.5
    return wins, used


def _detect_conflicts(comparisons: list[Comparison]) -> tuple[int, int]:
    """同一无序对上出现方向相反的明确回答记为冲突。"""
    directions: dict[tuple[int, int], set[str]] = {}
    for c in comparisons:
        if c.choice not in ("left", "right"):
            continue
        key = (min(c.left, c.right), max(c.left, c.right))
        winner = "lo" if (
            (c.choice == "left" and c.left < c.right)
            or (c.choice == "right" and c.right < c.left)
        ) else "hi"
        directions.setdefault(key, set()).add(winner)
    pairs_with_answers = len(directions)
    conflicts = sum(1 for winners in directions.values() if len(winners) > 1)
    return conflicts, pairs_with_answers


def fit_preferences(n_items: int, comparisons: list[Comparison]) -> PreferenceFit:
    if n_items < 1:
        raise ValueError("n_items must be >= 1")

    wins, used = _win_matrix(n_items, comparisons)
    p = [1.0] * n_items

    for _ in range(_MAX_ITER):
        new_p = []
        for i in range(n_items):
            total_wins = sum(wins[i][j] for j in range(n_items) if j != i)
            denom = sum(
                (wins[i][j] + wins[j][i]) / (p[i] + p[j])
                for j in range(n_items)
                if j != i
            )
            new_p.append(total_wins / denom if denom > 0 else p[i])
        norm = sum(new_p)
        new_p = [x / norm for x in new_p]
        delta = max(abs(a - b) for a, b in zip(new_p, p))
        p = new_p
        if delta < _TOL:
            break

    weights = [x / sum(p) for x in p]

    # 不确定度：该项参与的有效比较次数越多越低；冲突整体抬高不确定度
    conflicts, answered_pairs = _detect_conflicts(comparisons)
    consistency = 1.0 - (conflicts / answered_pairs if answered_pairs else 0.0)
    counts = [0] * n_items
    for c in comparisons:
        if c.choice == "incomparable":
            continue
        if 0 <= c.left < n_items and 0 <= c.right < n_items and c.left != c.right:
            counts[c.left] += 1
            counts[c.right] += 1
    uncertainty = [
        min(1.0, (1.0 / (1.0 + count) ** 0.5) + (1.0 - consistency) * 0.5)
        for count in counts
    ]

    return PreferenceFit(
        weights=weights,
        uncertainty=uncertainty,
        comparisons_used=used,
        conflict_pairs=conflicts,
        consistency=consistency,
    )
