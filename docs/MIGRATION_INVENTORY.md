# NEMT Application Migration Inventory

- **Status:** scoped inventory complete; no application code was copied or changed
- **Source repository:** [huliye24/NEMT-Simulator2](https://github.com/huliye24/NEMT-Simulator2)
- **Evidence snapshot:** `8582b66f3bff4b2aa6d2433399c6d3c4452761c9` (`origin/main`, inspected 2026-10-24)
- **Scope:** product-facing `web/`, `backend/trade_app/`, entry point, app tests, CI, and baseline acceptance documents

This inventory supports [ADR-001](adr/ADR-001-product-baseline-and-migration.md). It is not a full audit of NEMT and does not approve any source transfer, dependency, credential, or exchange integration.

## Verified product-facing inventory

| Source path | Current responsibility | Key dependencies | Existing tests or verification | Recommendation | Security, configuration, and license checks |
| --- | --- | --- | --- | --- | --- |
| `web/package.json`, `web/package-lock.json` | Next.js 16 / React 19 mobile-first web/PWA build and locked dependency graph | Next.js 16.3.2, React/React DOM 19.2.8, TypeScript 5, Tailwind CSS 4, ESLint 9 | `.github/workflows/nemt-app-ci.yml` runs `npm ci`, lint, `tsc --noEmit`, and production build | **Adapt** the verified stack and build gates; do not copy the application wholesale | Review the lockfile and transitive licenses/vulnerabilities at the migration revision; preserve GPL-3.0 compatibility; do not expose server secrets in client bundles |
| `web/src/app/page.tsx` | Server-rendered dashboard entry that loads market state, symbols, report, and portfolio | Next.js App Router; `@/components/EmergentixHome`; `@/lib/api` | Covered by the web lint/type-check/build gate; baseline acceptance records responsive build verification | **Adapt** the composition and loading-boundary ideas to Emergenzix contracts | Existing branding and data contracts are NEMT-specific; verify error/loading behavior and avoid assuming mock data is live |
| `web/src/components/EmergentixHome.tsx` | NEMT dashboard presentation and navigation | React hooks, Next.js `Link`, NEMT-specific types and props | Web CI; `docs/NEMT_APP_TESTNET_ACCEPTANCE.md` records 390 px, Android, PWA, offline, and screenshot checks | **Investigate** individual interaction patterns; do not bulk-copy | Revalidate accessibility, status labels, offline behavior, and DEMO/PAPER/LIVE distinctions; inspect any assets and notices before reuse |
| `web/src/lib/api.ts` | Async data-access facade currently backed by checked-in JSON fixtures, with a comment describing future FastAPI calls | Local files under `web/src/data/`; NEMT type definitions | Type-check/build only; no evidence in this file of a live API client contract | **Adapt** the interface shape only after defining Emergenzix endpoints | Treat fixture results as DEMO data, not exchange truth; investigate caching, authentication, CSRF, error handling, and stale-data semantics |
| `backend/trade_main.py`, `backend/trade_requirements.txt` | Uvicorn entry point and isolated app dependency set | Python 3.11, FastAPI, Uvicorn, HTTPX, pytest | CI installs the manifest, runs pytest, starts a DEMO process, and checks health/readiness | **Migrate concepts** for process/config boundaries; maintain an Emergenzix-owned manifest | Default binding is loopback; repeat dependency license/vulnerability review; keep runtime and test dependencies separated; never migrate `.env` values |
| `backend/trade_app/main.py`, `schemas.py`, `errors.py` | FastAPI assembly, API schemas/errors, sessions/RBAC/CSRF dependencies, system and trading routes | FastAPI plus all `trade_app` services | `backend/tests/test_trade_app.py`; DEMO health smoke; baseline acceptance evidence | **Adapt selectively** behind an Emergenzix-owned API contract | Broad module includes high-impact mutations; review CORS, cookies, CSRF, request logging, redaction, authorization, and route-by-route behavior before reuse |
| `backend/trade_app/config.py`, `db.py` | DEMO/TESTNET/LIVE configuration gates and SQLite schema/persistence | Python stdlib (`os`, `secrets`, `sqlite3`), local security helpers | `test_baseline.py` verifies LIVE disabled, unknown mode fail-closed, and DEMO readiness; CI checks safe sample defaults | **Adapt** fail-closed mode/config patterns; design Emergenzix persistence separately | Do not copy credentials or database files; review migrations, concurrency, backups, session secrets, production datastore needs, and environment isolation |
| `backend/trade_app/auth.py`, `security.py` | Sessions, password hashing/verification, CSRF material, order/token IDs, and redaction | Python `hashlib`, `hmac`, `secrets`; local database/config | Login/logout, invalid credentials, RBAC/CSRF, TESTNET operator, audit-redaction, and rate-limit tests in `test_trade_app.py` | **Investigate**; do not migrate authentication code without a dedicated security decision | Require threat modeling and modern password/session review; verify cookie flags, rate limits, rotation, revocation, audit privacy, and dependency-free crypto assumptions |
| `backend/trade_app/market.py`, `accounts.py` | DEMO market/account models plus public Binance market data and optional server-side account reads | HTTPX; local config/database; `BinanceSpotAdapter` | Parser/filter, stale/fail-closed, account, and system-state tests; real TESTNET account verification remains incomplete | **Adapt** DEMO/public-data behavior; **investigate** private account access | Keep keys server-side and withdrawals forbidden; distinguish forming/stale data; add provenance, rate limits, retry policy, and venue acceptance evidence |
| `backend/trade_app/risk.py`, `orders.py`, `admin.py`, `binance_spot.py` | Risk preview/recheck, idempotent order state, admin pause/cancel, and signed Binance Spot adapter | HTTPX, HMAC/SHA-256, SQLite services, schemas/config | Tests cover oversized rejection, pause race/restart, idempotency, reserve/cancel, audit, signing, and unknown timeout outcome; real TESTNET loop, partial fills, and reconciliation are not accepted | **Leave disabled and investigate** module by module; no direct migration into an active execution path | Financially high impact. Require TESTNET-only acceptance, least-privilege keys with no withdrawal, reconciliation, duplicate prevention, partial fills, timeouts, 429/5xx behavior, kill switch, audit, and separate LIVE approval |
| `backend/tests/test_baseline.py`, `backend/tests/test_trade_app.py`, `backend/tests/conftest.py` | Safety, API, DEMO ledger, adapter-mock, failure-path, and fixture coverage | pytest, FastAPI TestClient/HTTPX, temporary SQLite paths | 23 backend tests were recorded in `docs/NEMT_APP_BASELINE_ACCEPTANCE.md`; exact current results must be rerun at the pinned revision | **Migrate test cases as specifications**, then rewrite against Emergenzix behavior | Tests are evidence, not a security audit or compatibility proof; never reuse real credentials or private data in fixtures |
| `.github/workflows/nemt-app-ci.yml` | Backend tests and DEMO smoke, web checks, Ruff baseline, Compose/Redis health, and a scoped secret/default guard | GitHub Actions, Python 3.11, Node 22, Docker Compose, Ruff 0.6.9 | Workflow definition is present at the pinned revision; historical acceptance cites green checks | **Adapt** the safety gates to Emergenzix paths and manifests | The grep secret guard is narrow and not a substitute for full secret scanning; pin/review action versions and avoid secrets in pull-request jobs |
| `docs/NEMT_APP_BASELINE_ACCEPTANCE.md`, `docs/NEMT_APP_TESTNET_ACCEPTANCE.md` | Recorded architecture/runtime evidence, mode matrix, known gaps, and TESTNET gate status | Repository implementation and external/manual verification noted in each document | Baseline records 23 pytest tests and web checks; TESTNET document marks real-account/order, partial-fill, and reconciliation evidence incomplete | **Migrate as evidence references**, not as product claims | Preserve dates and qualification levels; repeat checks in Emergenzix. These documents explicitly do not authorize LIVE trading |
| NEMT research areas outside the table, including `matlab/`, root `nemt_*.py`, raw datasets, GPU artifacts, and experiments | Research hypotheses, numerical experiments, datasets, and research infrastructure | Mixed research/tooling stacks; not audited in this task | Not inspected beyond the documented boundary | **Leave in NEMT** | Transfer only reviewed, versioned artifacts with provenance and data/license approval; do not copy folders wholesale |

## Smallest viable Emergenzix baseline

The smallest viable product baseline is not the complete NEMT application. It consists of:

1. A responsive Next.js/React shell with Emergenzix-owned navigation and API contracts.
2. A small FastAPI service with health/readiness endpoints and explicit DEMO/PAPER mode reporting.
3. Deterministic DEMO fixtures and an isolated temporary SQLite database for acceptance tests.
4. Strategy definition, reproducible backtest persistence, a simulated paper account, and server-side risk rejection.
5. CI gates for backend tests, DEMO smoke, frontend lint/type-check/build, safe configuration defaults, and secret hygiene.

Exchange credentials, private account access, and order submission are not part of this baseline. PAPER remains simulated; TESTNET requires a separate acceptance task; LIVE real-money order execution remains disabled.

## Repository boundary

- **NEMT owns:** theory, hypotheses, raw research datasets, MATLAB/GPU experiments, research code, and reproducible research evidence.
- **Emergenzix owns:** product UX, API contracts, strategy lifecycle, evaluation records, paper state, risk enforcement, audit, deployment, and operations.
- **Transfer rule:** consume a reviewed artifact or migrate one focused module only after recording its source revision, license, security impact, configuration, tests, and limitations.

## Unresolved questions

1. Which NEMT UI interaction patterns are valuable after replacing mock JSON contracts with Emergenzix APIs?
2. Should any NEMT authentication code be reused, or should Emergenzix adopt a separately reviewed identity provider/session design?
3. What production datastore and migration tooling will replace the single-process SQLite baseline?
4. What canonical schema carries NEMT research provenance, dataset identity, parameters, limitations, and review status into Emergenzix?
5. Which market/account adapter behavior belongs in Emergenzix versus an isolated upstream engine such as Freqtrade?
6. What TESTNET evidence is required for partial fills, reconciliation, restart recovery, rate limits, and unknown order outcomes?
7. Which third-party notices are required for any selected source files, UI assets, or package dependencies beyond the repositories' GPL-3.0 licenses?
8. Should LIVE trading exist at all? This inventory does not authorize it.

## Verification notes

- Every table path was confirmed in the NEMT source tree at the evidence snapshot.
- Package manifests, test names, CI workflow, and acceptance documents were read directly from that snapshot.
- NEMT and Emergenzix both contain GPL-3.0 license texts; file-level notices and third-party dependency compatibility still require review for each actual transfer.
- No source file, credential, private data, dataset, or runtime artifact was copied into Emergenzix during this inventory.
