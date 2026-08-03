from app.services.risk_triage import triage_text
from shared_schemas import RiskLevel


def test_low_risk_product_decision():
    level, hits = triage_text("我在两台显示器之间纠结，预算5000元。")
    assert level == RiskLevel.LOW
    assert hits == []


def test_high_risk_keywords():
    level, hits = triage_text("我在考虑要不要辞职去创业。")
    assert level == RiskLevel.HIGH
    assert "辞职" in hits


def test_restricted_overrides_high():
    level, _ = triage_text("我不想活了，也在纠结要不要辞职。")
    assert level == RiskLevel.RESTRICTED


def test_medium_risk():
    level, hits = triage_text("纠结要不要跳槽到另一家公司。")
    assert level == RiskLevel.MEDIUM
    assert "跳槽" in hits
