# Emergenzix Architecture

The initial backend decision is recorded in [ADR-001](adr/ADR-001-product-backend-baseline.md). The architecture remains provisional outside that first vertical slice; production and multi-user decisions require further review.

## 1. Current implementation

The first implementation uses Python 3.11+ and FastAPI for the product API, with SQLite for local and single-process development persistence.

Implemented on the initial backend branch:

- Strategy definitions and listing
- A deterministic long-only SMA-crossover backtest (temporary compatibility baseline)
- Public spot OHLCV retrieval through CCXT for an explicit exchange allowlist
- Backtest result JSON that records data source, exchange, symbol, and timeframe
- Freqtrade source pinned as a Git submodule; its engine is not yet wired into the backtest API
- Persisted backtest metrics, assumptions, equity curve, and closed trades
- Paper account, simulated positions, and paper-order history
- Server-side paper-order notional limit, cash check, and prohibition of short selling
- Health endpoint that reports PAPER mode and explicitly reports live trading as disabled

This is the first vertical slice, not a claim of production readiness.

## 2. Logical components

```text
NEMT Research
  theory / datasets / MATLAB / GPU / evaluation evidence
                    |
        reviewed, versioned research artifacts
                    v
Emergenzix Product
  Next.js / React client
          |
     FastAPI API
          |
   +------+--------------------+
   |                           |
strategy/backtest/paper DB   CCXT public OHLCV
   |                           |
paper-only ledger          public exchange data

Separate upstream source (not yet wired into the API):
  vendor/freqtrade -> future isolated backtest / dry-run adapter

No exchange order execution or LIVE endpoint is enabled.
```

This is a logical diagram; planned components are not necessarily implemented.

## 3. Product API

The responsive Next.js/React dashboard consumes the FastAPI API through server-side rewrites. The API validates requests and exposes strategy, backtest, and paper-account workflows. Its interactive OpenAPI documentation is available at `/docs` when the service is running.

The API currently has no authentication and must not be exposed directly to the public internet. Network deployment requires a separately reviewed authentication, authorization, secret-management, and operations design.

## 4. Domain boundaries

- **Market data:** public OHLCV retrieval is available through CCXT. The response records source/exchange/symbol/timeframe, timestamps are UTC, and malformed candles are discarded. Persistent dataset versioning, caching, freshness checks, and complete provenance linkage to immutable datasets remain planned.
- **Strategy:** versioned parameters and provenance. The first strategy type is SMA crossover.
- **Evaluation:** reproducible backtest configuration, costs, metrics, and limitations.
- **Portfolio and orders:** paper-only account, positions, and simulated filled orders.
- **Risk:** server-side notional limits, available-cash checks, and prohibition of short selling in the first paper workflow.
- **Execution:** no exchange execution is implemented. CCXT is used only for public market-data requests; no private endpoint or order-submission method is called.
- **Upstream engine:** Freqtrade is pinned under `vendor/freqtrade`; it is an isolated source dependency only and is not yet the active backtest engine.
- **Audit:** saved backtests and paper-order records exist; a general audit-event subsystem is still planned.

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

- **DEMO:** synthetic or isolated demonstration behavior; a dedicated market-data demo feed is not yet implemented.
- **PAPER:** the only execution mode in the current backend.
- **TESTNET:** not implemented.
- **LIVE:** no endpoint exists; real-money order placement is intentionally unavailable.

Unknown or inconsistent mode state must never silently activate a more permissive mode.

## 7. Data and storage

SQLite is the initial development database. Its single-process scope is intentional; evaluate PostgreSQL or another production database before concurrent multi-user deployment. Local database files and runtime data must remain out of version control.

Never place credentials in source control or client bundles. Logs must be useful for debugging without exposing secrets.

## 8. Reliability and observability

Planned areas include structured redacted logs, request correlation IDs, idempotency, order-state reconciliation, rate-limit handling, operational alerts, and tested recovery behavior. These are not yet implemented by the first slice.

## 9. Decisions still required

1. Implement and verify a Freqtrade adapter in an isolated Python 3.12+ environment or container.
2. Whether to extend the responsive Next.js interface into an installable PWA.
3. Authentication and deployment model.
4. Production database and migration approach.
5. Dataset import format, immutable dataset identifiers, and research-artifact provenance.
6. Paper-account reset and idempotency semantics.
7. First exchange TESTNET adapter and acceptance boundary.
8. Whether LIVE trading should ever be enabled.

Each lasting architectural decision should be recorded in `docs/adr/` and linked from the roadmap.


## 10. Container and release pipeline

The first packaging target is a browser-based web application plus a separate FastAPI container, orchestrated by Docker Compose. The API is not published to the host; the web interface binds to localhost by default. SQLite uses the named emergenzix-data volume so the database is not tied to a disposable container. See [ADR-002](adr/ADR-002-container-and-release-pipeline.md) and [release operations](RELEASE.md).

GitHub Actions builds the API and web images on relevant changes. A version tag publishes both images to GitHub Container Registry and creates a GitHub Release. This workflow does not deploy to AWS. Authentication, reverse proxy/TLS, public network exposure, and AWS deployment credentials remain unresolved and must be addressed before any public deployment.
