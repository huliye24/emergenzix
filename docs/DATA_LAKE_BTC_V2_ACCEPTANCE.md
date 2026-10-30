# Binance BTCUSDT candle-v2 S3 acceptance

- Observed: 2026-10-30 UTC
- Builder code: `1832325`
- Incremental updater code: `7b0d49a`
- Bucket: `emergenzix-research-382309211832-cn-northwest-1-an`
- Execution mode: `RESEARCH_ONLY`

## Accepted snapshot

- Snapshot ID: `f1e0aa6fab3cb4a95a8e0edf57822d566700f07415e4611f8c331d3bd961aabe`
- Manifest: `market-data/v2/manifests/snapshot=f1e0aa6fab3cb4a95a8e0edf57822d566700f07415e4611f8c331d3bd961aabe/manifest.json`
- Coverage: 2021-01-01 00:00 UTC through 2026-10-30 05:00 UTC
- Base 15-minute rows: 204,239
- Imputed rows: 0
- Forming candles: excluded

All 204,239 base rows contain:

- BTC base volume;
- USDT quote volume / turnover;
- trade count;
- taker-buy BTC volume;
- taker-buy USDT volume;
- open, close, and availability timestamps;
- explicit closed and imputed flags.

The historical-adapter availability time is the interval close because the
archives do not preserve original publication latency. Live incremental rows
record the update observation time and never become available before interval
close.

## Read-back verification

The cloud verifier independently downloaded and checked the pointer, manifest,
every Parquet object, and every referenced source object.

| Timeframe | Rows |
| --- | ---: |
| 15m | 204,239 |
| 1h | 51,056 |
| 4h | 12,757 |
| 1d | 2,121 |
| 1w | 297 |
| 1mo | 63 |

- Parquet objects verified: 413
- Source objects verified: 99
- Official checksum objects present: 98
- Pointer, manifest, and recomputed canonical snapshot SHA-256: identical
- Schema on every Parquet object: `emergenzix-market-candle-v2`
- Required columns on every Parquet object: verified
- v1 pointer unchanged: yes

The seven checksum-valid Binance source gaps and 70 missing 15-minute candles
remain explicit. No other venue, forward fill, or synthetic price was inserted.
Incomplete derived buckets are omitted.

## Incremental operation

The Ningxia CPU host runs `emergenzix-market-data-btc-v2.timer` every 13
minutes with pinned code `7b0d49a`. The first observed systemd run read the
full gap-aware snapshot and returned `no_change` with exit status 0. It rejects
source revisions, insufficient overlap, a new tail gap, pointer races, and
stored-object hash failures before advancing the v2 pointer.

The original v1 timer remains active and independent. This preserves existing
consumers while v2 readers are integrated and tested.

## Boundary

This acceptance covers Binance spot BTCUSDT only. It does not claim that OKX,
Bybit, ETH, or BNB data is populated. A separate venue dataset may compare with
Binance, but it may not repair or overwrite this canonical venue history.
Nothing in this data pipeline grants TESTNET or LIVE order permission.
