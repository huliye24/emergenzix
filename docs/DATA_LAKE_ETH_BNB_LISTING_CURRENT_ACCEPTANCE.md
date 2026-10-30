# Binance ETHUSDT and BNBUSDT candle-v2 acceptance

- Observed: 2026-10-30 UTC
- Collector: `binance/binance-public-data@d571690eb66760763b0bd6e27e8cd35603c7e244`
- Adapter and updater code: `74c6f2f`
- Bucket: `emergenzix-research-382309211832-cn-northwest-1-an`
- Execution mode: `RESEARCH_ONLY`

## Accepted venue snapshots

| Symbol | First official candle UTC | Last at acceptance UTC | 15m rows | Snapshot |
| --- | --- | --- | ---: | --- |
| ETHUSDT | 2017-08-17 04:00 | 2026-10-30 05:30 | 322,020 | `3b05a80e3d1eeeabb1a16d367f41c7f37430bed45bd219c296df6451a40393bc` |
| BNBUSDT | 2017-11-06 03:45 | 2026-10-30 05:30 | 314,273 | `14b24dbddbf22640cd30148099c92c37c4d1692fa0e854d4de1d4c137f7145f3` |

Periods before each first official candle are `not_listed`, not gaps. Every
canonical base row contains OHLC, base volume, USDT quote volume, trade count,
taker-buy base and quote volume, open/close/availability time, and
closed/imputed flags. Imputed rows are zero for both symbols.

## Derived rows

| Symbol | 1h | 4h | 1d | 1w | 1mo |
| --- | ---: | ---: | ---: | ---: | ---: |
| ETHUSDT | 80,491 | 20,097 | 3,327 | 452 | 84 |
| BNBUSDT | 78,554 | 19,613 | 3,247 | 441 | 82 |

## Independent S3 read-back

| Check | ETHUSDT | BNBUSDT |
| --- | ---: | ---: |
| Parquet objects verified | 639 | 622 |
| Source objects verified | 140 | 137 |
| Official checksum objects present | 139 | 136 |
| Non-null rows for every extended volume field | 322,020 | 314,273 |

For both symbols, the pointer, manifest snapshot, and independently recomputed
canonical manifest SHA-256 matched. Every Parquet object had the v2 schema,
required columns, recorded row count, and recorded content hash.

## Source quality

- ETHUSDT records 31 source gaps totaling 643 missing grid candles.
- BNBUSDT records 30 source gaps totaling 615 missing grid candles.
- Each February 2018 archive contains 81 consecutive off-grid candles from a
  Binance maintenance period. Raw ZIPs remain in Bronze; these rows are
  quarantined from Silver without timestamp rounding.
- Duplicates: zero; imputed rows: zero; forming candles: excluded.
- Incomplete derived buckets are omitted from Gold.

The similarity of maintenance gaps across BTC, ETH, and BNB is venue evidence,
not permission to synthesize missing observations. A future OKX or Bybit
dataset remains an independent venue history.

## Incremental operation

The Ningxia host runs independent BTCUSDT, ETHUSDT, and BNBUSDT v2 updater
services with pinned code `74c6f2f`. ETH and BNB manual acceptance runs loaded
the full histories and returned `no_change` with exit status 0. All three
timers use a 13-minute interval; their activation phases are staggered to avoid
simultaneous full-snapshot rebuilds.

This data is research-only and grants no TESTNET or LIVE order permission.
