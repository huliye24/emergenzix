# Emergenzix

**An open-source, risk-controlled quantitative trading product.**

Emergenzix is the product layer emerging from the NEMT research program. It is designed to turn market data and reviewed research into reproducible strategy evaluation and auditable trading workflows.

> **Development status:** the first backend vertical slice is under review on the `feat/core-backend-mvp` branch. It includes a FastAPI API, SQLite persistence, an SMA-crossover backtest, and paper-only order simulation. It is not production-ready and has no live exchange connection.

## NEMT and Emergenzix

- **NEMT — research and experiments:** economic hypotheses, quantitative theory, research datasets, MATLAB/GPU experiments, and reproducible research.
- **Emergenzix — product and operations:** strategy management, backtesting, paper trading, product APIs, risk controls, and eventually a user-facing responsive application.

A research result must carry provenance and pass review before it informs a product strategy. Research code and experiment artifacts are not copied into the product runtime by default.

## Current implementation

- Health endpoint and product overview
- Strategy creation and listing for the initial SMA-crossover strategy type
- Deterministic backtesting using completed-bar signals and next-bar-open execution
- Fee and slippage assumptions recorded with each backtest
- Persisted backtest results, equity curve, and closed-trade records
- SQLite-backed paper account, positions, and simulated order history
- Server-side notional limit, available-cash checks, and no-short-selling validation
- Automated API tests and a GitHub Actions workflow

The backtest is a baseline implementation, not evidence of profitability. Paper orders use caller-supplied reference prices and do not represent exchange market fills.

## Run the backend

Requirements: Python 3.11 or newer.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m compileall -q emergenzix_api tests
pytest -q
```

On Windows PowerShell, activate the environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

Start the API locally:

```bash
uvicorn emergenzix_api.main:app --host 127.0.0.1 --port 8000
```

Open the interactive API documentation at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) and health status at [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health).

### AWS development testing

From the repository:

```bash
cd backend
python -m pip install -r requirements.txt
python -m compileall -q emergenzix_api tests
pytest -q
```

Use the output as the test evidence for the next iteration. The API currently has no authentication; bind it to localhost or protect it behind an approved private network/reverse proxy. Do not expose port 8000 directly to the public internet.

## API surface

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/health` | Runtime health and safety mode |
| GET | `/api/v1/overview` | Strategy, backtest, and paper-account summary |
| GET / POST | `/api/v1/strategies` | List or create strategies |
| GET | `/api/v1/strategies/{id}` | Inspect one strategy |
| POST | `/api/v1/backtests` | Run and persist a backtest |
| GET | `/api/v1/backtests` | List recent backtests |
| GET | `/api/v1/backtests/{id}` | Retrieve a saved backtest |
| GET | `/api/v1/paper/account` | Inspect the simulated account |
| GET | `/api/v1/paper/positions` | List simulated positions |
| GET / POST | `/api/v1/paper/orders` | List or create paper orders |
| GET | `/api/v1/paper/trades` | Inspect simulated fills |

Create a sample strategy:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/strategies \
  -H "Content-Type: application/json" \
  -d '{"name":"BTC SMA baseline","symbol":"BTCUSDT","strategy_type":"sma_crossover","fast_window":5,"slow_window":20}'
```

Backtest requests supply ordered OHLCV bars with timezone-aware timestamps. Fees and slippage are explicit request parameters and are included in the saved result.

## Safety boundaries

- The API only provides **PAPER** execution; no exchange adapter or LIVE order endpoint exists in this slice.
- Unknown or invalid requests are rejected; paper orders enforce notional limits and prohibit short selling.
- API keys and exchange credentials are not required and must never be committed.
- The initial SQLite setup is for local/single-process development. Reassess storage, authentication, monitoring, and operational controls before multi-user or production deployment.
- Backtests and simulated results do not guarantee future performance.

## Repository guide

- [Product scope](docs/PRODUCT_SCOPE.md)
- [Roadmap](docs/ROADMAP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Migration inventory](docs/MIGRATION_INVENTORY.md)
- [ADR-001: backend baseline](docs/adr/ADR-001-product-backend-baseline.md)
- [Strategy lifecycle](docs/STRATEGY_LIFECYCLE.md)
- [Engineering rules](AGENTS.md)
- [Security policy](SECURITY.md)

## License

Licensed under the **GNU General Public License v3.0 (GPL-3.0)**. See [LICENSE](LICENSE).
