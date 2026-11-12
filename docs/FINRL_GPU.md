# FinRL GPU Research Runtime

FinRL is deployed as an isolated reinforcement-learning research engine on the AWS GPU node. It is not an exchange execution service and contains no credentials or automatic order permission.

## Pinned components

- FinRL source: `7e98df461cbc7772f16a545a2f13b521e5d0b8f4` (MIT)
- PyTorch base: `2.5.1+cu124`, image digest `sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755`
- Stable-Baselines3 / sb3-contrib: `2.7.1`
- Runtime image: `emergenzix/finrl:7e98df4`

The host tags the verified PyTorch digest as `emergenzix/pytorch-base:c8268a9` before building. This avoids an unnecessary Docker Hub metadata lookup; the Dockerfile also records the authoritative base digest as an OCI label.

## Safety boundary

Validation uses `--network none`, a read-only root filesystem, bounded CPU/RAM/PID resources and synthetic public data. FinRL's top-level package eagerly imports broker adapters, so the offline validation loads only the environment namespace and does not install or initialize Alpaca or another broker SDK.

The smoke test proves that the FinRL environment exposes a continuous `[-1, 1]` action and that PPO can complete a short CUDA learning run. It does not prove profitability or implement Emergenzix's richer target-position, stop-loss and trailing-stop action contract.

## BRLA decision trainer

`ops/train_brla_g1_sb3.py` is the BRLA training entry point. It uses the pinned
Stable-Baselines3 PPO implementation rather than the earlier handwritten PPO
loop. `BRLASB3Env` preserves the four discrete target proposals, 0.15% position
change cost and frozen reflexive-risk reward. Every proposal passes through the
constitution and BRGC; the model never receives order permission.

Run it inside this isolated image with the repository and immutable path batch
mounted read-only. The trainer accepts only a verified BRMG scenario bundle and
its frozen partition manifest. It learns from `TRAIN`, reports `DEVELOPMENT`,
and has no CLI argument capable of unlocking `SEALED_TEST`. Use a new output
directory for every run. The earlier PPO v2 holdout must not be reused.

## Concurrency

The A10G has about 23 GiB VRAM. Start with two experiments and measure peak host RAM and VRAM before starting a third. Small MLP PPO workloads may be faster on CPU; GPU is more useful for larger networks, multiple environments, SAC/TD3 replay batches, or parallel experiments. Each real run must use a unique run ID and be registered in the persistent research-memory ledger.
