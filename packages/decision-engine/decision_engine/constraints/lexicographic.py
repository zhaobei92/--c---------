"""Lexicographic 非补偿规则（方案 6.4）。

按优先级（权重降序）逐个比较 lexicographic 标准：
差距超过容差 eps 即分出胜负；全部打平时退回 MAUT 效用比较。
"""

EPSILON = 0.05


def lexicographic_order(
    option_keys: list[str],
    lex_values: dict[str, list[float]],  # option_key -> 按优先级排列的标准值
    utilities: dict[str, float],
    eps: float = EPSILON,
) -> list[str]:
    """返回按 lexicographic 规则排序的 option_keys（降序）。"""

    def sort_key(key: str):
        return key  # 占位，实际用 cmp

    import functools

    def compare(a: str, b: str) -> int:
        for va, vb in zip(lex_values.get(a, []), lex_values.get(b, [])):
            if va - vb > eps:
                return -1  # a 在前
            if vb - va > eps:
                return 1
        ua, ub = utilities.get(a, 0.0), utilities.get(b, 0.0)
        if ua > ub:
            return -1
        if ub > ua:
            return 1
        return 0

    return sorted(option_keys, key=functools.cmp_to_key(compare))
