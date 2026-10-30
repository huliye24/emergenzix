# ADR-004: Versioned S3 market-data lake

- Status: Accepted for implementation
- Date: 2026-10-30
- Scope: Research market data only; no order permissions

## Context

Research files are currently split across local SQLite databases and AWS CPU/GPU
experiment directories. BTC history is relatively deep, BNB is less governed,
and neither cloud cache is current. Separate downloads for each candle interval
would make cross-timeframe results difficult to reconcile and reproduce.

Emergenzix already has a private, encrypted, versioned S3 bucket in
`cn-northwest-1`: `emergenzix-research-382309211832-cn-northwest-1-an`.
The existing `runs/` prefix contains research output and is not a canonical
market-data store.

## Decision

### Canonical source and timeframes

The first canonical dataset is Binance spot BTCUSDT, ETHUSDT, and BNBUSDT at 15-minute
resolution. Checksum-verified official archives provide history; the public API
provides the recent unpublished tail. The two sources must overlap and agree.

The canonical 15-minute data deterministically derives these closed-candle
datasets in UTC:

| Timeframe | Boundary | Expected complete source bars |
| --- | --- | ---: |
| 1h | Every UTC hour | 4 |
| 4h | 00/04/08/12/16/20 UTC | 16 |
| 1d | 00:00 UTC | 96 |
| 1w | Monday 00:00 UTC | 672 |
| 1mo | First calendar day 00:00 UTC | Variable by month |

Aggregation is `open=first`, `high=max`, `low=min`, `close=last`, and
`volume=sum`. An incomplete bucket is not published. Native Binance 1h, 4h,
1d, 1w, and 1mo files are validation sources, not independent strategy inputs.

Fifteen-minute bars are sufficient for the initial medium-frequency research
scope. They are not sufficient for execution-quality, microstructure, or
high-frequency models. Those require separately governed one-minute bars,
trades/aggregate trades, order-book snapshots, funding, mark/index prices, and
instrument metadata; they must never be synthesized from 15-minute candles.

### S3 zones and object keys

All market-data keys are below `market-data/v1/`; research results remain under
`runs/`.

```text
market-data/v1/
  bronze/provider=binance/market=spot/kind=klines/
    symbol=btcusdt/interval=15m/year=2026/month=10/<vendor-file>.zip
    symbol=btcusdt/interval=15m/year=2026/month=10/<vendor-file>.zip.CHECKSUM
  silver/exchange=binance/market=spot/symbol=btcusdt/timeframe=15m/
    year=2026/month=10/part-<content-sha256>.parquet
  gold/exchange=binance/market=spot/symbol=btcusdt/timeframe=4h/
    year=2026/month=10/part-<content-sha256>.parquet
  manifests/snapshot=<snapshot-sha256>/manifest.json
  pointers/binance-spot-btcusdt-15m-latest.json
```

Bronze, silver, gold, and manifests are immutable. Only a small `pointers/`
object may advance to a newer verified snapshot. A consumer pins the manifest
SHA-256, never an unversioned `latest` pointer, for a backtest or validation.

Parquet is the normalized analytical format. Partitions use low-cardinality
keys and calendar month boundaries to avoid a small-file explosion. SQLite
stores dataset/catalog metadata and pinned manifest identities, not multi-year
intraday candle payloads.

### Required manifest and gates

Each snapshot manifest records:

- provider, exchange, market type, symbol, base interval, schema version;
- inclusive first and last open times and last completed-candle boundary;
- raw object keys, official checksums, normalized partition hashes and sizes;
- row counts, expected rows, duplicates, gaps, off-grid timestamps, and OHLC
  invariant failures;
- archive/public overlap count and reconciliation status;
- derived partition lineage and aggregation code version;
- source commit, creation time, previous snapshot, and snapshot SHA-256.

Publication fails closed on checksum failure, source disagreement, duplicates,
an internal gap, off-grid timestamps, invalid OHLCV, insufficient history, a
forming final candle, or inability to reach the freshness target. A research
job also fails closed if its pinned manifest or any referenced object hash no
longer verifies.

Binance can revise historical archives. A revision creates new bronze objects,
new normalized partitions, and a new manifest linked to the superseded
snapshot. Existing research remains reproducible against its old manifest.

### Operations and security

- Use the existing versioned, AES256-encrypted, public-access-blocked bucket.
- Cloud workloads use an instance role or narrowly scoped workload role; no
  static AWS key is committed or written to reports.
- The ingestion role may write only `market-data/v1/`; research workers receive
  read-only access to pinned snapshots.
- Synchronization is idempotent and scheduled after closed-candle boundaries.
  It remains quiet when no new completed data exists and alerts on failed gates.
- Retention/lifecycle policy must preserve manifests and every object pinned by
  a research result. Cache eviction on compute hosts does not delete S3 data.

## Consequences

The design produces one coherent multi-timeframe history and makes every
research result reproducible. It adds PyArrow/Parquet, S3 upload/catalog code,
data-quality monitoring, and an operational scheduler. These are implementation
tasks; this ADR by itself does not claim that the data lake is populated.

The first implementation milestone is complete only when BTCUSDT, ETHUSDT, and BNBUSDT
15m history from 2021-01-01 through the latest completed candle is in S3, all
five derived timeframes reconcile, manifests and hashes verify, and a cloud
reader can reproduce the same row counts without using a local experiment
cache.
