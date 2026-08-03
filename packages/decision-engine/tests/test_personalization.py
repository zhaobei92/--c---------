from decision_engine.personalization.update import (
    Posterior,
    applicable,
    blend_with_default,
    update_posterior,
)


def test_first_observation_is_anchored_not_copied():
    p = update_posterior(None, observed_weight=1.0)
    # 单次观测不能永久写入极端值
    assert p.mean < 0.75
    assert p.evidence_count == 1
    assert not applicable(p)


def test_repeated_observations_converge():
    p = None
    for _ in range(10):
        p = update_posterior(p, observed_weight=0.8)
    assert p.evidence_count == 10
    assert applicable(p)
    assert 0.6 < p.mean < 0.8  # 向观测收敛但仍有先验锚定
    assert p.std < 0.25  # 不确定度收缩


def test_std_has_floor():
    p = None
    for _ in range(50):
        p = update_posterior(p, observed_weight=0.5)
    assert p.std >= 0.08


def test_single_evidence_does_not_affect_new_decisions():
    p = update_posterior(None, observed_weight=0.9)
    assert blend_with_default(p, default_weight=0.25) == 0.25


def test_accumulated_evidence_shifts_default():
    p = None
    for _ in range(4):
        p = update_posterior(p, observed_weight=0.9)
    blended = blend_with_default(p, default_weight=0.25)
    assert blended > 0.25
    assert blended < p.mean + 0.01  # 不会超过后验本身
