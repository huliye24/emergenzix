# GPU Walk-forward fold artifact verifier (PR #62)

**Status: code submitted for review; tests and runtime acceptance pending.**
This is a **read-only, CPU-only** evidence check for completed
`gpu-walk-forward-fold-v1` runs from PR #61. It does not import PyTorch,
load checkpoint weights, launch A10, connect to exchanges or authorize
PAPER/TESTNET/LIVE orders.

The earlier E001 verifier from PR #58 checks a different protocol and
MUST NOT be treated as interchangeable with this tool.

## CLI — requires the registered source dataset

From `backend/` on a host with the ordinary backend dependencies:

```bash
python -m emergenzix_gpu_lab.fold_verify \
  --db-path /path/to/emergenzix.db \
  --run-dir /path/to/fold-01-mlp-s42
```

The database is opened through the existing verified loader using SQLite
`mode=ro`. It must contain the original registered `dataset_sha256`
specified in the fold manifest. The run directory must contain all five
artifacts created by the fold trainer, including `sha256.json`, which is
the final completion marker.

Success prints JSON with `"status": "verified_against_source"`. A
rejected or incomplete run exits nonzero and prints the reason. It never
modifies experiment outputs or the registered source database.

## Verification contract

1. Reject missing, symlinked, oversized or malformed expected artifacts;
   reject a missing completion marker and any changed SHA-256 digest.
2. Require the exact supported fold/plan protocols, the fixed E001 feature
   schema, and research-only / no-orders / no-promotion safety fields.
3. Reopen the content-addressed source dataset and verify its OHLCV hash,
   contiguous UTC history, symbol and timeframe.
4. Rebuild the full deterministic walk-forward plan **from source candles
   and recorded bounded configuration**. Require exactly the same plan
   SHA-256, fold number, sample counts, timestamp windows and purged split
   boundaries as reported by the trainer.
5. Independently calculate the training feature mean/std only through the
   last train anchor. Never fit against validation or holdout features.
6. Verify **every** holdout row's anchor/target UTC times and target
   log return against the source dataset (the trainer stores float32
   labels). Reject gaps, duplicates, reordering and changed targets.
7. Recompute saved MSE, MAE and direction accuracy from predictions,
   including the zero-return baseline, and verify best epoch and
   finite validation loss.

This is materially stronger than checking a self-declared SHA-256 list
alone, but it remains a **consistency** check, not cryptographic proof of
who generated the files. It does **not** deserialize or evaluate the
model checkpoint. Therefore a forged, self-consistent prediction series
could still pass after adjusting its metadata and checksum. A separate
independent inference replay and trusted attestation would be required
to prove weights produced those predictions.

## Codex acceptance

```bash
cd backend
python -m pytest -q tests/test_gpu_fold_verify.py
python -m pytest -q \
  tests/test_gpu_fold_train.py \
  tests/test_gpu_fold_samples.py \
  tests/test_gpu_walk_forward.py
```

The test suite includes a generated complete fold without importing
PyTorch and an optional real CPU trainer integration test when PyTorch
is installed. It covers missing files, unsafe symlinks, duplicate JSON,
nonfinite JSON, modified SHA-256, forged/resealed manifests, plans,
training scalers, source labels and metrics. Tests have **not** been
executed by the code-writing agent; Codex should run and fix as needed.

Merge order: #59 (planner) -> #60 (fold data) -> #61 (one-fold
trainer) -> **this PR**. #58 remains an independent E001-only gate.

## Remaining work

No multi-fold orchestration, fold aggregation, fee/slippage-adjusted PnL,
model replay, statistics/multiple-test correction, hardware acceptance,
or strategy/exchange promotion is implemented here. A passing verification
must never grant permission to send orders.
