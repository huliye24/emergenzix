# Quick historical backtest: capital + period

**Status:** Research-only execution facade over the existing BTC/USDT spot date-range engine. It does **not** implement the proposed S-003 staged entries, pyramiding, futures leverage, or strategy-family router. No exchange orders are sent.

## Two-input launch

From the `backend` directory with its Python environment installed:

```bash
python -m emergenzix_api.quick_backtest_cli --capital 100 --period 1w
```

This freezes a UTC historical window ending at **00:00 UTC today** (last complete UTC date). Historical archive availability is verified separately. An archive not yet published or any missing/unverified 4h candle causes failure, **not** silent fabrication.

To replay a particular past interval:

```bash
python -m emergenzix_api.quick_backtest_cli \
  --capital 100 --period 1w --start 2025-10-21 \
  --output out/btc-2025-10-21-100u.json
```

Or a fixed-day year-scale interval:

```bash
python -m emergenzix_api.quick_backtest_cli \
  --capital 120 --period 1y --start 2022-01-01
```

`1w = 7` days, `1m = 30` days, `1y = 365` days and `3y = 1095` days. Exact custom durations `Nd` are supported for 1–1095 days (for example `14d`, `180d`). **These are fixed-day intervals, not calendar months/years**; leap years do not change their lengths. The end date is exclusive. All dates use UTC.

Long windows fetch and verify many official public archives and run the existing fixed research-candidate comparisons; they can be resource-intensive. The existing Telegram daily table is limited to 30-day reports, but long-horizon results still include the immutable JSON, full equity CSV, trade CSV, selected-candidate metadata and cost-sensitivity exports.

## API: same two required fields

```bash
curl -X POST http://localhost:8000/api/v1/research/date-range-backtests/quick \
  -H 'Content-Type: application/json' \
  -d '{"initial_cash":100,"period":"1w"}'
```

For a reproducible historical run:

```bash
curl -X POST http://localhost:8000/api/v1/research/date-range-backtests/quick \
  -H 'Content-Type: application/json' \
  -d '{"initial_cash":100,"period":"30d","start_date":"2025-10-21"}'
```

The quick endpoint calls the **existing versioned** date-range backtest pipeline and returns its immutable report. It selects among the currently registered simple **long-only BTC spot** research candidates using only pre-test data, then scores the selected candidate on the requested period. The response includes selected strategy, starting capital, end equity, net PnL, total return, drawdown, trades, equity history, fees, verification fingerprints and safety flags.

For now, use a BTC historical strategy benchmark: results do not test the S-003.1 rolling-equity/multi-tranche research strategy. Existing long-only backtest mechanics re-invest available capital after a trade closes, but they do not split each trade into staged tranches or conditionally pyramid. That will be a distinct, versioned engine after the account-ledger work.

## Input and safety contract

- Required: initial capital in USDT (`> 0`, finite, at most 1 billion) and a fixed-day period (`1w`, `1m`, `1y`, `3y`, `Nd`).
- Optional: historical UTC start date `YYYY-MM-DD`. If omitted, select the most recent **completed** UTC date range; do not use unfinished candles.
- An explicit range extending into the current UTC day or future is rejected. Invalid values are rejected before any historical download.
- Underlying research defaults remain unchanged: 90 days of pre-test strategy selection, fee 10 bps per side and slippage 5 bps per side. These are existing benchmark assumptions, **not** exchange guarantees.
- Historical-data checksums, complete contiguous 4h candles, data hashes and immutability constraints remain active. Repeating the same configuration and dataset returns the saved record.
- No funding, market impact, minimum-order/lot-size execution checks, liquidation or futures exposure is represented by this legacy spot engine. Therefore a simulation with 100 USDT is **not yet an executable small-account trading plan**.
- This entrypoint cannot authorize PAPER orders, TESTNET orders or LIVE trading. It never submits any orders or automatically promotes a strategy.
- Larger datasets must fit the source archive's own range and 50,000-bar safety limits. Failure to obtain valid history returns an error and no fabricated result.

## Next isolated engineering tasks

1. Implement **Equity Ledger A**: account equity, spendable cash, completed trade PnL, reserved margin and transaction costs; verify 100→120→132 and 100→80 with synthetic scenarios.
2. Then build a separate `S-003.1` conditional-tranche engine with per-plan **aggregate** risk checks. Never silently replace the simple baseline with new trade rules.
3. Once the new engine is scientifically reviewed, expose a versioned strategy selector in this same quick-run interface, preserving research records for every strategy version.

This task adds a user-friendly entrypoint; it does not claim additional strategy profitability.
