# Binance account visibility and visitor access deployment — 2026-11-03

## Status

- Deployment state: **staged on the Hong Kong control host**
- Public `/trade` route: unchanged; it still serves the legacy NEMT interface
- Emergenzix web: `127.0.0.1:3000`, image
  `ghcr.io/huliye24/emergenzix-web:visitor-e667ec8`
- Emergenzix API: private Compose network, image
  `ghcr.io/huliye24/emergenzix-api:paper-v2-binance-a2ff980`
- PAPER new orders: paused, reason `paper_v2_not_activated`
- LIVE trading: disabled; no Binance order method exists

The API image is based on the deployed PAPER v2 safety commit `9d2122f`, not
the later release branch that does not contain that staged kill-switch work.
The visitor web is based on current v0.2.0 `main` plus the visitor UI change.

## Secret boundary

Existing Binance values were migrated from the host's protected NEMT runtime
environment into separate files below `/etc/emergenzix`. Values were not
printed, committed, or copied into a container image. Binance files are mode
`0400` and owned by the API container UID; visitor files are mode `0400` and
owned by the web container UID.

The current Binance key was observed with reading enabled, withdrawals
disabled, and IP restriction enabled. Its exchange-side Spot/Futures trading
flags are enabled, so a dedicated USER_DATA/read-only key remains the preferred
follow-up. Emergenzix itself implements signed GET only and reports
`product_order_permission=false` regardless of those credential flags.

## Verification

- Local focused safety suite: 15 passed (Binance adapter, persisted PAPER kill
  switch, and PAPER operations); Ruff and Python compile passed.
- Web type-check and Next.js production build passed.
- API and web containers reported healthy after deployment.
- Binance summary returned `connected`, `READ_ONLY`, withdrawals false,
  IP-restricted true, and LIVE false without logging or returning credentials.
- Persisted PAPER control remained paused with reason
  `paper_v2_not_activated` after API replacement.
- Visitor login returned 200, authenticated account-summary GET returned 200,
  and visitor POST to `/api/v1/paper/orders` returned 403.
- Public `https://postyouth.club/trade` continued to return HTTP 200 from the
  unchanged legacy route.

## Operations and rollback

The pre-change production backup is stored locally under
`E:/亚马逊云/香港节点/backup/20261103-112636/`.

The prior API image `ghcr.io/huliye24/emergenzix-api:paper-v2-9d2122f` and
prior web image `ghcr.io/huliye24/emergenzix-web:0.1.0` remain available for
rollback. The rejected non-PAPER-v2 API candidate and dangling build layers
were removed; data volumes and rollback images were retained.

Do not route public traffic from `/trade` to port 3000 until the visual
transition and reverse-proxy path behavior are separately reviewed. The two
applications have different UI and asset-routing assumptions.
