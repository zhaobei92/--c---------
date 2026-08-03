"""语义新颖度的确定性近似（方案 6.9）。

V1 用字符 n-gram 重叠系数近似语义相似度（对中文友好、零依赖）：
similarity = max(overlap(bigrams), 0.8 * overlap(unigrams))
其中 overlap(A,B) = |A∩B| / min(|A|,|B|)。

Novelty = 1 - max Similarity(m_t, m_{1:t-1})

注意：方案中的 0.15 阈值针对 embedding 余弦相似度；此近似算法的
重复判定阈值标定为 0.4，V2 接入向量模型后换回 0.15。
"""


def _ngrams(text: str, n: int) -> set[str]:
    cleaned = "".join(ch for ch in text if not ch.isspace() and ch not in "，。！？,.!?、")
    if len(cleaned) < n:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def similarity(a: str, b: str) -> float:
    """字符级相似度近似，0-1。"""
    bigram = _overlap(_ngrams(a, 2), _ngrams(b, 2))
    unigram = _overlap(_ngrams(a, 1), _ngrams(b, 1))
    return max(bigram, 0.8 * unigram)


def novelty(new_text: str, previous_texts: list[str]) -> float:
    """相对历史文本的新颖度：1 - 最大相似度。无历史时为 1。"""
    if not previous_texts:
        return 1.0
    max_sim = max(similarity(new_text, prev) for prev in previous_texts)
    return round(max(0.0, 1.0 - max_sim), 4)


# 针对 n-gram 近似标定的重复判定阈值（embedding 版本应使用 0.15）
LOW_NOVELTY_THRESHOLD = 0.5


def is_repetitive(new_text: str, previous_texts: list[str]) -> bool:
    return novelty(new_text, previous_texts) < LOW_NOVELTY_THRESHOLD
