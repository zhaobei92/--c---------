from decision_engine.question_selection.value import (
    QuestionCandidate,
    question_value,
    select_best_question,
)


def c(target, impact, uncertainty, burden=0.2):
    return QuestionCandidate(target, impact, uncertainty, burden)


def test_value_formula():
    cand = c("budget", impact=0.8, uncertainty=0.5, burden=0.5)
    assert abs(question_value(cand) - (0.8 * 0.5 - 0.2 * 0.5)) < 1e-9


def test_selects_highest_value():
    best = select_best_question([c("a", 0.9, 0.9), c("b", 0.3, 0.3)])
    assert best is not None and best.target_variable == "a"


def test_skips_already_asked():
    best = select_best_question([c("a", 0.9, 0.9), c("b", 0.8, 0.8)], asked_targets={"a"})
    assert best is not None and best.target_variable == "b"


def test_returns_none_below_threshold():
    assert select_best_question([c("a", 0.2, 0.2)]) is None


def test_returns_none_when_all_asked():
    assert select_best_question([c("a", 0.9, 0.9)], asked_targets={"a"}) is None
