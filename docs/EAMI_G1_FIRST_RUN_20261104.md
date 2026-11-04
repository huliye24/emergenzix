# EAMI G1 first resumable research run — 2026-11-04

Status: **implemented and verified for historical research only**.

## Fixed release and runtime

- Git commit: `d88affd`
- GPU node release: `/home/ubuntu/emergenzix-runs/d88affd`
- Python: existing isolated Qlib research environment
- Registry: `/home/ubuntu/.local/share/emergenzix-g1/registry/eami.db`
- Feature asset: `/home/ubuntu/.local/share/emergenzix-g1/features/btcusdt-4h-return-v1.parquet`
- Feature SHA-256: `1acfd31c3de24a4c64ea88c04f2eeee12864ab457120ed64f14a7cc01ec9d8dd`
- Feature rows: 2,202 complete BTCUSDT 4h candles
- Feature range: 2025-11-01 00:00 UTC through 2026-11-02 20:00 UTC

The feature asset was registered, validated, and published through append-only
quality events before the inference session was created.

## Registered identities

- raw asset: `710c2fe86b2320c8c774c0ce519058369e4dde7a6d309cff2e56461df2f4b615`
- feature asset: `bdfdbf8327be3eb379ab5e097f773010b1328c866e835565e21c8392b5bbc7a5`
- model: `4061e9d0b87dc4004d00a23c497f6424c8d985992412b1b79344886cafb6c7b5`
- inference session: `9c62d5c231652d9cc02a0c49ddbb5b1e1458d4f90827793d3598bdbc215981e6`

## Observed run

The first on-demand session covered 2026-10-01 00:00 UTC through
2026-11-02 20:00 UTC:

- predictions appended: 198
- failures appended: 0
- matured outcomes appended: 197
- remaining expected-candle gaps: 0
- order permission: false

An immediate second invocation appended zero predictions, zero failures, and
zero outcomes and still reported zero gaps. This verifies resumable idempotency
for the fixed session. SQLite `PRAGMA integrity_check` returned `ok`.

## Interpretation limits

G1 is deliberately simple: it classifies the sign of the prior 4h return and
emits an uncalibrated score. It is a pipeline and evidence baseline, not a claim
of predictive value. The pinned cloud mirror ended on 2026-11-02, so this first
run is historical rather than current-market inference. No PAPER, TESTNET, or
LIVE order was created or authorized. Existing GPU research timers and release
symlinks were not changed.
