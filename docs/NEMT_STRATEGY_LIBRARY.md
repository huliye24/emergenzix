# NEMT Strategy Library

## Status

- **Implemented:** reviewed NEMT v4/v4.4 research modules are packaged under
  `backend/emergenzix_api/nemt_strategy`.
- **Default cloud research strategy:** `nemt-v4.4-regime-router-monthly-001`.
- **Execution boundary:** `BACKTEST_ONLY`; LIVE is hard-disabled and no exchange
  order adapter is imported by the package.
- **Legacy baseline:** SMA/Donchian remains available only when explicitly
  requested for comparison. It is not the NEMT default.

## Provenance

| Item | Value |
| --- | --- |
| Research repository | `huliye24/nemt-simulator` |
| v4.4 implementation commit | `e764247` |
| Validation commit | `3de60ef` |
| Router protocol | `nemt-v4.4-regime-router-monthly-001` |
| Validated evidence | `NEMT_V44_MONTHLY_REGIME_2024_2026.json` |
| Market assumptions | Binance BTC/USDT, 4h, Asia/Shanghai decision boundaries |
| Costs | 5 bps fee and 2 bps slippage per side |

The imported Python modules are a focused transfer from the same GPL-3.0 NEMT
project. Raw datasets, notebooks, GPU artifacts, MATLAB experiments, credentials,
and unrelated research files are not included.

## Strategy behavior

The v4.4 adapter uses causal monthly router decisions to select among:

- `v4a`: two-stage risk-budgeted pyramiding with whole-position break-even
  protection;
- `v4b`: two-stage risk-budgeted pyramiding with ATR trailing protection;
- `v4c`: no pyramiding, trend-following exit control.

The underlying strategy keeps one non-overlapping account position, supports long
and short research simulations, limits stop risk to 2% of equity, and uses 3x or
5x modeled leverage. Annual trend is retained only as a research label and does
not directly control direction or size.

## Reproducibility

Each report records:

- strategy ID and semantic version;
- research and validation commits;
- canonical parameter SHA-256;
- validation-evidence SHA-256;
- canonical OHLCV data SHA-256 in each monthly child run;
- child and aggregate report SHA-256 values.

Run a reviewed, month-aligned historical interval with:

```bash
cd backend
python -m emergenzix_api.nemt_strategy.v44_adapter \
  --start 2026-01-01 --end 2026-06-01 --capital 100 \
  --output-dir data/reports/nemt-v44-2026-01-to-05
```

The end date is exclusive. A month without a reviewed router decision fails
closed instead of silently selecting SMA/Donchian or an unreviewed NEMT variant.

## Interpretation limits

This is historical simulation evidence, not a profitability guarantee. Funding,
liquidation tiers, execution latency, and order-book depth are not modeled. A
successful backtest cannot authorize PAPER, TESTNET, or LIVE promotion; those
remain separate lifecycle decisions.
