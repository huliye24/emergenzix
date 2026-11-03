# Immutable research compute pipeline acceptance — 2026-11-03

Status: **implemented and verified for research/backtest use**.

This acceptance record covers one fixed research protocol: a content-addressed
BTC/ETH/BNB data snapshot, a deterministic CPU/GPU routing plan, completed CPU
and GPU jobs, and installed recurring schedules. It does not authorize PAPER,
TESTNET, or LIVE orders and does not promote any model into a trading strategy.

## Immutable input

The published S3 snapshot-set manifest is:

- bucket: `emergenzix-research-382309211832-cn-northwest-1-an`
- region: `cn-northwest-1`
- key: `market-data/v2/collections/snapshot=68987ccad05bffe3645d4f0efaea0b060439182f66a53b0c5f01464faa97916a/manifest.json`
- snapshot-set ID: `68987ccad05bffe3645d4f0efaea0b060439182f66a53b0c5f01464faa97916a`
- manifest payload SHA-256: `fb5248337b92b71b54266a8753b9d9c014445e15d452b57e3a64321ed6246912`
- S3 version ID: `f.1fMWVhNl9TrymKhANTyZsMYtWZ649r`
- selected range: `2025-11-01T00:00:00Z` through
  `2026-11-02T00:00:00Z` (end exclusive)
- local Parquet collection SHA-256:
  `d89957dfc07919f9f9e1afe694f4d195366a80fc636d687fb53845ced0a681aa`

The manifest contains exactly BTCUSDT, ETHUSDT, and BNBUSDT canonical v2
snapshot IDs. The publisher verifies range coverage, excludes forming candles,
requires zero imputed rows, reads the object back after upload, and refuses to
replace different content at the same content-addressed key.

S3 bucket versioning is enabled. S3 Object Lock is not configured, so the word
"immutable" here means content-addressed identity, publisher conflict refusal,
and versioned recovery; it is not a bucket-level WORM retention guarantee.

## Fixed experiment plan

- plan ID: `4954b7992cb8822409aa491b4a71096d4edde0c1da67bfbcbc1a3879857af722`
- pinned experiment code: `7abc94f`
- permissions: `paper=false`, `testnet=false`, `live=false`

| Job | Node class | Dependency | Result SHA-256 |
| --- | --- | --- | --- |
| `classic-turtle-matrix` | CPU | snapshot set | `6db6b9cdc7f96072cfbd9ceef90f1c7cca703206b4333958349a9f8f7852cbce` |
| `chronos-zero-shot` | GPU | snapshot set | `ddd44a753715ca0f4e5872ae98743945dcf4c2c81d5f7036dd4324caae84a68c` |
| `qlib-locked-models` | GPU | Chronos report | `d0117ba574f90885493671d7bae8396a46c1e425bece7dbee765f729625bfce3` |

The dispatcher rejects a job on the wrong node class, verifies the plan and
local collection hash before execution, accepts only allowlisted commands, and
uses output hashes plus receipts to make reruns idempotent.

## Observed runs

The CPU run produced the 18-cell BTC/ETH/BNB × 1h/4h/1d × Turtle 20/10 and
55/20 matrix, with four out-of-sample folds per cell. The protocol uses 120
training days, 60 test days, and a 60-day step. Its report hash is
`1eaf9e81b4cfd0f452d4b9e7ac6c003eac87bedecfec644caf97d4f849833966`.

The GPU node was verified as an NVIDIA A10G with Torch `2.15.0+cu132`. Chronos
loaded its pinned local model snapshots in offline mode. Qlib `0.9.8` completed
Transformer, LSTM, ALSTM, and TRA training against the locked feature, label,
training, validation, and test boundaries. All four models and both Chronos
models were scored on the same 72 test origins.

| Model | Direction accuracy | Cost-adjusted sampled return sum |
| --- | ---: | ---: |
| Chronos 2 | 52.78% | -6.68% |
| Chronos Bolt Small | 61.11% | -10.89% |
| Qlib Transformer | 36.11% | -17.49% |
| Qlib LSTM | 36.11% | -14.88% |
| Qlib ALSTM | 43.06% | -12.93% |
| Qlib TRA | 50.00% | -11.18% |

These results are evidence, not a deployment recommendation. Every listed
model was negative after the fixed transaction-cost assumption, so none should
be promoted. GPU deterministic algorithms use `warn_only=True`; the current
Torch attention kernel can still be nondeterministic at the bit level. The
immutable inputs, configuration, artifacts, and output hashes remain available
for comparison even where exact GPU bitwise replay is not guaranteed.

## Installed schedules

The CPU host has `emergenzix-research-cpu.timer` enabled. It runs the classic
matrix daily at 02:15 UTC with up to five minutes randomized delay. The GPU host
has `emergenzix-research-gpu.timer` enabled. It runs Chronos and then Qlib every
Sunday at 03:15 UTC with up to fifteen minutes randomized delay.

Both hosts expose the same pinned plan at
`/etc/emergenzix/research-compute-plan.json`, the release at
`/opt/emergenzix-research/current`, the immutable local mirror at
`/var/lib/emergenzix/research-data/current`, and receipts below
`/var/lib/emergenzix/research-runs`. Updating the schedule to a new experiment
requires publishing a new snapshot set and atomically installing a newly built
plan; the timers do not mutate a plan or select a moving data pointer.

Manual systemd acceptance runs returned `Result=success`. The CPU job and both
GPU jobs returned `status=already_complete`, confirming receipt-based
idempotency through the installed production paths.

## Verification and implementation references

- `b7811eb`: immutable research snapshot-set publisher
- `64a6b8d`: fixed CPU/GPU routing and dispatcher
- `4476255`: pinned offline Chronos cache resolution
- `7abc94f`: unified filename-ordered collection hashing
- `6621b78`: recurring CPU/GPU systemd units
- focused test run: 18 passed
- Ruff check over the changed pipeline and tests: passed
- `systemd-analyze verify` on both deployed unit pairs: passed after their
  standardized runtime paths were installed

No exchange credentials were read, no order endpoint was called, and every
receipt records `orders_created=false` and `live_order_permission=false`.
