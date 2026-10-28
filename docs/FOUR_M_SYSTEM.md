# 4M System v0.1 — Research-only architecture

**Status:** experimental scaffold. **Execution:** no orders, no network IO, no exchange credentials, no account writes. This module is NOT an approved trading strategy.

## Ownership and boundaries

- NEMT repository: economic hypotheses, preregistration, theoretical derivations and evidence.
- Emergenzix `four_m`: deterministic implementation of reviewed feature-to-state rules.
- Existing paper/backtest engines: independent execution and financial simulation, **not connected to 4M v0.1**.
- AWS and Telegram: deployment and notification infrastructure; this change configures neither.

## Four layers

| Layer | Inputs | Output | Cadence (proposed) |
| --- | --- | --- | --- |
| M1 Macro | policy, 10Y real yield, DXY, liquidity: directional votes | positive / neutral / negative / unknown | on official publication |
| M2 Market Regime | long-horizon trend, valuation, holder/ETF flows | accumulation / expansion / distribution / contraction / unknown | daily/weekly |
| M3 Mapping | M1 + M2 + completed-candle price trend | aligned scenario / wait | closed 4h bars |
| M4 Management | data freshness and external risk-veto result | review_long / review_short / wait / halt | every evaluation |

M1's four equal-weight votes are -1, 0, +1 and require complete point-in-time coverage. A score >= 0.5 is positive; <= -0.5 negative; otherwise neutral. **All thresholds and categorical definitions are hypotheses.** Their predictive ability has not been established.

M2's initial deterministic regime mapping uses long-horizon trend and two demand votes. It is a transparent placeholder, not a validated NEMT regime estimator. Valuation -1 means cheap, +1 expensive. Conflicts return unknown. M3 authorizes only *review labels* with matching price direction. M4 vetoes stale data or failed risk checks irrespective of other layers.

## Point-in-time contract

`Snapshot.as_of` is a timezone-aware observation time. The caller must ensure each feature was actually *available by that time*, preserve source, original release time, revision/vintage, retrieval time, currency, calculation version and market/contract identity. No feature loader is included. Never forward-fill unknown releases as known; missing values remain `None` and fail closed.

Price trend must come from **completed** candles, computed without using the active bar. Macro signals must not assume a change in interest-rate policy is universally bullish/bearish; independently backtest interactions with growth shocks.

## Safety contract

- `assess` is pure and returns immutable objects.
- `REVIEW_LONG` and `REVIEW_SHORT` are *research interpretations*, never executable permissions.
- Data staleness or risk veto causes `HALT`; incomplete evidence produces `WAIT`.
- The existing spot paper-order API is not a perpetual futures simulator; 4M must not call it to simulate leverage, shorting, funding or liquidation.
- No router, worker, watcher, Telegram handler or Docker service imports this module in v0.1.
- No API keys or tokens may be written to this repository. Keep all production execution disabled.

## Validation and promotion gates

Before integrating into any paper execution path:
1. Define precise feature formulas, data sources and release/vintage policy in NEMT.
2. Build timestamped USD-M futures datasets with mark-price, funding, fees and exchange trading constraints.
3. Unit-test boundaries and time leakage; run fee/slippage/funding-sensitive walk-forward evaluation with untouched holdouts.
4. Compare against buy-and-hold, simple trend and no-trade baselines; report uncertainty, trade counts and regime coverage.
5. Review and version a promotion decision. PAPER execution requires a separate PR and safety review.
6. LIVE trading is explicitly outside this project milestone and must never be inferred from successful backtests.

## Python API example

```python
from datetime import datetime, timezone
from emergenzix_api.four_m import Snapshot, assess
from emergenzix_api.four_m.engine import Trend

snapshot = Snapshot(
    as_of=datetime(2026, 10, 28, tzinfo=timezone.utc),
    macro_votes=(1, 1, 0, 1),
    market_trend=1,
    valuation=0,
    holder_flow=1,
    etf_flow=1,
    price_trend=Trend.UP,
    data_fresh=True,
    risk_ok=True,
)
print(assess(snapshot))  # review label only, NOT an order
```

Tests: `cd backend && pytest -q tests/test_four_m.py`.

## AWS deployment

No runtime deployment required for this code-only addition. After CI and PR review, Codex may deploy a pinned image version using the existing documented Compose workflow, while retaining PAPER and localhost-only access. Do not run `docker compose down -v` (persistent data loss). Verify current AWS environment and backups before making changes.
