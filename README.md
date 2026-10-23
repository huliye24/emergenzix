# Emergenzix

**An open-source, risk-controlled quantitative trading product.**

Emergenzix is the product layer emerging from the NEMT research program. It is designed to turn market data and reviewed research into reproducible strategy evaluation and auditable trading workflows.

> **Development status:** `main` contains the initial backend, responsive dashboard, a CCXT-based public OHLCV endpoint, and a pinned Freqtrade upstream submodule. This is an early development baseline; AWS/Codex verification remains the next step.

## NEMT and Emergenzix

- **NEMT — research and experiments:** economic hypotheses, quantitative theory, research datasets, MATLAB/GPU experiments, and reproducible research.
- **Emergenzix — product and operations:** strategy management, backtesting, paper trading, product APIs, risk controls, and the user-facing responsive interface.

A research result must carry provenance and pass review before it informs a product strategy. Research code and experiment artifacts are not copied into the product runtime by default.

## Current implementation

- FastAPI backend with health and product overview endpoints
- Strategy creation and listing for the initial SMA-crossover strategy type
- Deterministic backtesting using completed-bar signals and next-bar-open execution
- Fee and slippage assumptions recorded with each backtest
- Persisted backtest results, equity curve, and closed-trade records
- SQLite-backed paper account, positions, and simulated order history
- Server-side notional limit, available-cash checks, and no-short-selling validation
- Responsive Next.js/React workspace with dedicated Overview, Strategies, Backtests, Paper Trading, and Activity views; strategy, backtest, paper-order, position, and history workflows share the existing API
- Public spot OHLCV retrieval through CCXT for an explicit exchange allowlist, plus synthetic demo candles
- Freqtrade source pinned as a Git submodule for evaluation as the mature strategy/backtesting/dry-run engine; its adapter is not yet wired into the current backtest endpoint
- Dataset source, exchange, symbol, and timeframe persisted with backtest results
- Automated backend tests and CI build/type-check workflows

The backtest is a baseline implementation, not evidence of profitability. Paper orders use caller-supplied reference prices and do not represent exchange market fills.

## Get the repository and upstream engine

Clone the repository with the pinned Freqtrade source:

```bash
git clone --recurse-submodules https://github.com/huliye24/emergenzix.git
cd emergenzix
```

If the repository is already cloned, initialize the upstream source with `git submodule update --init --recursive`. Freqtrade is kept as a separate source tree and should run in its own environment/container; its current upstream branch requires Python 3.12 or newer.

## Run the backend

Requirements for the Emergenzix API: Python 3.11 or newer. CCXT is installed from `backend/requirements.txt`.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m compileall -q emergenzix_api tests
pytest -q
```

Start the API in a terminal:

```bash
cd backend
uvicorn emergenzix_api.main:app --host 127.0.0.1 --port 8000
```

## Run the web dashboard

In a second terminal:

```bash
cd web
npm install
EMERGENZIX_API_URL=http://127.0.0.1:8000 npm run dev
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000). On Windows PowerShell, set the variable first with `$env:EMERGENZIX_API_URL = "http://127.0.0.1:8000"`, then run `npm run dev`.

The dashboard proxies API requests through Next.js. The backend must be running first.

## AWS / Codex verification

Run these checks from the repository:

```bash
cd backend
python -m pip install -r requirements.txt
python -m compileall -q emergenzix_api tests
pytest -q
cd ../web
npm install
npm run typecheck
npm run build
```

Use the observed output as evidence for the next iteration. The API currently has no authentication; keep it on localhost or protect it behind an approved private network/reverse proxy. Do not expose the backend or dashboard directly to the public internet without a reviewed deployment and authentication design.

## API surface

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/health` | Runtime health and safety mode |
| GET | `/api/v1/overview` | Strategy, backtest, and paper-account summary |
| GET | `/api/v1/market-data/ohlcv` | Fetch public OHLCV candles through CCXT; no exchange credentials required |
| GET / POST | `/api/v1/strategies` | List or create strategies |
| GET | `/api/v1/strategies/{id}` | Inspect one strategy |
| POST | `/api/v1/backtests` | Run and persist a backtest |
| GET | `/api/v1/backtests` | List recent backtests |
| GET | `/api/v1/backtests/{id}` | Retrieve a saved backtest |
| GET | `/api/v1/paper/account` | Inspect the simulated account |
| GET | `/api/v1/paper/positions` | List simulated positions |
| GET / POST | `/api/v1/paper/orders` | List or create paper orders |
| GET | `/api/v1/paper/trades` | Inspect simulated fills |

Backtest requests supply ordered OHLCV bars with timezone-aware timestamps. Fees and slippage are explicit request parameters and are included in the saved result.

## Safety boundaries

- The API only provides **PAPER** execution; the CCXT integration is read-only public market data, with no exchange order adapter or LIVE order endpoint.
- Unknown or invalid requests are rejected; paper orders enforce notional limits and prohibit short selling.
- API keys and exchange credentials are not required and must never be committed.
- The initial SQLite setup is for local/single-process development. Reassess storage, authentication, monitoring, and operational controls before multi-user or production deployment.
- Backtests and simulated results do not guarantee future performance.

## Repository guide

- [Product scope](docs/PRODUCT_SCOPE.md)
- [Roadmap](docs/ROADMAP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Upstream components and license boundaries](docs/UPSTREAM_COMPONENTS.md)
- [Migration inventory](docs/MIGRATION_INVENTORY.md)
- [ADR-001: backend baseline](docs/adr/ADR-001-product-backend-baseline.md)
- [Strategy lifecycle](docs/STRATEGY_LIFECYCLE.md)
- [Engineering rules](AGENTS.md)
- [Security policy](SECURITY.md)

## License

Licensed under the **GNU General Public License v3.0 (GPL-3.0)**. See [LICENSE](LICENSE).
