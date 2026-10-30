# ADR-005: Venue-preserving multi-exchange market data

- Status: Accepted for implementation
- Date: 2026-10-30
- Scope: Research market data only; no order permissions

## Context

ADR-004 established Binance spot 15-minute candles as the first reproducible
data-lake input. A single venue is not a complete observation of the crypto
market. Venue maintenance, listing history, liquidity concentration, stablecoin
risk, and exchange-specific price dislocations can otherwise be mistaken for a
general market event.

The current normalized candle stores OHLC and base-asset volume under the
ambiguous name `volume`. Binance's raw kline records also contain quote-asset
volume, trade count, and taker-buy volumes, but the first Silver schema does not
retain them. Other venues expose different subsets and units. Combining these
without explicit semantics would create false comparability.

## Decision

### Preserve venues before constructing market-wide views

Binance, OKX, and Bybit spot candles are separate canonical venue datasets.
Each venue has its own Bronze evidence, Silver partitions, manifest, quality
report, and latest pointer. Data from another venue must never fill or overwrite
a missing candle in a canonical venue dataset.

The initial instrument set remains BTC, ETH, and BNB quoted in USDT where the
venue actually lists that instrument. Absence before a venue's listing date is
`not_listed`, not a data gap. USD, USDC, and USDT markets are not silently
treated as identical. A future normalized USD view must record the quote asset
and the FX or stablecoin conversion source.

### Candle schema v2

Every Silver candle uses UTC open time and these fields:

| Field | Requirement | Meaning |
| --- | --- | --- |
| `timestamp` | required | UTC candle-open timestamp |
| `close_time` | required | Exclusive UTC interval end |
| `available_at` | required | Earliest time the completed record may be used |
| `open`, `high`, `low`, `close` | required | Venue price in the stated quote asset |
| `base_volume` | required | Quantity of the base asset traded |
| `quote_volume` | nullable | Quantity of the quote asset traded / turnover |
| `trade_count` | nullable | Venue-reported number of trades |
| `taker_buy_base_volume` | nullable | Venue-reported aggressive-buy base quantity |
| `taker_buy_quote_volume` | nullable | Venue-reported aggressive-buy quote quantity |
| `is_closed` | required | Candle completion state |
| `is_imputed` | required, false for canonical Silver | Whether values were synthesized |

Missing optional fields remain null; zero means a reported zero and must not be
used as a missing-value sentinel. The manifest records field availability and
vendor units. For spot BTCUSDT, `base_volume` is BTC and `quote_volume` is USDT.
Derived timeframes sum volume and trade-count fields only when all required
source bars are present. `VWAP = quote_volume / base_volume` is derived when
both values are available and base volume is positive; it is not substituted
for the exchange OHLC.

Schema v1 objects and pinned manifests remain immutable. Schema v2 is published
under new content hashes and pointers; existing research keeps resolving its
pinned v1 snapshot.

Keeping event time separate from availability time is a look-ahead control.
Historical adapters use the best evidenced publication time and never a value
earlier than `close_time`; live ingestion records observed availability.

### Provider adapters and source gates

Each adapter maps official vendor semantics into schema v2 and must document:

- official endpoint or archive, pagination direction, rate limit, and earliest
  verified timestamp;
- symbol/listing lifecycle and quote-asset identity;
- candle boundary, completion flag, and volume units;
- whether historical checksums, trade count, and taker-side volume exist;
- duplicate, gap, off-grid, OHLC, and non-negative volume validation;
- reproducible raw-response evidence and adapter code version.

Official documentation currently establishes that Binance klines expose base
and quote volumes plus trade count, OKX spot history exposes base and quote
volumes with a completion flag, and Bybit spot klines expose base volume and
quote turnover. Actual historical depth is an observed acceptance result, not
assumed from API documentation.

### Cross-venue Gold products

Cross-venue products are derived datasets, never repairs to Silver. The first
research products will be:

1. venue coverage and freshness matrix;
2. per-candle median close and cross-venue price dispersion;
3. total reported base and quote volume across the eligible venue set;
4. venue volume share and concentration;
5. a robust composite return using only venues that pass the candle's quality
   and quote-asset eligibility gates.

Every composite manifest pins its constituent venue manifests and records the
eligible venue set for every period. A missing venue reduces coverage; it does
not become a zero-price or zero-volume observation. Volume is not interpreted
as unique economic activity across venues, and wash-trading risk remains an
explicit limitation.

Synthetic or proxy-filled candles, if needed for sensitivity analysis, live in
a separate `imputed` research product with method, donor venue, confidence, and
`is_imputed=true`. Strategy baselines use canonical venue data by default.

## Consequences

Research can distinguish market-wide moves from Binance-specific events and can
use both price and liquidity information without corrupting provenance. It adds
schema migration, venue adapters, listing metadata, independent schedulers, and
cross-venue quality monitoring. This ADR does not claim that OKX or Bybit
history is already populated.

The implementation sequence is: publish the schema-v2 contract and tests;
upgrade Binance without mutating v1; inventory and backfill OKX; inventory and
backfill Bybit; then publish cross-venue Gold metrics after overlap and unit
reconciliation pass acceptance.
