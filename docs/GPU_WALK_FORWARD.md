# GPU Alpha Lab — Walk-forward planning v1 (offline)

Status: **implemented planner; model training and scoring NOT implemented**.

This separate research-only capability creates a deterministic, read-only
plan for multiple chronological retraining and holdout experiments. It does
not modify the existing E001 code or create trading permissions. All ranges
index contiguous UTC candles from a registered SHA-256-verified dataset.

## Purpose

E001 uses a single 70/15/15 chronological split. Walk-forward evaluates
several later out-of-sample periods while giving each future fold an
expanding set of previously observed training candles. We must fix these
windows before GPU training, rather than select convenient periods after
seeing results.

The planner uses a fixed minimum training history, then advances by one
test window by default while validation and test lengths stay fixed.
Tests across folds never overlap. A short dataset causes a clear error
instead of a silently reduced fold count.

## Run without a GPU

Install the ordinary backend dependencies (including NumPy) and register
a contiguous UTC 1h, 4h or 1d dataset as for E001.

From the backend directory:

    python -m emergenzix_gpu_lab.walk_forward \
      --db-path /path/to/emergenzix.db \
      --dataset-id DATASET_SHA256 \
      --folds 3 --train-bars 512 \
      --validation-bars 128 --test-bars 128 \
      --lookback 32 --horizon 1

The command only emits a JSON plan to stdout. It does NOT start GPU
training, write to the production SQLite database, create model weights,
write run artifacts, connect to an exchange or place orders.

Defaults require at least 1,024 contiguous candles. For a serious model
experiment, prefer much longer history. Window widths are counted in
**candles**, not days, so 128 candles mean different durations at 1h/4h/1d.

## Chronology and leakage controls

Stage bars and anchors are half-open index ranges: [start, stop).
An anchor t observes only bar indices <= t; its label uses close[t+horizon].
The planner purges all anchors with targets crossing split boundaries.

- Train labels must end strictly before validation begins.
- Validation labels must end strictly before test begins.
- Test labels must end before their fold's test window ends.
- The first train anchor is lookback-1, allowing past-only features.
- Every future fold may use older folds' elapsed test periods as
  **past** training history; never its own validation/test bars.
- step_bars >= test_bars is mandatory, so distinct test windows do not overlap.
- Downstream materialization must fit a **new train-only normalizer** per
  fold; reusing E001's global 70/15/15 scaler is NOT acceptable.

Default layout for exactly 1,024 candles (indices start at zero):

| Fold | Train | Validation | Holdout |
|---|---|---|---|
| 1 | [0, 512) | [512, 640) | [640, 768) |
| 2 | [0, 640) | [640, 768) | [768, 896) |
| 3 | [0, 768) | [768, 896) | [896, 1024) |

Every stage records bar and anchor ranges, first/last target indices,
UTC anchor/target timestamps and number of purged samples. The output
includes dataset_sha256, configuration, unused_tail_bars and plan_sha256,
a deterministic digest of canonical JSON (excluding the digest itself).

## Codex verification (CPU-only)

From the backend directory:

    python -m pytest -q tests/test_gpu_walk_forward.py

Check invalid windows, boolean or fractional numeric arguments, overlap,
minimum examples and short datasets. Confirm equal inputs produce the
same plan digest and that reading the registered dataset does not mutate
the source SQLite database.

## Not yet implemented

Fold-aware feature materialization, new train-only normalization per fold,
three separate GPU training runs, validation checkpoint selection, fold
aggregation, slippage/fees/PnL, significance tests and any form of trade
authorization remain outside this PR.

**Next microtask:** feed this plan to a fold-aware sample builder, and
prove that changing future validation/test candles cannot change a
fold's training samples or fitted normalizer. Only then can the GPU
trainer safely consume the planned folds.
