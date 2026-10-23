# ADR-001: Initial Product Backend Baseline

- **Status:** Accepted for the first vertical slice
- **Date:** 2026-10-23
- **Decision owners:** Emergenzix maintainers

## Context

Emergenzix is the independent product repository; NEMT remains the research and experiment repository. The NEMT codebase already contains a Next.js/React frontend and a FastAPI backend, but the existing application is broader than the first Emergenzix product slice and includes exchange-ordering paths that require separate review.

The immediate objective is to create a small, testable product core that can be run and verified by Codex on an AWS development instance without requiring exchange credentials or real funds.

## Decision

1. Implement the first backend slice in Python 3.11+ using FastAPI.
2. Use SQLite for the initial local/development persistence layer. Revisit PostgreSQL before multi-user or production deployment.
3. Keep application code under `backend/emergenzix_api/` and automated tests under `backend/tests/`.
4. Provide strategy management, a deterministic SMA-crossover backtest, backtest history, a paper account, simulated positions, and risk-limited paper orders.
5. Execute backtest signals on the next bar's open after the signal bar has completed; document fees, slippage, and end-of-series position closing in the result.
6. Keep all exchange connectivity out of this initial implementation. The API reports `live_trading_enabled: false`; no live order endpoint is provided.
7. Use a small, independent implementation instead of copying the NEMT application wholesale. Review selected source modules later, one at a time, with explicit license and security checks.
8. Use Next.js 16 and React 19 for the initial responsive dashboard, with Next.js server-side rewrites to the FastAPI API. Treat the UI as an initial development interface; verify type-check/build and AWS connectivity before expanding it.
9. Use GitHub Actions for fast regression checks and the AWS development environment for the user's additional integration/runtime testing.

## Alternatives considered

### Copy the entire NEMT app

Rejected for the first slice because it would import a broad set of product assumptions and high-impact exchange workflows before the new product boundary and test evidence are established.

### Implement only a static frontend

Rejected because the product's core asset is strategy evaluation and recorded trading history. A dashboard without a reproducible backend would not create the first usable research-to-product workflow.

### Connect directly to a live exchange now

Rejected. Exchange integration adds credential, order-state, reconciliation, and financial-risk concerns before the paper workflow has been validated.

## Consequences

- The initial API is easy to run locally and in a controlled cloud environment.
- SQLite is suitable for this single-process development baseline but is not the final choice for a concurrent multi-user production service.
- The first strategy engine is deliberately narrow and is not evidence of profitability.
- Paper-order fills use caller-supplied reference prices; they are simulations, not exchange market prices.
- Authentication, public deployment, exchange adapters, and live trading remain out of scope until separately designed and reviewed.
- The first frontend will be built against this versioned API contract after test feedback.

## Verification plan

- Run `python -m compileall -q emergenzix_api tests`.
- Run `pytest -q` from the `backend/` directory.
- Verify health, strategy validation, backtest persistence, order limits, buy/sell accounting, and no-short-selling behavior.
- Repeat tests in the AWS development environment and record the observed output before treating the baseline as verified.
