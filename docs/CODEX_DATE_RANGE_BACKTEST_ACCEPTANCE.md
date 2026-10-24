# Codex Acceptance Checklist — BTC Date-Range Backtest

## Scope

Validate the implementation of the date-driven BTC historical backtest. Prefer focused fixes over refactors. Do not connect a cloud server or Telegram bot for this acceptance pass, and do not enable or test live order placement.

## 1. Static and automated checks

From the repository root:

```bash
cd backend
python -m pip install -r requirements.txt
python -m compileall -q emergenzix_api tests
pytest -q
```

Record the actual command output. Do not report success if a command was skipped or failed.

## 2. Real historical-data smoke test

Run one historical week against Binance's official public archive. No API key or Telegram token is needed:

```bash
python -m emergenzix_api.date_range_backtest_cli \
  --start-date 2025-10-21 \
  --duration-days 7 \
  --training-days 90 \
  --initial-cash 10000 \
  --fee-bps 10 \
  --slippage-bps 5 \
  --output ../data/reports/btc-2025-10-21.json
```

If the network or archive provider is unavailable, capture the sanitized error and stop the real-data acceptance step. Do not replace the historical data with synthetic data and describe it as real market history.

## 3. Inspect the result artifacts

Verify the command produces:

- `../data/reports/btc-2025-10-21.json`
- `../data/reports/btc-2025-10-21.md`
- `../data/reports/btc-2025-10-21.summary.csv`
- `../data/reports/btc-2025-10-21.candidates.csv`
- `../data/reports/btc-2025-10-21.equity.csv`
- `../data/reports/btc-2025-10-21.trades.csv`

Inspect the JSON and confirm:

- The requested test interval is 2025-10-21 00:00 UTC through 2025-10-28 00:00 UTC, end-exclusive.
- There are 540 pre-test candles and 42 test candles when the 90-day training window is complete.
- Every candle interval is present; a gap must fail the run rather than create a partial report.
- The selected candidate and its score are based only on the pre-test validation window.
- Test metrics include net PnL, net return, benchmark return, maximum drawdown, trade count, win rate, estimated fees, equity curve, and closed trades.
- The record includes dataset and result SHA-256 fingerprints.
- `orders_created` and `live_trading_enabled` are false. No paper order rows are created by the research run.

## 4. Idempotency and isolation

Run the exact same command a second time.

Acceptance criteria:

- The CLI reports `already_exists`.
- The record ID and record SHA-256 are unchanged.
- The persisted result remains unchanged.
- The report does not create or modify paper orders.
- No real exchange credentials, private exchange endpoints, Telegram credentials, or cloud resources are used.

## 5. Final review

- Inspect `git diff` and `git status --short`; local database/report files under `data/` must remain ignored and must not be committed.
- Review any changes made to the candidate grid or execution semantics. If the protocol's result semantics change, increment `DATE_RANGE_BACKTEST_PROTOCOL_VERSION`.
- Report the exact commands, observed outcomes, remaining limitations, and any fixes in the PR.
- Do not merge or deploy until the relevant GitHub Actions checks pass and the real-data smoke test is documented.

## Known scope limits

The current protocol is BTC/USDT spot, 4-hour candles, long-only SMA crossover and Donchian breakout candidates. It does not model futures shorts, funding, leverage, liquidation, hedging, market impact beyond the configured slippage estimate, or guarantee profitability. A successful historical run is research evidence only and does not promote a strategy to paper execution or LIVE.
