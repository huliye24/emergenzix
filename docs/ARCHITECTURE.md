# Emergenzix Architecture

**Status: provisional.** This document describes boundaries and safety invariants, not a final technology selection. The initial stack and migration approach must be recorded in an Architecture Decision Record after inspecting the current NEMT application.

## 1. Logical components

```text
NEMT Research
  theory / datasets / MATLAB / GPU / evaluation evidence
                    |
        versioned, reviewed strategy artifacts
                    v
Emergenzix Product
  responsive client
       |
  authenticated product API
       |
  strategy + evaluation + portfolio domain
       |
  risk and permission boundary
       |
  execution coordinator
       |
  exchange adapter interface
       |
  DEMO / PAPER -> TESTNET
                  LIVE disabled by default
```

This is a logical diagram, not a claim that these components have been implemented.

## 2. Client

The initial interface should be mobile-first and responsive. A web/PWA is the default candidate because it can serve desktop and mobile users without maintaining separate clients at the beginning. A native mobile application requires a written ADR showing why a web/PWA is insufficient.

The client presents state and requests actions. It must not be the sole place where authorization, risk limits, or trading-mode restrictions are enforced.

## 3. Product API

The API is responsible for authenticated requests, authorization, input validation, business rules, and stable interfaces between the client and server-side domain services.

Expected responsibilities include:

- Strategy definitions and versions
- Backtest job submission and result retrieval
- Paper-trading sessions
- Portfolio, order, fill, and position views
- Risk configuration and order preview
- Audit history and operational health

These are target responsibilities; implementation status must be tracked separately.

## 4. Domain boundaries

- **Market data:** source, timestamp, freshness, completeness, and quality state.
- **Strategy:** versioned parameters, dependencies, status, and provenance.
- **Evaluation:** reproducible backtest configuration, costs, metrics, and limitations.
- **Portfolio and orders:** explicit lifecycle states and reconciliation.
- **Risk:** server-side pre-trade checks, limits, exposure, and rejection reasons.
- **Execution:** mode-aware orchestration, idempotency, timeouts, and recovery.
- **Audit:** who or what requested an action, which checks ran, and the observed result.

Keep exchange-specific behavior behind adapters; keep product domain rules independent from any single exchange API.

## 5. NEMT integration

NEMT remains the source of truth for research experiments. Emergenzix should consume reviewed, versioned artifacts or a documented interface rather than importing raw research folders into the product runtime.

Every imported result should identify, where applicable:

- Research commit or artifact version
- Dataset/version and date range
- Experiment configuration and parameters
- Evaluation method and limitations
- Timestamp and provenance
- Review state and intended use

A result that lacks sufficient provenance must not be treated as production-ready evidence.

## 6. Operating modes

Modes must be explicit and non-interchangeable:

- **DEMO:** local or isolated synthetic behavior.
- **PAPER:** simulated execution against selected market data.
- **TESTNET:** exchange-provided test environment with dedicated credentials.
- **LIVE:** real funds; disabled by default and unavailable until separately approved.

Configuration errors must not cause a fallback from TESTNET to LIVE or from LIVE to an unintended mode. Unknown or inconsistent mode state must fail closed.

## 7. Data and storage

The implementation must choose storage only after identifying the required entities and consistency guarantees. Order state, strategy versions, audit events, and execution idempotency require durable and testable semantics. Large research artifacts should remain in the research/data infrastructure and be referenced by version rather than copied wholesale.

Never place credentials in source control or client bundles. Logs must be useful for debugging without exposing secrets.

## 8. Reliability and observability

At minimum, the design should account for:

- Health/readiness checks
- Structured, redacted logs
- Correlation IDs for order workflows
- Idempotency for mutation requests
- Explicit unknown/pending order states
- Reconciliation after disconnects and restarts
- Rate-limit and timeout handling
- Auditable risk rejections
- Tested shutdown and recovery behavior

## 9. Decisions still required

Do not silently decide these in code:

1. Initial client/API technology baseline.
2. Whether to migrate the existing NEMT app or recreate a smaller shell.
3. Database and job-queue requirements.
4. Authentication and deployment model.
5. First exchange adapter and the exact TESTNET acceptance boundary.
6. How research artifacts are versioned and delivered to the product.

Each decision with lasting architectural impact should be recorded in `docs/adr/` and linked from the roadmap.
