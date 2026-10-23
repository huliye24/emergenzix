# Emergenzix Product Scope

## 1. Purpose

Emergenzix is the user-facing quantitative trading product emerging from the NEMT research program. It should make quantitative workflows understandable, reviewable, and safer to operate without turning research experiments directly into live execution.

The product should provide a coherent path from a strategy idea to evaluation, paper trading, and—only after explicit future approval—controlled exchange execution.

## 2. Repository boundary

| Area | NEMT research repository | Emergenzix product repository |
| --- | --- | --- |
| Economic theory and hypotheses | Source of truth | Consumes reviewed conclusions |
| MATLAB/GPU experiments | Source of truth | Consumes versioned results or interfaces |
| Raw research datasets and experiment artifacts | Owned here, subject to data policy | No bulk copying by default |
| User experience and application workflows | Reference material only | Source of truth |
| Strategy configuration and lifecycle | Research may propose candidates | Product owns operational state |
| Backtesting and paper-trading workflow | May supply algorithms/evidence | Product owns user-facing integration and audit |
| Exchange adapters, permissions, risk checks | Not research shortcuts | Product owns operational safety |
| Deployment, monitoring, and user support | Research environment only | Product owns product operations |

A repository split is an ownership boundary, not a claim that all code has already been migrated. Any code transfer requires an inventory and license/security review.

## 3. Initial user jobs

The initial product should help a user:

1. Inspect market data and know whether it is fresh and complete enough to use.
2. Create and version a strategy configuration.
3. Run or review a backtest with visible assumptions, costs, and limitations.
4. Compare in-sample and out-of-sample results without hiding failures.
5. Start, pause, and stop paper trading.
6. Inspect orders, fills, positions, balances, and strategy decisions.
7. Configure risk limits and understand why an action was rejected.
8. Export reproducible reports and audit records.

## 4. Initial delivery principles

- **Mobile-first interface:** optimize for a small-screen workflow. A responsive web/PWA is the initial delivery candidate; native apps require an ADR.
- **Cloud-controlled execution:** credentials and exchange requests stay in trusted server-side components.
- **Explicit operating modes:** DEMO, PAPER, TESTNET, and LIVE must never be confused or silently fall back into one another.
- **Evidence before promotion:** strategies move through defined validation gates.
- **Safe defaults:** no live trading by default; no withdrawals; risk controls are enforced server-side.
- **Readable status:** the interface must distinguish unavailable, stale, simulated, testnet, and live data.

## 5. In scope for early versions

- Product shell and responsive navigation
- Market-data status and data-quality indicators
- Strategy definitions, versioning, and review state
- Backtest job lifecycle and result summaries
- Paper-trading sessions and portfolio/order history
- Server-side risk checks and order previews
- Exchange adapter interface with DEMO first and TESTNET only after acceptance
- Authentication, authorization, audit trails, operational health, and safe configuration
- Exportable reports and reproducible evaluation metadata

## 6. Explicitly out of scope until separately approved

- Unattended real-money trading
- Withdrawal permissions or transfer of funds
- AI-generated strategies automatically promoted to execution
- Claims of guaranteed returns or predictive certainty
- Copying the entire NEMT repository into Emergenzix
- Replacing the research platform's MATLAB/GPU workflows with product runtime code
- Multi-exchange expansion before one adapter has robust tests and reconciliation
- Complex social features, copy trading, leverage, derivatives, or other higher-risk capabilities without a new product/risk review

## 7. Success criteria

The product is progressing when a user can follow an auditable path:

**strategy definition → reproducible evaluation → out-of-sample review → paper trading → monitored testnet validation**

Every transition has a clear status, evidence, owner, and acceptance condition. No transition to LIVE is implied by passing an earlier gate.

## 8. Current status

This document defines intended scope as well as the current implementation boundary. The repository now has a responsive product shell, paper-only API, public OHLCV access through CCXT, and a pinned Freqtrade submodule. Freqtrade is not yet wired into the API's backtest endpoint, and AWS/Codex runtime verification remains outstanding.
