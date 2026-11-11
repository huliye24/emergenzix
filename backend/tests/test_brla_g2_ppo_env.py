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
    _, _, _, _, info = env.step(INTENTS.index("INCREASE_SHORT"))
    assert info["approved_target"] == -.10
    assert info["brpm_decision"] == "APPROVE_PROBE"
    assert info["brgc_decision"] == "ALLOW_PAPER_TARGET"
    assert info["order_permission"] is False


def test_late_entry_is_rejected_and_position_stays_flat():
    row = episode(); row["rows"][0]["encoder"][8] = .85
    env = BRLAG2PPOEnv([row]); env.reset(seed=1)
    _, _, _, _, info = env.step(INTENTS.index("PROBE_SHORT"))
    assert info["brpm_decision"] == "REJECT"
    assert info["approved_target"] == 0


def test_same_seed_and_intents_are_reproducible():
    def run():
        env = BRLAG2PPOEnv([episode()]); env.reset(seed=2)
        for action in (1, 2, 3, 4, 5): env.step(action)
        return env.trajectory, env.equity
    assert run() == run()
