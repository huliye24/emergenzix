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

## Advisory inference artifact acceptance

Commit `3abf9d6` adds a read-only model-signal contract. A Chronos or Qlib
record is accepted only when its family, version, source revision, model-file
SHA-256, exact OHLCV window SHA-256, input start/end timestamps, generation
timestamp, next-bar target, and self-hash all verify. Stale, future-dated,
malformed, mismatched, or permission-bearing records fail closed. A verified
record remains advisory and cannot authorize PAPER, TESTNET, or LIVE orders.

An A10G smoke run used `amazon/chronos-bolt-small` from the pinned Chronos
2.3.2 cache over 199 completed public BTC/USDT 1h bars. The independently
loaded artifact verified with model-file SHA-256
`06a6a19bbe74bc10a9cd193bd4bf2bf638ae07f7e0d51653ae7ab8ea968a21dd`
and returned `order_permission=false`. Its score is deliberately fixed at 0.5
because the point forecast is not a calibrated probability. This was a
one-shot smoke artifact, not a continuously published production signal.

The existing Qlib checkpoints are not exported as current signals yet. Their
locked feature contract includes taker-buy volume and training-only
normalization statistics, while the current live CCXT view exposes basic
OHLCV. Substituting missing features would violate the exact-snapshot contract,
so Qlib correctly remains `artifact_unavailable` until a compatible immutable
feature snapshot and inference adapter are added.

## THUML Timer isolated smoke evaluation

The official THUML repository was pinned at
`thuml/Large-Time-Series-Model@1ff8d1afc073182e6d46022069ff32470ab47945`
(MIT). The `thuml/timer-base-84m` checkpoint was pinned at
`70077a71acce1b4c00d98332fcaabc694255d8e5` (Apache-2.0 card), with
model-file SHA-256
`9c3d18f12ffe1ea7d4fa70eb3304b26e3841164a6a265fbae4f7a05cd213aa3d`.
Its remote Python files were inspected before execution; the focused scan found
no subprocess, shell, network, or filesystem mutation calls.

Timer was loaded offline in a separate environment using Transformers 4.40.1
and Torch 2.15.0+cu132, then completed one GPU forecast over the latest 192
completed BTC/USDT 1h closes. This proves only runtime compatibility. It does
not establish accuracy, calibration, cost-adjusted value, strategy promotion,
or execution permission. Timer is not installed in the API runtime and is not
yet an allowlisted product signal family.
