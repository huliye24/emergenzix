import numpy as np

from emergenzix_api.brla_g2_ppo_env import INTENTS
from emergenzix_api.brla_g2_probe_curriculum import audit_curriculum, build_curriculum


def episode():
    returns = (-.02, -.01, .01, .01)
    rows = []
    for index, market_return in enumerate(returns):
        encoder = np.asarray([0, 0, 0, 0, .8, .2,
            .2, .8, .35, .7, .7, .2, .1], dtype=np.float32)
        rows.append({"encoder": encoder, "mark_price": 100 - index,
                     "market_return": market_return, "cost_rate": .001})
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
