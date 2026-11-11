import numpy as np
import pytest

pytest.importorskip("gymnasium")
from emergenzix_api.brla_g2_ppo_env import BRLAG2PPOEnv, INTENTS


def episode():
    rows = []
    for index in range(12):
        encoder = np.asarray([0, 0, 0, 0, .8, .2,
            .2, .8, .35, .7, .7, .2, .1], dtype=np.float32)
        rows.append({"encoder": encoder, "mark_price": 100 - index,
                     "market_return": -.01, "cost_rate": .001})
    return {"path_sha256": "a" * 64, "rows": rows}


def test_ppo_intent_cannot_bypass_brpm_or_brgc():
    env = BRLAG2PPOEnv([episode()]); env.reset(seed=1)
    _, _, _, _, info = env.step(INTENTS.index("PROBE_SHORT"))
    assert info["approved_target"] == -.10
    assert info["brpm_decision"] == "APPROVE_PROBE"
    assert info["brgc_decision"] == "ALLOW_PAPER_TARGET"
    assert info["order_permission"] is False


def test_late_entry_is_rejected_and_position_stays_flat():
    row = episode(); row["rows"][0]["encoder"][8] = .85
    env = BRLAG2PPOEnv([row]); env.reset(seed=1)
    assert env.action_masks()[INTENTS.index("PROBE_SHORT")] == 0
    with pytest.raises(PermissionError, match="masked"):
        env.step(INTENTS.index("PROBE_SHORT"))


def test_same_seed_and_intents_are_reproducible():
    def run():
        env = BRLAG2PPOEnv([episode()]); env.reset(seed=2)
        for action in (1, 2, 3, 4, 5): env.step(action)
        return env.trajectory, env.equity
    assert run() == run()


def test_action_masks_remove_flat_and_position_aliases():
    env = BRLAG2PPOEnv([episode()]); env.reset(seed=3)
    assert env.action_masks().tolist() == [True, True, False, False, False, False]
    with pytest.raises(PermissionError, match="masked"):
        env.step(INTENTS.index("HOLD"))
    env.step(INTENTS.index("PROBE_SHORT"))
    assert env.action_masks().tolist() == [False, False, True, True, False, True]


def test_confirmed_missed_down_move_penalizes_staying_flat():
    env = BRLAG2PPOEnv([episode()]); env.reset(seed=4)
    _, reward, _, _, _ = env.step(INTENTS.index("WAIT"))
    assert env.trajectory[-1]["missed_opportunity"] < 0
    assert reward < 0


def test_early_order_stage_can_still_be_a_confirmed_short_opportunity():
    row = episode(); row["rows"][0]["encoder"][:6] = [1, 0, 0, 0, 0, 0]
    env = BRLAG2PPOEnv([row]); env.reset(seed=6)
    env.step(INTENTS.index("WAIT"))
    audit = env.trajectory[-1]
    assert audit["counterfactual_probe_return"] > 0
    assert audit["opportunity_quality"] > 0
    assert audit["missed_opportunity"] < 0


def test_wait_is_not_penalized_when_counterfactual_probe_would_lose():
    row = episode(); row["rows"][0]["market_return"] = .01
    env = BRLAG2PPOEnv([row]); env.reset(seed=7)
    env.step(INTENTS.index("WAIT"))
    assert env.trajectory[-1]["counterfactual_probe_return"] == 0
    assert env.trajectory[-1]["missed_opportunity"] == 0


def test_brpm_infeasible_probe_is_masked_before_policy_sampling():
    row = episode(); row["rows"][0]["encoder"][8] = .85
    env = BRLAG2PPOEnv([row]); env.reset(seed=5)
    assert env.action_masks().tolist() == [True, False, False, False, False, False]
