# Product shell and persistent visitor session deployment — 2026-11-03

## Status

Deployed and verified on `postyouth.club` from pinned Git commit `51fb06f`.

This deployment moves the product's three primary views into the shared dashboard shell:

- 实盘（monitoring only; live order placement remains disabled）
- 回测
- 影子跟随（PAPER）

It also extends the signed visitor session from 12 hours to 30 days. Refreshing or reopening the site no longer requires the visitor code while the cookie remains valid.

## Release artifact

- Git branch: `codex/trade-decision-456`
- Git commit: `51fb06f`
- Server release worktree: `/opt/emergenzix-releases/51fb06f`
- Web image: `ghcr.io/huliye24/emergenzix-web:51fb06f`
- Compose project: `emergenzix`
- Active container: `emergenzix-web-1`

The image was built from the pinned worktree. Existing visitor-code and signing-secret files under `/etc/emergenzix/` were reused and were not copied into Git or image layers.

## Routing cutover

The Nginx configuration for `postyouth.club` now routes these product paths to the Web container on `127.0.0.1:3000`:

- `/trade`
- `/access`
- `/api/access`
- `/api/v1/`
- `/_next/`

The previous configuration is preserved at:

`/etc/nginx/conf.d/postyouth.club.conf.before-51fb06f`

Legacy NEMT services remain available on their existing internal ports during the migration window, but they are no longer the primary `/trade` experience.

## Verification

Observed checks:

- visitor-session tests: 2 passed
- Web TypeScript check: passed
- production Next.js build: passed
- Docker image build from pinned commit: passed
- active Web container health: healthy
- product health endpoint: `status=ok`, `mode=PAPER`, `live_trading_enabled=false`
- unauthenticated `/trade`: redirects to `/access`
- unauthenticated `/api/v1/overview`: returns 401
- visitor cookie: `Max-Age=2592000`, `HttpOnly`, `Secure`, `SameSite=Strict`
- two consecutive authenticated refresh requests: both returned 200 without redirecting to `/access`

Because the product cookie name differs from the legacy NEMT cookie, an existing browser must enter the visitor code once after this cutover. It should not be requested again until the user logs out, clears site data, the 30-day session expires, or the server signing secret is intentionally rotated.

## Safety boundary

No exchange credentials, API permissions, risk limits, order-placement paths, or data volumes were changed. LIVE order placement remains disabled. The visitor role remains read-only and non-GET visitor requests to `/api/v1/` remain blocked.

## Rollback

1. Restore `/etc/nginx/conf.d/postyouth.club.conf.before-51fb06f` to `/etc/nginx/conf.d/postyouth.club.conf`.
2. Run `nginx -t` and reload Nginx only if validation succeeds.
3. Redeploy the prior Web image `ghcr.io/huliye24/emergenzix-web:visitor-e667ec8` in the existing `emergenzix` Compose project if the product container itself must also be rolled back.
4. Re-run public access, API authorization, container-health, and PAPER-mode checks.

## Release qualification

This is a pinned development deployment from a review branch. It is not a semantic production release tag. A tagged release still requires merge to the protected release branch and the repository's normal CI/review gates.
