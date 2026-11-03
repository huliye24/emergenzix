# Legacy NEMT visitor-code deployment — 2026-11-03

## Status

- Public page: `https://postyouth.club/trade`
- Deployment state: **active and verified**
- Runtime: Hong Kong control host, `/opt/nemt-app`
- Execution mode: DEMO/PAPER; LIVE remains disabled

This deployment replaces the legacy email/password screen with a single
visitor-code field. The old password and unauthenticated DEMO login routes are
removed. A successful code exchange creates a server-side session for the
dedicated `visitor` user with exactly the `viewer` role.

The legacy application is not a Git worktree. No legacy source tree was moved
into Emergenzix. The exact changed-file inventory and backups below provide the
rollback boundary until that application is migrated into a reviewed source
repository.

## Changed files

Backend:

- `/opt/nemt-app/backend/trade_app/config.py`
- `/opt/nemt-app/backend/trade_app/auth.py`
- `/opt/nemt-app/backend/trade_app/db.py`
- `/opt/nemt-app/backend/trade_app/schemas.py`
- `/opt/nemt-app/backend/trade_app/main.py`

Web:

- `/opt/nemt-app/web/src/lib/trade/api.ts`
- `/opt/nemt-app/web/src/components/trade/studio/StudioLogin.tsx`
- `/opt/nemt-app/web/src/components/trade/LoginScreen.tsx`
- `/opt/nemt-app/web/src/components/trade/TradeApp.tsx`

Runtime secret configuration:

- `/etc/nemt-app/visitor_code`, mode `0400`, owner `nemtapp:nemtapp`
- `NEMT_VISITOR_CODE_FILE=/etc/nemt-app/visitor_code` in the root-only
  `/etc/nemt-app/api.env`

The visitor code value is not present in source, logs, this document, or Git.

## Authentication boundary

- `POST /api/v1/auth/visitor` accepts the visitor code with process-local rate
  limiting and constant-time comparison.
- `POST /api/v1/auth/login` is no longer registered.
- `POST /api/v1/auth/demo` is no longer registered.
- A dedicated `visitor` database user is created with exactly `viewer`; its
  unusable random password hash is rotated on service startup.
- Pre-existing sessions were deleted during the cutover so an earlier
  admin/demo cookie cannot bypass the new gate.
- Viewer authorization continues to deny trader/admin endpoints server-side.

## Verification

- Python compile and isolated visitor-auth checks passed.
- Next.js 16 production build and TypeScript checks passed.
- Public page was visually inspected and showed one visitor-code field; the
  email and password fields were absent.
- Endpoint matrix after deployment:
  - old password login: HTTP 404
  - old DEMO bypass: HTTP 404
  - incorrect visitor code: HTTP 401
  - correct visitor code: HTTP 200
  - session restore: HTTP 200, role `viewer`
  - viewer request to admin audit endpoint: HTTP 403
- `nemt-api.service` and `nemt-web.service` were active after deployment.

The full ESLint run still reports three pre-existing React-rule errors in
`ExecutionPage.tsx`, `StrategiesPage.tsx`, and `useTradingData.ts`. None is in
the visitor-auth change set; the production build succeeds.

## Backups and rollback

- Source/config backup:
  `/opt/nemt-app/backups/visitor-code-complete-20261103-115447.tar.gz`
  (`sha256:2e691259770556090795eed01be41e30c19791726107053cdfd3c9ddfc5c897f`)
- SQLite online backup:
  `/opt/nemt-app/backups/nemt-app-before-visitor-20261103-115650.sqlite3`
  (`sha256:c20d04b567ae891c87ecfea8a6b2b7501dcebfb830d0903c80434ada4ab1a5db`)
- Previous Next.js build:
  `/opt/nemt-app/backups/.next-before-visitor-20261103-1155`

Rollback must restore the backed-up source, database, and `.next` build as one
coherent version, then restart both services. Do not restore only the old UI,
because it calls authentication routes that are intentionally absent now.
