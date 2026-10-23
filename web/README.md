# Emergenzix Web

Mobile-first dashboard for the Emergenzix API. The first interface supports strategy creation, a synthetic-data sample backtest, paper orders, positions, and saved activity.

## Run locally

Start the backend first by following the root README. Then:

```bash
cd web
npm install
EMERGENZIX_API_URL=http://127.0.0.1:8000 npm run dev
```

Open http://127.0.0.1:3000.

On PowerShell:

```powershell
$env:EMERGENZIX_API_URL = "http://127.0.0.1:8000"
npm run dev
```

The Next.js rewrite proxies browser requests to the FastAPI service, avoiding a direct browser-to-backend URL dependency. Set `EMERGENZIX_API_URL` to the private backend address reachable by the Next.js server.

## Important

- The sample backtest uses generated synthetic bars, not historical exchange data.
- Paper orders use the reference price entered in the form and never contact an exchange.
- The backend currently has no authentication. Keep both services on localhost or a protected private network during development.
