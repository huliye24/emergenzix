# Saved Date-Range Backtest Comparison

## Purpose

The comparison CLI reads two or more already persisted date-range backtest records without downloading candles or rerunning a strategy. It verifies every stored result fingerprint before displaying any metrics.

Run it from `backend/`:

```bash
python -m emergenzix_api.date_range_compare_cli \
  RECORD_ID_1 RECORD_ID_2 \
  --db-path ../data/emergenzix.db \
  --output ../data/reports/comparison.csv
```

`--output` is optional and must end in `.csv`. Without it, the verified table is printed only to stdout.

## Reported fields

Each observation includes:

- Record ID and protocol version
- Dataset SHA-256 fingerprint
- Test start and end timestamps
- Fee and slippage assumptions per side
- Selected candidate and strategy type
- Net and benchmark returns
- Maximum drawdown
- Completed trades, win rate, and total fees

The command preserves the requested record order. Missing IDs, invalid fingerprints, inconsistent protocol metadata, malformed JSON, and unsupported record shapes fail the entire comparison rather than emitting a partial table.

## Interpretation and safety

Rows are labeled as independent saved observations. Different test periods or transaction-cost assumptions are called out explicitly and are never presented as a controlled head-to-head experiment. The command does not rank candidates, choose a winner, promote a strategy, download market data, update the database, or create paper/live orders.

The database is opened in SQLite read-only mode. CSV creation affects only the explicitly requested output path.
