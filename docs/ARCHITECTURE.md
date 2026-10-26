# Emergenzix Architecture

The initial product and migration decision is recorded in [ADR-001](adr/ADR-001-product-baseline-and-migration.md). The architecture remains provisional outside that first vertical slice; production and multi-user decisions require further review.

## 1. Current implementation

The first implementation uses Python 3.11+ and FastAPI for the product API, with SQLite for local and single-process development persistence.

Implemented on the initial backend branch:

- Strategy definitions and listing
- A deterministic long-only SMA-crossover backtest (temporary compatibility baseline)
- Public spot OHLCV retrieval through CCXT for an explicit exchange allowlist
- Backtest result JSON that records data source, exchange, symbol, and timeframe
- Date-driven BTC range backtests that select from a versioned SMA and Donchian candidate registry using only pre-test validation candles, then score the requested holdout period and a fixed fee/slippage sensitivity matrix
- Immutable date-range run records tied to content-addressed Binance 4h datasets, with SHA-256 integrity checks and JSON/Markdown/CSV exports
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

- **Market data:** public OHLCV retrieval is available through CCXT. The response records source/exchange/symbol/timeframe, timestamps are UTC, and malformed candles are discarded. Validated OHLCV payloads can be registered as immutable content-addressed datasets; canonical SHA-256 IDs identify the exact source metadata and candle sequence. Backtests can consume an immutable dataset ID and persist its SHA-256 fingerprint and range in result provenance. A read-only dataset quality endpoint reports aligned interval gaps, irregular spacing, coverage when spacing is consistent, and a freshness heuristic. Public OHLCV responses use a bounded, process-local TTL cache (30 seconds and 256 entries by default); the key includes exchange, normalized symbol, timeframe, and limit. Only successful responses are cached, and TTL zero disables caching. This cache is not persistent or shared across processes. Broader completeness checks remain planned. Dataset source metadata is caller-declared at registration and is not an independent authenticity attestation.
- **Strategy:** immutable parameter snapshots in `strategy_versions`, monotonically increasing version numbers, canonical parameter JSON, and SHA-256 fingerprints. New backtests pin the selected version ID; historical backtests are not retroactively assigned a version when their original parameters are unknowable. The first strategy type is SMA crossover.
- **Evaluation:** reproducible backtest configuration, costs, metrics, and limitations. The date-range BTC workflow fetches verified Binance 4h archives, requires gap-free training and test intervals, ranks a versioned registry of SMA and Donchian candidates on the pre-test validation slice, freezes the chosen strategy and parameters, and reports the separate test interval. The CLI can write JSON, Markdown, summary/candidate/equity/trade CSV files. These reports are research evidence only; candidate selection does not promote a strategy to paper execution. Strategy comparisons evaluate 2–10 immutable versions against one registered content-addressed dataset and shared cash/fee/slippage settings. Chronological validation pins one version, scores the in-sample prefix separately from a later holdout, uses pre-split candles only as indicator warm-up for holdout signals, and persists both metric sets with dataset/version provenance. Holdout results must not be fed back into parameter selection.
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


## 11. 三层职责：术 / 道 / 容器

架构责任已确立为三个概念层，详见 [ADR-003](adr/ADR-003-strategy-system-software.md)：

- **交易策略（术）**：研究具体市场状态、机会、入场/分批和失效退出，向系统输出条件计划；不拥有账户权益或下单权。
- **交易系统（道）**：统一管理组合资金、权益账本、风险、策略冲突、实际持仓与审计，不让每个策略自行改账本。
- **交易软件（容器）**：CLI/API/Web/Telegram、数据库、Docker/AWS；只提供相同的参数入口、运行与追溯，不改变底层交易规则。

其中纯研究的策略规范在独立 NEMT 仓库；可执行系统与软件容器均在 Emergenzix 仓库。新增 `equity_ledger.py` 是**只处理已平仓净损益的不可变账本组件**，包含费用、外部资金流与资金守恒检查；它尚未接入当前日期区间回测，也不处理开放仓位市值、实时保证金或多批次委托。不要据此声称 S-003.1 已完成或真实账户可交易。
