# GPU Walk-forward: one-fold trainer (research-only)

**Status:** code written in the stacked PR following #60; **not run on an
A10/A10G and not independently accepted**. The work belongs to the GPU Alpha
Lab only. Nothing here connects to PAPER, TESTNET or LIVE order execution.

## Purpose and ordering

1. Register and verify an immutable, contiguous 1h/4h/1d OHLCV dataset.
2. Freeze the expanding-history plan with `plan_walk_forward` from #59.
3. Materialize a single fold using `make_fold_samples` from #60.
4. **This PR:** fit one **fresh** MLP or causal TCN model on that fold's
   training samples; choose its checkpoint by lowest *validation* MSE.
5. Evaluate that fold's holdout exactly once, after checkpoint selection,
   reporting error metrics and a zero-return prediction baseline.
6. Write files under a new, never-overwritten output directory, with
   `sha256.json` written LAST as the completion marker.

A positive holdout result is neither proof of alpha nor permission to trade.
The next task must separately verify and compare saved fold artifacts.

## Local CPU research command (only when testing is authorized)

From `backend/`, with the backend dependencies and PyTorch installed:

```bash
python -m emergenzix_gpu_lab.fold_train \
  --db-path /path/to/emergenzix.db \
  --dataset-id DATASET_SHA256 \
  --folds 3 --fold 1 \
  --train-bars 512 --validation-bars 128 --test-bars 128 \
  --lookback 32 --horizon 1 \
  --model mlp --device cpu --seed 42 \
  --epochs 2 --batch-size 256 \
  --output-dir /tmp/emergenzix-fold01-mlp-s42
```

The `--fold` argument is REQUIRED. Fold indexes are one-based and must
belong to the frozen plan. `--device` defaults to `cuda` in the training
CLI; when CUDA is explicitly requested but unavailable, training fails,
never silently changing hardware. CPU must be explicitly requested.
`output-dir` must not exist. Choose a distinct directory for each
model/fold/seed.

## Optional A10 execution (AFTER the operator starts the GPU host)

From the project root after confirming the private Docker GPU environment
in `docs/GPU_ALPHA_LAB.md`:

```bash
docker compose --profile gpu run --rm --no-deps \
  --entrypoint python gpu-research \
  -m emergenzix_gpu_lab.fold_train \
  --db-path /data/emergenzix.db \
  --dataset-id DATASET_SHA256 \
  --folds 3 --fold 1 \
  --model tcn --device cuda --seed 42 --epochs 20 \
  --output-dir /outputs/fold01-tcn-s42
```

Do not execute this until the human operator intentionally boots an A10
host. The GPU Compose service has `network_mode: none`, no published ports,
a read-only source SQLite volume, and a separate experiment output volume.

## Saved evidence

Each successful run produces:

- `model_weights.pt` — locally created weights; loading external or
  untrusted checkpoints must use PyTorch's safe loading mode.
- `manifest.json` — `gpu-walk-forward-fold-v1` protocol, dataset SHA-256,
  parent `plan_sha256`, fold number, full fold boundaries/times, model,
  hyperparameters, library/device versions, train-only feature scaler,
  stage sample counts, and safety declarations.
- `metrics.json` — best validation loss/epoch, **single untouched holdout**
  MSE/MAE/direction accuracy and zero-return baseline on the same candles.
- `test_predictions.json` — UTC anchor and target timestamps, actual and
  predicted future log returns for each holdout sample.
- `sha256.json` — SHA-256 of the four other files, written only after
  those files are created.

Incomplete runs can leave directories without `sha256.json`. They must
be rejected as incomplete; NEVER resume by overwriting a directory.
File hashes verify accidental alteration only; they are not signatures.

**Important:** PR #58's existing validator checks **E001** artifacts and
must not be treated as validating the new `gpu-walk-forward-fold-v1`
format. New fold-aware offline artifact verification is future work.

## Codex verification gate

```bash
cd backend
python -m pytest -q \
  tests/test_gpu_fold_train.py \
  tests/test_gpu_fold_samples.py \
  tests/test_gpu_walk_forward.py \
  tests/test_gpu_alpha_lab.py
```

Run backend CI and perform a separate manual A10 smoke/reproducibility
acceptance. In particular, verify that two runs with the same seed and
identical train/validation histories but different *holdout candles*
select identical checkpoint weights and validation epochs.

## Not yet implemented

Automated multi-fold orchestration, cross-fold aggregation, signed artifact
attestation, statistical significance or multiple-trial corrections,
transaction costs, slippage, funding/PnL, strategy promotion and exchange
execution. Do not compare experiments by selectively rerunning folds after
looking at their holdout outcomes.

**Next microtask:** build an independent, read-only verifier for the new
fold protocol that recomputes stored fold metrics, validates the frozen
plan fingerprint/bounds and detects incomplete or tampered artifacts.
