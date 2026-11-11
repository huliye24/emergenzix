import numpy as np

from emergenzix_api.brla_g2_ppo_env import INTENTS
from emergenzix_api.brla_g2_probe_curriculum import audit_curriculum, build_curriculum


def episode():
    returns = (-.02, -.01, .01, .01)
    rows = []
    for index, market_return in enumerate(returns):
        probabilities = ([0, 0, .8, .2, 0, 0] if index < 2
                         else [0, 0, 0, 0, .8, .2])
        encoder = np.asarray([*probabilities,
            .2, .8, .35, .7, .7, .2, .1], dtype=np.float32)
        rows.append({"encoder": encoder, "mark_price": 100 - index,
                     "atr": 2.0, "market_return": market_return, "cost_rate": .001})
    return {"path_sha256": "b" * 64, "rows": rows}


def test_curriculum_labels_are_legal_and_exclude_future_from_observation():
    examples = build_curriculum([episode()])
    assert [INTENTS[row.action] for row in examples] == [
        "PROBE_SHORT", "HOLD", "EXIT", "WAIT"]
    assert all(row.action_mask[row.action] for row in examples)
    assert all(row.future_fields_in_observation is False for row in examples)


def test_curriculum_audit_reports_probe_coverage_and_diversity():
    audit = audit_curriculum(build_curriculum([episode()]))
    assert audit["probe_episode_coverage"] == 1
    assert audit["action_diversity"] == 4
    assert audit["all_labels_mask_valid"] is True
    assert audit["position_permission"] is False


def test_future_up_move_does_not_directly_force_exit():
    path = episode()
    path["rows"][1]["market_return"] = .05
    examples = build_curriculum([path])
    assert INTENTS[examples[1].action] == "HOLD"


def test_nemt_mother_prior_can_label_a_brpm_feasible_decreasing_add():
    path = episode()
    path["rows"][0]["encoder"][6:8] = [.8, .2]
    path["rows"][1]["encoder"][6:8] = [.8, .2]
    path["rows"][1]["mark_price"] = 94
    examples = build_curriculum([path])
    assert INTENTS[examples[0].action] == "PROBE_SHORT"
    assert INTENTS[examples[1].action] == "INCREASE_SHORT"
    assert examples[1].nemt_prior_action == "DECREASING_ADD_CANDIDATE"
