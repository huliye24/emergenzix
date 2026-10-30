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
persistence, API review, and PAPER order intents. Those changes require their
own acceptance tests and do not enable LIVE trading.

## Decreasing pyramiding plan

The first pyramiding slice is also a pure review decision. It accepts an
existing long position only when all of the following are true:

1. current price is above average entry;
2. the protective stop is above average entry and therefore locks profit;
3. a new same-direction signal has been confirmed;
4. the next frozen R-multiple threshold has been reached;
5. conservative expectancy remains positive;
6. trade, fractional-Kelly, portfolio, cluster, and total-notional budgets all
   have remaining capacity.

Default additions are requested at `1R` and `2R`, with decreasing sizes of 50%
and 25% of initial notional. Locked profit can release trade-level risk budget,
but it cannot bypass portfolio or correlation-cluster limits. The planner never
loosens or changes the supplied protective stop and never adds to a losing
position. Its output remains `approved_for_review` and cannot place an order.
