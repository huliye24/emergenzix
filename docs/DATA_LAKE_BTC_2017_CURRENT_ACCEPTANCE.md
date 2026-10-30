# Binance BTCUSDT candle-v2 acceptance — listing to current

- Observed: 2026-10-30 UTC
- Collector: `binance/binance-public-data@d571690eb66760763b0bd6e27e8cd35603c7e244`
- Builder and updater code: `517b1d9`
- Bucket: `emergenzix-research-382309211832-cn-northwest-1-an`
- Execution mode: `RESEARCH_ONLY`

## Accepted snapshot

- Snapshot ID: `ac6305939fd4e9f0eca60ea1134b9d9eed25beaa590561449ec716365a315cef`
- First canonical candle: 2017-08-17 04:00 UTC
- Last candle at acceptance: 2026-10-30 05:00 UTC
- Base 15-minute rows: 322,018
- Imputed rows: 0

BTCUSDT has no Binance history before its 2017 listing. The period from
2017-01-01 to the first official candle is `not_listed`, not a data gap.

Every canonical base row contains OHLC, BTC base volume, USDT quote volume,
trade count, taker-buy BTC and USDT volume, open/close/availability time, and
closed/imputed flags.

| Timeframe | Verified rows |
| --- | ---: |
| 15m | 322,018 |
| 1h | 80,491 |
| 4h | 20,097 |
| 1d | 3,327 |
| 1w | 452 |
| 1mo | 84 |

## Read-back evidence

- Parquet objects downloaded and SHA-256 verified: 639
- Source objects downloaded and SHA-256 verified: 140
- Official checksum objects present: 139
- Pointer, manifest, and recomputed canonical snapshot hash: identical
- Non-null rows for every extended volume field: 322,018
- v1 and v2 pointers: verified independent

## Early-source quality events

The added 2017–2020 official archives contain 24 gap intervals. Together with
the seven later intervals, the full snapshot records 31 gaps and 643 missing
15-minute grid candles. These are explicit missing observations; they are not
forward-filled or repaired from another venue.

The February 2018 archive also contains 81 consecutive records whose open
times are shifted by 14.789 seconds from the UTC 15-minute grid. Their official
ZIP and checksum remain immutable Bronze evidence. They are quarantined from
canonical Silver and recorded in `quality.off_grid_details`; timestamps were
not rounded and prices were not reassigned to another interval.

Incomplete 1h/4h/1d/1w/1mo buckets are omitted from Gold. Research consumers
must reject or explicitly exclude windows intersecting a recorded gap.

## Incremental operation

The Ningxia `emergenzix-market-data-btc-v2.timer` runs every 13 minutes using
pinned code `517b1d9`. After publication, a manual systemd run loaded the full
gap-aware and off-grid-aware manifest and returned `no_change` with exit status
0. The timer was restored and observed active.

This acceptance covers Binance spot BTCUSDT only and grants no TESTNET or LIVE
order permission.
