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

## Read-only HTTP API

The API exposes the same verified comparison through:

`GET /api/v1/research/date-range-comparisons?record_ids=RECORD_ID_1&record_ids=RECORD_ID_2`

Supply **2–10 unique** lowercase 64-character record IDs as repeated `record_ids` query parameters. Order is preserved. The response uses the CLI engine's independent-observation schema, including `different_test_periods`, `different_cost_assumptions`, `controlled_head_to_head: false`, and `automatic_winner_selection: false`.

- `200`: all records loaded and fingerprints verified; no new experiment is run.
- `422`: invalid, duplicated, or out-of-range record IDs.
- `404`: at least one saved record does not exist.
- `409`: stored fingerprint, protocol, or record structure cannot be trusted.
- `503`: the saved-record database is unavailable.

This is a **read-only API**. It opens SQLite in read-only mode, has no POST or execution path, and never selects an automatic winner. The current application API has no authentication: keep port 8000 private and do not publish this endpoint directly to the public internet. A Telegram `/compare` command is a separately planned integration, not part of this API change.

For review, run `cd backend && python -m pytest -q tests/test_date_range_comparison.py` followed by the complete backend suite.
