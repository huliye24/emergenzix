# ADR-001: Product Baseline and NEMT Migration Boundary

- **Status:** Accepted
- **Date:** 2026-10-24
- **Decision owners:** Emergenzix maintainers
- **Issue:** [#2](https://github.com/huliye24/emergenzix/issues/2)
- **Evidence:** [NEMT migration inventory](../MIGRATION_INVENTORY.md), [product scope](../PRODUCT_SCOPE.md), and repository manifests

## Context

Emergenzix is the product repository created from the NEMT research program. The verified migration inventory identifies a substantial NEMT Next.js 16 / React 19 client and a Python/FastAPI trading application, including authentication, exchange-ordering, and risk modules. It also records that those modules have broader assumptions than the first Emergenzix slice and that their tests are evidence of NEMT behavior, not proof of compatibility here.

The product needs a small, runnable baseline without importing live-execution risk or copying the research repository wholesale. The repository itself is GPL-3.0 licensed. Any transferred source or added dependency therefore needs an explicit provenance, license, and security review.

## Decision

### 1. Build a smaller product shell; do not migrate the application wholesale

Emergenzix will adapt verified concepts and contracts from NEMT while keeping a newly authored, independently testable implementation. Source files may be migrated later only as focused changes after their behavior, tests, security assumptions, and license notices have been reviewed.

This choice applies to both the client and server. It avoids inheriting NEMT branding, mock/demo assumptions, account logic, and exchange-ordering paths before the Emergenzix boundary is established.

### 2. Use the verified web and API technology baseline

- **Client:** a responsive, mobile-first Next.js 16 / React 19 web application. An installable PWA remains optional and requires verification rather than assumption.
- **Server:** Python 3.11+ with FastAPI.
- **Initial persistence:** SQLite for local, single-process development and acceptance tests only. A concurrent production datastore is an open decision.
- **Repository layout:** product API code under `backend/emergenzix_api/`, backend tests under `backend/tests/`, and the product client under `web/`.

These choices are grounded in `web/package.json`, `backend/trade_main.py`, `backend/trade_app/main.py`, and `backend/trade_requirements.txt` listed in the migration inventory. They preserve familiar technology without treating the existing NEMT application as directly portable.

### 3. Keep research and product responsibilities separate

NEMT remains the source of truth for hypotheses, theory, raw research datasets, MATLAB/GPU experiments, and reproducible research evidence. Emergenzix owns the user experience, product APIs, strategy lifecycle, reproducible evaluation, paper-account state, server-side risk controls, audit records, deployment, monitoring, and support.

Emergenzix consumes only reviewed, versioned research artifacts or documented interfaces. It does not import raw research folders by default. A promoted artifact must retain its research revision, dataset and date range, parameters, evaluation method, limitations, review state, and intended use.

### 4. Make DEMO the first runnable acceptance mode

The first runnable acceptance path is **DEMO**: deterministic fixtures, an isolated temporary database, and no exchange credentials. It proves that the product shell, API contracts, strategy validation, backtest persistence, risk rejection, and accounting behavior can be exercised safely.

PAPER is the first operational execution mode and remains simulated. TESTNET requires a separate adapter and acceptance report. **LIVE real-money order execution is explicitly disabled:** no LIVE order endpoint or private exchange-order adapter is authorized by this decision, and no earlier validation stage may enable one automatically.

### 5. Test the boundary, not only the happy path

The initial verification strategy uses repository compilation, backend unit/API tests with isolated SQLite state, client type-check/build checks, and a controlled runtime smoke test. Tests must cover validation and risk failures as well as successful strategy, backtest, and paper-account workflows. NEMT tests may inform cases but do not substitute for Emergenzix tests.

### 6. Preserve GPL-3.0 and third-party license compatibility

Emergenzix remains licensed under GPL-3.0. Before copying source or adding an upstream component, maintainers must record its source URL and version, confirm the upstream license from the source repository, preserve required copyright/license notices, and assess compatibility with GPL-3.0. Missing, unclear, or incompatible licensing blocks migration until resolved.

Prefer a package dependency or pinned submodule when it keeps provenance and the update boundary clearer than copied source. Security and dependency review remain required even when licenses are compatible. The current upstream register is maintained in [UPSTREAM_COMPONENTS.md](../UPSTREAM_COMPONENTS.md).

## Alternatives considered

### Migrate the complete NEMT web and API application

Rejected for the baseline. The inventory shows a useful product-facing implementation, but it also includes NEMT-specific UI assumptions plus authentication and live exchange pathways that require separate review. A wholesale migration would obscure provenance and expand the first acceptance surface.

### Recreate the product with an unrelated stack

Rejected. Next.js/React and FastAPI are present in verified NEMT code and support the required mobile-first client/API split. A new stack would add migration and operational cost without evidence of a product benefit.

### Deliver only a static client

Rejected. Reproducible strategy evaluation, persisted evidence, paper accounting, and server-side risk enforcement require a testable server boundary.

### Connect to a live exchange in the first slice

Rejected. Credential handling, idempotency, uncertain order state, partial fills, reconciliation, rate limits, and financial loss require a separate design and acceptance process. Public market data does not authorize private trading access.

## Consequences

- The first product slice is smaller and easier to verify, but selected NEMT capabilities must be adapted deliberately rather than appearing immediately.
- Research provenance stays visible and research experiments do not silently become production behavior.
- The client and API can evolve independently behind explicit contracts.
- SQLite and the initial DEMO/PAPER workflows are development baselines, not claims of multi-user or production readiness.
- License review is part of migration work, which may block otherwise useful code.
- LIVE execution remains unavailable unless a future ADR and explicit security, operational, legal, and risk approval authorize it.

## Decisions intentionally left open

1. Whether the responsive client should become an installable PWA; offline behavior and update semantics have not been verified.
2. Authentication, authorization, tenant isolation, and public deployment design; the initial shell is not evidence for an Internet-facing service.
3. Production database and migration strategy; SQLite does not establish the concurrency or recovery requirements.
4. The exact adapter boundary for Freqtrade and any exchange TESTNET integration; result and order semantics need comparison first.
5. The long-term research-artifact exchange format between NEMT and Emergenzix; provenance requirements are known, but the transport and schema are not.
6. Whether LIVE trading should ever exist. This ADR does not approve it.

## Verification plan

1. Confirm every NEMT code claim against `docs/MIGRATION_INVENTORY.md` and the cited source paths.
2. From `backend/`, run `python -m compileall -q emergenzix_api tests` and `pytest -q` with isolated test storage.
3. From `web/`, run the repository type-check and production build commands.
4. Exercise the DEMO/PAPER health, strategy validation, deterministic backtest persistence, order-limit rejection, buy/sell accounting, and no-short-selling cases.
5. Inspect the runtime response to confirm the active mode is not LIVE and `live_trading_enabled` is false.
6. Review each later migration as a focused change with provenance, license compatibility, security impact, tests, and rollback notes.
