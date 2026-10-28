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

## Fold-aware sample construction (stacked follow-up PR)

The follow-up `emergenzix_gpu_lab.fold_samples.make_fold_samples` now
materializes E001-compatible NumPy tensors for **one frozen fold**.
This addition depends on the planning PR and must be merged afterward.
It reuses exactly the same four causal financial features as E001 and
retains the same `Samples` return shape (train_x/train_y, val_x/val_y,
test_x/test_y, train/val/test anchors, mean/std and split cut indexes).

For each fold, the feature normalizer is fitted exclusively on features
from candle index zero through the **last training feature anchor**,
inclusive. Future validation/test feature rows and their target labels
cannot influence that scaler or the fold's training samples. Features for
the first validation/test anchors may use earlier candles as past context,
but never information later than their own anchor.

Python usage after loading a verified registered dataset:

```python
from emergenzix_gpu_lab.dataset import load_registered_dataset
from emergenzix_gpu_lab.walk_forward import plan_walk_forward
from emergenzix_gpu_lab.fold_samples import make_fold_samples

dataset = load_registered_dataset("/path/to/emergenzix.db", "DATASET_SHA256")
windows = plan_walk_forward(len(dataset.times), lookback=32, horizon=1)
first = make_fold_samples(dataset, windows[0], lookback=32, horizon=1)
print(first.train_x.shape, first.val_x.shape, first.test_x.shape)
```

No model is fitted and no files are produced by these calls. The fold
must come from the same verified history and planning parameters; a
stale or modified fold boundary is rejected, but these in-memory
objects are not signed or independently authenticated.

Codex CPU-only tests for the materialization follow-up:

```bash
cd backend
python -m pytest -q tests/test_gpu_fold_samples.py tests/test_gpu_walk_forward.py tests/test_gpu_alpha_lab.py
```

These tests include future-candle mutation checks, stage/horizon
mismatch rejection and preservation of the original E001 causal
feature arithmetic. They have not been run as part of the code-writing
handoff.

## Still not implemented

Three separate GPU training runs, checkpoint selection using each fold's
validation only, holdout metrics and aggregation, slippage/fees/PnL,
significance tests and strategy promotion remain outside these PRs.

**Next microtask:** implement the one-fold training executor consuming
`make_fold_samples`, saving provenance and choosing its checkpoint by
validation loss alone. Do not start A10, add exchange permissions or
claim that the plan and tensors demonstrate profitable alpha.
