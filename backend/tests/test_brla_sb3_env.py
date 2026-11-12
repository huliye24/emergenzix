import pytest
import numpy as np
import hashlib
import json

gym = pytest.importorskip("gymnasium")

from emergenzix_api.brla_sb3_env import BRLASB3Env


def path():
    bars = []
    price = 100.0
    for index in range(40):
        open_price = price
        price *= 1 + (-.002 if index % 5 else .001)
        bars.append({"timestamp": f"2026-01-{index // 24 + 1:02d}T{index % 24:02d}:00:00Z",
            "open": open_price, "high": max(open_price, price) * 1.001,
            "low": min(open_price, price) * .999, "close": price,
            "base_volume": 1000.0 + index * 3})
    fingerprint = hashlib.sha256(json.dumps(bars, sort_keys=True).encode()).hexdigest()
    return {"synthetic": True, "bars": bars, "path_sha256": fingerprint}


def rollout(seed, actions):
    env = BRLASB3Env([path()]); env.reset(seed=seed)
    infos = []
    for action in actions:
        _, _, terminated, _, info = env.step(action); infos.append(info)
        if terminated:
            break
    return env.trajectory_sha256(), infos


def test_same_seed_and_actions_reproduce_identical_guarded_trajectory():
    first, first_infos = rollout(11, [3, 3, 0, 2, 1])
    second, second_infos = rollout(11, [3, 3, 0, 2, 1])
    assert first == second
    assert first_infos == second_infos


def test_model_proposal_cannot_bypass_constitution_or_brgc():
    _, infos = rollout(12, [3, 3])
    assert infos[0]["proposed_target"] == -.50
    assert infos[0]["approved_target"] == -.10
    assert all(info["guard_decision"] == "ALLOW_PAPER_TARGET" for info in infos)
    assert all(info["mode"] == "PAPER" and info["order_permission"] is False
               and info["automatic_promotion"] is False for info in infos)


def test_pinned_sb3_ppo_is_reproducible_for_same_seed():
    from stable_baselines3 import PPO

    def train():
        model = PPO("MlpPolicy", BRLASB3Env([path()]), seed=23, device="cpu",
            n_steps=32, batch_size=32, n_epochs=1, gamma=.995, gae_lambda=.95,
            policy_kwargs={"net_arch": [64, 64]}, verbose=0)
        model.learn(total_timesteps=64)
        observation, _ = BRLASB3Env([path()]).reset(seed=23)
        action, _ = model.predict(observation, deterministic=True)
        return int(action), [value.detach().cpu().numpy().copy()
            for _, value in sorted(model.policy.state_dict().items())]

    first_action, first_weights = train()
    second_action, second_weights = train()
    assert first_action == second_action
    assert all(np.array_equal(first, second)
               for first, second in zip(first_weights, second_weights, strict=True))
