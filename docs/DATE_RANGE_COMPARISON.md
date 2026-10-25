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

## Cross-period descriptive statistics (read-only API)

`GET /api/v1/research/date-range-statistics?limit=30`

The endpoint selects the latest `limit` saved date-range records **by creation timestamp**, not automatically the latest 30 distinct calendar weeks. Default 30, allowed 2–100; no market data is fetched and no experiment is rerun. Every selected record is SHA-256 fingerprint checked and must use protocol `btc-date-range-backtest-v4` with valid UTC day boundaries and finite metrics. A bad record aborts the entire response with `409`. The SQLite connection uses read-only `mode=ro`; database unavailable returns `503`.

The response provides: number of selected saved observations, positive/negative/flat **record** counts, mean/median/min/max single-record returns, mean/median/min/max **individual-record** maximum drawdowns, and full verified observations. Zero or one record is labeled `insufficient_records`; no statistics are invented for an empty archive.

Interpretation flags include repeated test periods, pairwise overlapping test intervals, different fee/slippage assumptions, different selected candidates, non-seven-day durations, and whether at least two complete non-overlapping seven-day windows are present. The API deliberately **does not** compound individual returns, report a portfolio-level drawdown, claim statistically independent experiments, rank strategies, or grant PAPER / TESTNET / LIVE trading authority. Even disjoint test windows can share correlated market conditions or substantial overlapping training history. Multiple saved results with identical dates are separate records, not separate weeks.

This API is not authenticated in the current product; keep it private to the Compose network. Related PRs exposing per-record comparison and evidence screening are independent integrations.

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
