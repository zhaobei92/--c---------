from app.services.intake_service import heuristic_extract
from shared_schemas import DecisionDomain


def test_between_pattern_extracts_two_options():
    ex = heuristic_extract("我在雷鸟U8和三星S27B800之间纠结，主要用来办公。")
    names = [o.name for o in ex.options]
    assert names == ["雷鸟U8", "三星S27B800"]
    assert ex.clarification_required is False


def test_or_pattern_extracts_options():
    ex = heuristic_extract("到底是买MacBook还是ThinkPad，好难选。")
    names = [o.name for o in ex.options]
    assert "MacBook" in names[0]
    assert "ThinkPad" in names[1]


def test_no_options_requires_clarification():
    ex = heuristic_extract("最近好烦，不知道该怎么办。")
    assert ex.options == []
    assert ex.clarification_required is True


def test_budget_constraint_extracted_as_hard():
    ex = heuristic_extract("我在A和B之间纠结，预算不超过5000元。")
    assert any(c.is_hard for c in ex.constraints)


def test_domain_guess():
    assert heuristic_extract("买显示器，A和B之间选。").domain == DecisionDomain.PRODUCT
    assert (
        heuristic_extract("今天先做项目甲还是项目乙？").domain
        == DecisionDomain.WORK_PRIORITY
    )


def test_never_fabricates_facts():
    ex = heuristic_extract("我在A和B之间纠结。")
    assert ex.facts == []
    assert ex.concerns == []
