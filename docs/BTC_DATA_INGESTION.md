# Verified BTC/BNB 4h Historical Data

## Scope

The first archive importer is intentionally narrow: Binance **spot BTCUSDT, 4-hour candles**. It uses Binance's public historical archive at \`data.binance.vision\`, verifies each ZIP file against its published SHA-256 checksum, normalizes timestamps to UTC, filters the requested date range, and stores the result as an immutable, content-addressed dataset.

No TradingView credentials, Binance API key, account permissions, or trading endpoints are used. This is a legal public-data path, not a workaround for TradingView's subscription controls.

The original BTC endpoint remains backward compatible. The research sync CLI
also supports allowlisted Binance spot `BTCUSDT` and `BNBUSDT`. It combines the
checksum-verified archive with the latest public Binance/CCXT tail, requires at
least six identical overlap candles, excludes the forming candle, verifies
strict four-hour continuity, and registers a new immutable snapshot.

## Endpoint

\`POST /api/v1/datasets/import/binance-btc-4h\`

Request body:

~~~json
{
  "start_at": "2021-01-01T00:00:00Z",
  "end_at": "2026-01-01T00:00:00Z"
}
~~~

Both timestamps must include a timezone offset. The range is inclusive by candle opening timestamp, cannot be in the future, and is capped at ten years and 50,000 bars.

The importer uses official monthly ZIP archives where available and falls back to daily ZIP archives when a monthly archive is unavailable. It refuses to persist an archive whose checksum is absent or incorrect, or to silently skip a missing historical daily file. The current UTC day is skipped when only daily archives would be available, because Binance publishes daily archives after the day ends.

The response contains the immutable dataset ID and SHA-256, the actual first and last candle timestamps, the candle count, and source provenance. Re-importing an identical dataset returns the existing dataset with HTTP 200.

## Example workflow

1. Import a BTC 4h date range.
2. Use the returned \`id\` as \`dataset_id\` for a historical replay.
3. Provide either a random \`seed\` or an exact \`anchor_at\` timestamp to \`POST /api/v1/research/historical-replays\`.
4. Inspect the saved replay and its SHA-256 fingerprint.

For the cloud rolling-test dataset, run:

~~~bash
cd backend
python -m emergenzix_api.market_history_sync \
  --symbols BTCUSDT BNBUSDT \
  --start 2021-01-01T00:00:00Z \
  --db-path /data/emergenzix.db
~~~

The command is safe to rerun. Identical content resolves to the same dataset;
new completed candles create a new dataset ID without mutating old snapshots.
It fails closed on a missing or wrong archive checksum, archive/public source
disagreement, an internal 4h gap, less than 730 days of coverage, or failure to
reach the newest completed candle. It is `RESEARCH_ONLY` and cannot place an
order.

For the TradingView screenshot, read the chart's displayed timezone. If the selected candle is labelled \`2024-03-17 04:00\` while the chart is in UTC+08:00, send \`2024-03-17T04:00:00+08:00\`. The replay API converts it to UTC and requires an exact match to the candle's opening timestamp.

## Storage boundary

This importer stores a moderate 4h history in SQLite because that is the current dataset store. It is not intended for multi-million-row 1-minute archives. Long-range minute data, trades, and order-book snapshots should be stored as partitioned Parquet files in object storage and queried through an analytical engine; they should not be packed into one SQLite JSON field.

## Cloud inventory observed on 2026-10-30

- AWS CPU/MATLAB (`52.83.234.254`) had 63 monthly 4h ZIP files per symbol for
  BTCUSDT and BNBUSDT, spanning 2020-11 through 2026-01. They were paired with
  locally named `.sha256` files, but were stale and not registered as one
  canonical product dataset.
- The CPU research database contained six BTC-only four-month snapshots. Its
  newest dataset ended at 2026-05-31 and there was no registered BNB dataset.
- AWS GPU (`43.192.236.139`) had 101 BTCUSDT monthly 4h ZIP files spanning
  2017-08 through 2025-12, paired one-for-one with official `.CHECKSUM` files.
  That cache was also stale and BTC-only.

These directories are input evidence, not the product source of truth. The
sync command downloads and verifies the current official source instead of
silently trusting or modifying scattered experiment caches.

Tests are authored for the repository CI/Codex workflow and are not run as part of this coding task.
