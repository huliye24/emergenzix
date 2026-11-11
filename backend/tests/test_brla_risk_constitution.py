from emergenzix_api.brla_risk_constitution import approve_target, constitutional_reward


def test_constitution_requires_small_probe_and_gradual_build():
    assert approve_target(-.50, current=0, drawdown=0, previous_step_return=0) == (
        -.10, "INITIAL_PROBE_CAP")
    assert approve_target(-.50, current=-.10, drawdown=0, previous_step_return=.01) == (
        -.20, "GRADUAL_BUILD_CAP")


def test_constitution_blocks_loss_add_and_fails_closed():
    assert approve_target(-.25, current=-.10, drawdown=0, previous_step_return=-.01) == (
        -.10, "LOSS_ADD_BLOCKED")
    assert approve_target(float("nan"), current=0, drawdown=0, previous_step_return=0) == (
        0.0, "INVALID_STATE_FLAT")


def test_reward_penalizes_exposure_building_drawdown_and_loss_add():
    reward, parts = constitutional_reward(step_return=.01, drawdown_increase=.02,
        current=-.10, approved=-.20, loss_add_attempted=True)
    assert set(parts) == {"net_return", "drawdown", "exposure", "build", "loss_add"}
    assert reward < -.03
