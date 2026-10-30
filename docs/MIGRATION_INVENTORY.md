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

## 2026-10-30 position, accounting, and cloud-runtime addendum

This addendum extends the original product-facing snapshot with a read-only
inspection of the local NEMT research tree and the two AWS research hosts. It
does not change the original evidence snapshot or authorize a bulk transfer.

### Inspected sources

- Local NEMT repository: `E:/NEMT Simulator`, branch
  `feature/current-main`, HEAD
  `c0c7dbf37962e4e146c448d73f103e925fbe22ba`.
- AWS GPU research host `43.192.236.139`: the Git checkout
  `/home/ubuntu/emergenzix` was at
  `3e985bba55d4c3f242b49926164296b5442dc694`; additional acceptance and
  validation directories were runtime snapshots, not authoritative Git roots.
- AWS MATLAB/CPU research host `52.83.234.254`: Emergenzix Git checkouts were
  older or task-specific (`b203a2f...`, `5ddb049...`, and `6195100...`) and
  contained untracked operator scripts. They are evidence/runtime workspaces,
  not a source of truth.

The local NEMT worktree contained extensive modified and untracked research
files. Their presence is useful for discovery but is not provenance. No cloud
file, dataset, credential, log, database, or generated report was copied.

### Focused reuse decisions

| Candidate | Provenance and license | Observed verification | Decision | Required treatment |
| --- | --- | --- | --- | --- |
| `nemt_os/services/research/nemt_strategy_v3.py::risk_sized_notional` | Tracked in NEMT commit `f784e6ff5f9bc5465078a85b10f599a5bf716280`; source repository is GPL-3.0-or-later | NEMT has focused v3 tests; the cloud acceptance snapshot also carried an imported copy and tests | **Adapt as specification**, not a direct drop-in | Preserve source commit and copyright/license notice; remove leverage assumptions from the first spot PAPER slice; include fees, slippage, explicit stop price, cash/notional caps, and fail-closed validation |
| `nemt_os/services/research/position_sizing.py` | Untracked local research file, SHA-256 `5fd497fbc157cd9cae4f3b75b9db52c9c4951446def4c92f33dab4649ddfba32`; stdlib-only | Its untracked test file, SHA-256 `4b65a099bfac9804f5b983c00889ca3959956494352e247322f221d10145b635`, passed 3 tests locally | **Research only; do not migrate yet** | First commit and review it in NEMT with assumptions, estimator stability, sample-size requirements, and reproducible evidence; Kelly output must never directly authorize an order |
| `nemt_os/services/research/nemt_pyramiding_v4.py` | Tracked in NEMT commit `d133e8deaefa6a1bbb382cf7814727b0b11642d9`; GPL-3.0-or-later | Focused NEMT tests are present; no Emergenzix reproduction was run in this addendum | **Defer; later adapt policies** | Use only after base sizing, portfolio risk, ledger identities, and idempotent execution exist; never add to losing positions; preserve total campaign risk budget |
| `nemt_os/services/research/complete_lifecycle.py` | Untracked local research file, SHA-256 `ea1e6225b74913baff4ce1c1a98f69aca636dbb38ee998c2161b9e9fb10b9b35` | Tests are present but provenance is incomplete | **Research only; do not migrate** | Commit/review in NEMT first; separate signal, sizing, execution, and accounting before any product adaptation |
| Cloud `equity_ledger.py` acceptance snapshot | Non-Git GPU snapshot, SHA-256 `98645f9756ef73f0db71c36b09b7481f6433c88c6922a39c063b1d461b4aff0e`; intended GPL-compatible Emergenzix code but no authoritative commit was found | Snapshot includes focused immutable-decimal conservation tests, SHA-256 `62f5a8365c149e1f0a87d8ecf4312250fe14ab910b167c80fe85fe92a6ba5c66` | **Recover provenance before reuse** | Locate the originating branch/PR or re-submit it as a reviewed patch with attribution; it covers settled closed trades only, not open positions, margin, fills, or available collateral |
| Cloud quick-backtest and imported-strategy snapshots | Present in non-Git acceptance/release directories; absent from the inspected local Emergenzix Git history | Tests were present in the snapshots, but their CI/commit origin was not established | **Do not copy from cloud** | Recover the originating Git revision or reconstruct from reviewed NEMT source with an explicit migration record |
| Local `freqtrade/` source tree | Upstream GPL-3.0; Emergenzix already pins Freqtrade as a submodule | No new adapter acceptance was performed | **Keep isolated behind an adapter** | Do not copy internal engine code; preserve upstream notices and compare results against the compatibility engine before adoption |
| Local `vnpy/` source tree | Upstream MIT, copyright Xiaoyou Chen | Architecture reference only in this addendum | **Use concepts, not copied runtime code** | If code is later copied or modified, retain the MIT copyright and permission notice; TESTNET acceptance remains mandatory |
| `quant-mvp/` | No license file was found at its directory root during inspection | Not audited | **Do not migrate** | Establish ownership and license before reading it as a code donor |
| NEMT/cloud LIVE executors and private exchange adapters | Mixed untracked prototypes and operational files | No safe TESTNET acceptance or provenance established | **Explicitly excluded** | Never copy credentials or activate LIVE paths; routine development must remain PAPER-only |

### Next reusable product slice

The first position-sizing product slice should be an Emergenzix-owned,
deterministic PAPER calculator derived from the tracked v3 risk-budget concept,
not from the untracked Kelly implementation. Its inputs should be account
equity, entry price, protective stop, risk fraction, fee/slippage buffer,
available cash, and maximum notional. Its output should be a reviewable sizing
decision with the binding cap and no ability to create an order. Invalid or
non-finite inputs, zero stop distance, insufficient cash, and unknown mode must
return no executable quantity.

## 2026-10-30 NEMT v4.4/v5.2.7 research-source addendum

The NEMT research package now used for compatibility work was recovered from
the public Emergenzix Git commit
`5b453c4332a216a38df0582679a147dc0978c4c8`, rather than from an untracked
cloud runtime directory. The focused migration contains
`backend/emergenzix_api/nemt_strategy/` and its original unit tests. Its
registry marks NEMT v4.4 as `BACKTEST_ONLY` with `live_enabled=False`; NEMT
v5.2.7 remains an experiment and is not registered as an executable product
strategy or promoted to PAPER, TESTNET, or LIVE.

The new outer walk-forward scheduler is an Emergenzix-owned protocol layer. It
does not fetch data, submit orders, or grant strategy approval. A real M0
three-arm comparison remains pending until the existing v4.4 CLI is refactored
to consume the same in-memory bars, fee/slippage assumptions, capital, and risk
limits as the Donchian-only and v5.2.7 arms. Until then, framework tests are not
research evidence and no relative-performance conclusion may be drawn.

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
