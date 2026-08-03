from decision_engine.reopen.gate import ReopenSignals, evaluate_reopen
from decision_engine.reopen.novelty import is_repetitive, novelty, similarity


def test_reopen_score_formula():
    d = evaluate_reopen(ReopenSignals(1.0, 1.0, 1.0, 1.0, 1.0))
    assert d.score == 1.0
    assert d.outcome == "full_reopen"


def test_strong_new_fact_allows_full_reopen():
    # 卖家承认设备拆修过：高可信、高相关、高翻转概率
    d = evaluate_reopen(ReopenSignals(0.9, 0.8, 0.9, 0.8, 0.7))
    assert d.score >= 0.70
    assert d.outcome == "full_reopen"


def test_moderate_info_gets_partial_analysis():
    d = evaluate_reopen(ReopenSignals(0.6, 0.5, 0.6, 0.4, 0.5))
    assert 0.45 <= d.score < 0.70
    assert d.outcome == "new_facts_only"


def test_no_new_facts_rejected():
    # 又刷到别人的好评：低新颖、低可信、低翻转
    d = evaluate_reopen(ReopenSignals(0.1, 0.2, 0.3, 0.1, 0.1))
    assert d.score < 0.45
    assert d.outcome == "rejected"


def test_signals_clipped_to_unit_range():
    d = evaluate_reopen(ReopenSignals(5.0, -1.0, 0.5, 0.5, 0.5))
    assert d.signals.novelty == 1.0
    assert d.signals.credibility == 0.0


def test_similarity_and_novelty():
    assert similarity("雷鸟U8看电影效果好", "雷鸟U8看电影效果好") == 1.0
    assert similarity("雷鸟U8看电影效果好", "完全无关的另一句话") < 0.2
    assert novelty("全新的信息", []) == 1.0


def test_repetitive_complaint_detected():
    history = ["我又开始后悔了，是不是选错了", "总觉得选错了很后悔"]
    assert is_repetitive("我又后悔了，感觉选错了", history)
    assert not is_repetitive("卖家刚承认这台设备之前拆修过", history)
