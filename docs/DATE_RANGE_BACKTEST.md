# BTC Date-Range Backtest

## Goal

Run a repeatable historical BTC experiment by specifying a calendar start date and duration. The current protocol is intentionally narrow: Binance BTC/USDT spot, verified 4-hour candles, six fixed SMA-crossover candidates plus one Donchian breakout candidate, long-only simulation, and no exchange orders.

## CLI

From the repository root, install the backend requirements once and run:

```bash
cd backend
python -m pip install -r requirements.txt
python -m emergenzix_api.date_range_backtest_cli \
  --start-date 2025-10-21 \
  --duration-days 7 \
  --training-days 90 \
  --initial-cash 10000 \
  --fee-bps 10 \
  --slippage-bps 5 \
  --output ../data/reports/btc-2025-10-21.json
```

The date is interpreted as 00:00 UTC. The seven-day test interval is half-open: `[2025-10-21T00:00:00Z, 2025-10-28T00:00:00Z)`. With 4-hour candles, the scored period contains 42 candles. The 90-day pre-test window supplies 540 candles for indicator warm-up and candidate selection. The combined content-addressed dataset contains 582 candles when the archive is complete.

The command uses the same `EMERGENZIX_DB_PATH` database as the application unless `--db-path` is supplied. It does not require the API server to be running. If `--output` is supplied, the command writes:

- `*.json`: complete reproducible record with dataset and record fingerprints, selected candidate, validation scores, test-week equity curve, and closed trades.
- `*.md`: human-readable weekly result summary and candidate/trade tables.
- `*.summary.csv`: one-row return and risk summary.
- `*.candidates.csv`: all candidate parameters and pre-test selection metrics.
- `*.equity.csv`: time-stamped simulated equity curve.
- `*.trades.csv`: entry/exit timestamps, prices, fees, quantity, and net PnL.
- `*.cost-sensitivity.csv`: the frozen selected strategy rerun under the fixed fee/slippage stress matrix.

The `data/` folder is ignored by Git so generated reports and local databases are not accidentally committed.

## API

The same workflow is available through the backend API:

```http
POST /api/v1/research/date-range-backtests
Content-Type: application/json

{
  "start_date": "2025-10-21",
  "duration_days": 7,
  "training_days": 90,
  "initial_cash": 10000,
  "fee_bps": 10,
  "slippage_bps": 5
}
```

A successful new run returns HTTP 201. Repeating the same request against identical verified historical data returns the existing record with HTTP 200. The API also supports:

- `GET /api/v1/research/date-range-backtests` — list recent saved reports.
- `GET /api/v1/research/date-range-backtests/{record_id}` — retrieve one report and verify its SHA-256 integrity fingerprint.

The API downloads public historical archive data, validates coverage before persisting a dataset, and stores the result in SQLite. If a candle is missing, duplicated, misaligned, or the range cannot be fully verified, the run fails rather than generating a partial report.

## Candidate selection and test isolation

The candidate universe is versioned in `emergenzix_api/date_range_backtest.py`. Protocol version 3 compares these SMA pairs:

- 3 / 10
- 5 / 15
- 5 / 20
- 7 / 28
- 10 / 30
- 15 / 40

It also compares one Donchian breakout candidate with a 20-bar entry channel and a 10-bar exit channel. The current candle's close is tested against channels built only from earlier completed candles, and any resulting signal executes at the next candle's open.

The first 70% of the pre-test window is used to warm indicators. Candidate scores are calculated on the final 30% of the pre-test window only. The ranking score is:

`net_return_pct - 0.5 × max_drawdown_pct`

Candidates with at least two completed validation trades are preferred. If none meets that minimum, the report still records the top-scoring candidate but marks the selection as `insufficient_validation_trades` and includes a warning. This is a research heuristic, not a claim of statistical significance.

The selected parameters are frozen before the requested test period. The test-week candles are not passed to the candidate-ranking loop. The backtest starts with fresh cash at the evaluation boundary; pre-test bars provide indicator warm-up only. Signals use completed candles and execute at the next candle open, with configured fee and slippage assumptions. Any remaining position is closed at the last test candle's close.

## Three-fold walk-forward evidence

Protocol version 3 also divides the final 30% of the pre-test window into three consecutive folds. Each fold uses an expanding history ending immediately before its evaluation window to rank the same fixed candidate registry. The chosen strategy and parameters are then frozen and scored only on that fold. A fold's evaluation candles are never visible during its selection step.

The report records each fold's selection cutoff, evaluation boundaries, candle counts, selected candidate, evidence status, and metrics. It also reports the median fold return, worst fold maximum drawdown, folds with completed trades, folds meeting the two-trade evidence threshold, and how often each candidate was selected. A fold with fewer than two completed trades is explicitly marked `insufficient_completed_trades`; it is not counted as adequate evidence.

Walk-forward results are diagnostic research evidence. They do not replace the separate final test week and never promote a candidate to paper or live execution.

## Transaction-cost sensitivity

Protocol version 4 freezes the selected strategy before rerunning the same test candles and initial cash under nine cost scenarios: fees of 5, 10, and 20 basis points per side crossed with slippage of 0, 5, and 10 basis points per side. The matrix never re-selects or optimizes the strategy. Each row records net return, net PnL, maximum drawdown, and completed trades using the same execution semantics as the primary result.

The matrix is exported to `*.cost-sensitivity.csv` and summarized in Markdown. It is a stress test for cost fragility, not a profitability guarantee.

## Data and limitations

- Data source: Binance public historical archive; archive checksums are verified by the existing ingestion module.
- Timezone: UTC, including date boundaries.
- Candle interval: fixed at 4 hours in protocol version `btc-date-range-backtest-v4`.
- Completeness: the full pre-test and test interval must contain every expected 4-hour candle.
- Strategy universe: six fixed SMA-crossover candidates and one Donchian breakout candidate, currently long-only.
- Not modeled: futures short positions, funding, leverage, liquidation, hedging, and market impact beyond the configured slippage estimate.
- No private exchange credentials are used. No exchange orders are created. `live_trading_enabled` remains false.

This is the first end-to-end research workflow, not a profitability guarantee. Three folds and a small fixed cost matrix remain limited evidence; next validation steps should include regime-separated results and comparison with buy-and-hold.
