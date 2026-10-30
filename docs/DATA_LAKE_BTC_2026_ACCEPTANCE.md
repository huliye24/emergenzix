# BTCUSDT 15m S3 data-lake acceptance — 2026

- Observed: 2026-10-30 UTC
- Source code: `e0a5d1e`
- Collector: `binance/binance-public-data@d571690eb66760763b0bd6e27e8cd35603c7e244`
- Collection path: Hong Kong collector -> local relay -> Ningxia CPU verifier -> Ningxia S3
- Bucket: `emergenzix-research-382309211832-cn-northwest-1-an`
- Execution mode: `RESEARCH_ONLY`

## Accepted snapshot

- Snapshot ID: `ad5c2503ff479313dbae77b00f5260302ab09f806f535d91bb1beff03ae52c7a`
- Manifest: `market-data/v1/manifests/snapshot=ad5c2503ff479313dbae77b00f5260302ab09f806f535d91bb1beff03ae52c7a/manifest.json`
- Base coverage: 2026-01-01 00:00 UTC through 2026-10-30 04:30 UTC
- Archive/public-tail overlap: 980 candles
- Hong Kong official archive checks: 311 passed, 0 failed
- Selected archive set: nine monthly packages plus 29 October daily packages
- Selected archive rows: 28,992, with no gaps or duplicates
- Final base rows after the current public tail: 29,011

## S3 read-back verification

The verifier downloaded the pointer, manifest, every Parquet object, and every
source object from S3. The pointer snapshot, stored manifest snapshot, and
independently recomputed canonical manifest SHA-256 were identical.

| Timeframe | Rows | First open UTC | Last complete open UTC |
| --- | ---: | --- | --- |
| 15m | 29,011 | 2026-01-01 00:00 | 2026-10-30 04:30 |
| 1h | 7,252 | 2026-01-01 00:00 | 2026-10-30 03:00 |
| 4h | 1,813 | 2026-01-01 00:00 | 2026-10-30 00:00 |
| 1d | 302 | 2026-01-01 00:00 | 2026-10-29 00:00 |
| 1w | 42 | 2026-01-05 00:00 | 2026-10-19 00:00 |
| 1mo | 9 | 2026-01-01 00:00 | 2026-09-01 00:00 |

- Parquet objects verified: 59
- Bronze sources verified: 39
- Gaps: 0
- Duplicates: 0
- Off-grid timestamps: 0
- Forming candles excluded: yes

## Boundary and limitations

This acceptance covers BTCUSDT from 2026-01-01 only. It does not yet satisfy
the M0 multi-year requirement beginning in 2021, and it does not cover ETHUSDT
or BNBUSDT. No PAPER, TESTNET, or LIVE order permission is granted by this
dataset or acceptance result.

## Incremental updater acceptance

The Ningxia CPU host runs the pinned `e9a68a5` updater as a systemd oneshot
service triggered every 13 minutes by
`emergenzix-market-data-btc.timer`. It uses the instance role and Binance's
market-data-only endpoint; it stores no static AWS or exchange credential.

Observed consecutive-run acceptance on 2026-10-30:

1. The first run appended one newly completed 15m candle, advanced the base
   range to 04:45 UTC, linked the previous snapshot, and published snapshot
   `de2842a6682c175d23b6c7168b3f8cf1e63898319cf8c002b1261dfd0ccf1308`.
2. The immediate second run returned `no_change`, retained the same snapshot,
   and did not republish data.
3. A systemd-triggered run exited successfully with `no_change`; the next run
   was scheduled 13 minutes later.

Only closed candles can advance the pointer. A source mismatch, insufficient
overlap, Parquet hash failure, gap, or upload failure leaves the previous
snapshot pointer unchanged.
