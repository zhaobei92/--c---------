import pytest

from decision_engine.utility.curves import CURVES, apply_curve


@pytest.mark.parametrize("curve", list(CURVES))
def test_all_curves_bounded_and_monotone_at_ends(curve):
    assert 0.0 <= apply_curve(curve, 0.0) <= 0.06
    assert 0.94 <= apply_curve(curve, 1.0) <= 1.0
    # 单调不减（在采样点上）
    xs = [i / 20 for i in range(21)]
    ys = [apply_curve(curve, x) for x in xs]
    assert all(b >= a - 1e-9 for a, b in zip(ys, ys[1:]))


def test_diminishing_front_loaded():
    assert apply_curve("diminishing", 0.3) > 0.3


def test_threshold_flat_below_cutoff():
    assert apply_curve("threshold", 0.2) < 0.05
    assert apply_curve("threshold", 0.9) > 0.8


def test_loss_averse_penalizes_losses_more():
    gain = apply_curve("loss_averse", 0.7) - 0.5
    loss = 0.5 - apply_curve("loss_averse", 0.3)
    assert loss > gain


def test_unknown_curve_falls_back_to_linear():
    assert apply_curve("nope", 0.42) == pytest.approx(0.42)
