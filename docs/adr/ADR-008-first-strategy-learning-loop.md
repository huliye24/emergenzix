# ADR-008: First-strategy learning loop

- **Status:** Accepted
- **Date:** 2026-11-04
- **Scope:** First operational product milestone

## Context

Emergenzix has accumulated market-data, backtest, research, PAPER, shadow,
notification, AI-advisory, and dashboard capabilities. That breadth has not yet
produced one continuously observed strategy whose data, decisions, simulated
trades, failures, and outcomes form a complete and reviewable record.

The first product milestone does not need to prove profitability. It needs to
prove that Emergenzix can accumulate trustworthy operating evidence so that a
later strategy version is changed for recorded reasons rather than intuition.

## Decision

Emergenzix will prioritize one 30-day PAPER observation loop:

| Boundary | Selected value |
| --- | --- |
| Market | Binance public spot `BTCUSDT` |
| Timeframe | `4h`, completed candles only |
| Strategy | Turtle/Donchian `20/10`, one immutable version |
| Portfolio | One isolated PAPER account starting at `10,000 USDT` |
| Execution assumption | Signal on a completed candle, fill at the next candle open with recorded fees and slippage |
| Runtime | One authoritative 24/7 worker on the Hong Kong control node |
| Safety | PAPER only; TESTNET and LIVE remain unavailable |

The product must answer five questions from persisted evidence:

1. Which exact strategy version is running?
2. Why did it buy, sell, or hold on the latest completed candle?
3. What are the current position, cash, equity, and risk?
4. How does observed PAPER behavior differ from the pinned backtest assumptions?
5. How many independent completed-candle decisions exist, and is the observation
   gate open for evaluation or modification?

Every completed candle produces at most one immutable decision, including
`HOLD`. A repeated cycle is idempotent. Missing or stale data, an unknown mode,
an inconsistent portfolio, or an unresolved execution state records a failure
and blocks a new simulated action.

## Observation gate

The strategy version and operating assumptions remain frozen until all of the
following are true:

- 30 consecutive calendar days of intended operation have elapsed;
- at least 180 independent `4h` completed-candle decisions are recorded;
- every expected candle is either represented by a decision or an explicit
  failure/gap record;
- restart idempotency and account reconciliation have passed;
- PAPER fills, fees, slippage, positions, cash, and equity reconcile;
- deviations from the pinned backtest are reported.

Reaching the gate permits evaluation; it does not require a strategy change and
does not grant TESTNET or LIVE permission. Emergency safety fixes may be made
earlier, but create a new runtime revision and must preserve the prior evidence.

## Product freeze

Until the observation loop meets its acceptance criteria, the default roadmap
freezes:

- new strategy families, symbols, venues, and shadow fleets;
- GPU or AI models that recommend trading actions;
- automatic strategy promotion or parameter tuning;
- further dashboard redesign beyond what is needed to answer the five questions;
- TESTNET or LIVE execution work;
- migration of the legacy 160-account NEMT experiment.

Security, data-integrity, recovery, and defects that prevent the selected loop
from running remain in scope.

## Runtime responsibilities

- **Hong Kong control node:** the single authoritative PAPER worker, operational
  database, five-question status API, and notifications.
- **AWS Ningxia CPU node:** immutable market-data updates, offline reproduction,
  and scheduled evaluation artifacts. It cannot mutate PAPER runtime state.
- **AWS GPU node:** on-demand research only. It does not run an independent
  Emergenzix product stack for this milestone.
- **S3:** immutable datasets, manifests, runtime snapshots, and evaluation
  artifacts; it is not an execution control plane.

## Consequences

- Existing capabilities remain available but do not define the next milestone.
- The current modular-monolith stack remains; this decision adds no dependency
  and authorizes no stack migration.
- Historical backtest and 24/7 PAPER operation must converge on the same
  versioned signal, sizing, cost, and risk rules.
- Product progress is measured by evidence continuity and reconciliation before
  strategy return.

## Rejected alternatives

- **Continue broad feature development:** rejected because it increases surface
  area without validating the operating evidence loop.
- **Run many strategies to collect more data:** rejected because correlated
  accounts multiply ambiguity before one account is trustworthy.
- **Use profitability as the first acceptance gate:** rejected because a short,
  profitable run can still contain missing decisions, leakage, or accounting
  errors.
- **Let each cloud host run its own product lineage:** rejected because runtime
  evidence would not share one reviewed code and strategy identity.
