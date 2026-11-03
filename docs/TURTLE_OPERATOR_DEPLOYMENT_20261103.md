# Turtle backtest and research-operator deployment — 2026-11-03

## Status

Deployed and verified on `postyouth.club` from pinned Git commit `01fa241`.

## Release artifacts

- Strategy/UI commit: `f9735c7`
- Portable dual-node runner commit: `1b5c766`
- Dual-AWS evidence commit: `23360a0`
- Bounded operator commit and deployed revision: `01fa241`
- Server worktree: `/opt/emergenzix-releases/01fa241`
- API image: `ghcr.io/huliye24/emergenzix-api:01fa241`
- Web image: `ghcr.io/huliye24/emergenzix-web:01fa241`

Both images were built on the deployment host from the detached, pinned worktree. The existing `emergenzix-data` volume and Binance read-only secret mounts were retained.

## Delivered behavior

- Versioned BTC, ETH, and BNB Turtle 20/10 research candidates are seeded idempotently when `EMERGENZIX_SEED_BUILTIN_STRATEGIES=true`.
- The backtest view accepts symbol, 1h/4h/1d timeframe, 120/250/500/1000 completed candles, and initial USDT cash.
- The strategy uses a 20-bar breakout, 10-bar exit, ATR(20) sizing, 1% risk units, at most four long-only units, 10 bps fee and 5 bps slippage per side.
- A separate `/operator` session lasts 30 days and may authorize only `POST /api/v1/backtests`.
- Visitor sessions remain read-only.
- Operator sessions cannot mutate strategies or create PAPER orders. No TESTNET or LIVE execution path exists.

The operator code is stored outside Git and images. A local owner copy is at `E:\亚马逊云\亿速云服务器\emergenzix-operator-code.txt`; the deployment host stores the code and signing secret under `/etc/emergenzix/` with container-readable owner-only permissions.

## Verification

- Turtle and Donchian focused Python tests: 6 passed.
- Visitor session tests: 2 passed.
- Operator session and authorization tests: 3 passed.
- Web TypeScript check: passed.
- Web production build: passed locally and inside the pinned Docker build.
- API and Web containers: healthy.
- Seeded Turtle candidates observed through the deployed API: 3.
- `/operator`: HTTP 200 publicly.
- Operator cookie: `Max-Age=2592000`, `Secure`.
- Operator empty backtest POST: backend HTTP 422, demonstrating permitted routing without persisting a run.
- Operator PAPER order POST: proxy HTTP 403.
- Operator strategy mutation POST: proxy HTTP 403.
- Anonymous `/trade`: redirects to `/access`.

The broader local FastAPI TestClient suite could not be executed in the workstation's pre-existing Starlette/httpx combination because that installed TestClient passes a removed `app=` argument. Pure engine tests and an independent ASGI transport test cover the new strategy dispatch; the pinned Docker image built successfully with repository runtime dependencies.

## Routing and rollback

Nginx now routes `/operator` and `/api/operator` to the product Web container. The previous Nginx configuration is preserved at:

`/etc/nginx/conf.d/postyouth.club.conf.before-01fa241`

To roll back:

1. Restore that Nginx file, validate with `nginx -t`, then reload Nginx.
2. Redeploy API image `ghcr.io/huliye24/emergenzix-api:paper-v2-binance-a2ff980` and Web image `ghcr.io/huliye24/emergenzix-web:51fb06f` in Compose project `emergenzix`.
3. Preserve the `emergenzix-data` volume and all `/etc/emergenzix` secrets.
4. Re-run health, visitor authorization, PAPER/LIVE safety, and Binance read-only checks.

Pre-existing Nginx warnings about duplicate `strokera.com` server names remain unrelated to this deployment.
