# Strategy, position, and portfolio decision module (domains 4-6)

**Status:** initial pure PAPER decision slice implemented; no API or order integration.

This module consumes an already-reviewed signal. Indicators and signals describe
possibility; they do not grant permission to trade. The module answers whether
the strategy has conservative positive expectancy, how much stop risk one trade
may consume, and whether aggregate portfolio and correlation-cluster limits have
enough headroom.

For conservative out-of-sample win probability `p` and net payoff ratio `b`:

```text
robust_expectancy_R = p * b - (1 - p)
full_Kelly = max(0, robust_expectancy_R / b)
```

The approved currency risk is the minimum of:

1. maximum per-trade risk;
2. fractional-Kelly risk;
3. remaining aggregate portfolio stop-risk budget;
4. remaining correlation-cluster stop-risk budget.

The existing cost-aware position calculator then converts the approved risk to
quantity using entry price, protective stop, fees, slippage, cash, and maximum
notional. Its output is `approved_for_review`, never executable permission.

The caller remains responsible for proving that probability is calibrated from
unseen rolling windows, payoff is net of realistic costs, the signal and market
data are fresh, and current positions are reconciled. Unknown inputs fail closed.
Breakout, Bollinger, MACD, volume, NEB, and NSW remain upstream signal evidence;
none is hard-coded into domains 4-6.

Future micro-tasks may add short/futures modeling, correlation estimation,
pyramiding-plan integration, persistence, API review, and PAPER order intents.
Those changes require their own acceptance tests and do not enable LIVE trading.
