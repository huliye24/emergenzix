# Legacy NEMT persistent visitor-session deployment

Date: 2026-11-03

Target: `https://postyouth.club/trade`

Status: deployed and verified

## Objective

Reduce repeated visitor-code entry during development without bypassing public access control or extending privileged operator sessions.

## Implemented behavior

- A successfully authenticated visitor receives a 30-day server-side session and matching persistent HttpOnly cookie.
- Operator, trader, admin, and DEMO account sessions retain the existing 12-hour lifetime.
- Logout still clears the cookie and revokes the server-side session.
- Expired or server-revoked sessions still fail authentication.
- The cookie remains `HttpOnly`, `SameSite=Lax`, and `Secure` under the deployed HTTPS configuration.
- Visitor authorization remains viewer-only; this change grants no order-placement permission.

Existing 12-hour visitor sessions are not silently extended. The developer signs in once after this deployment, and the newly issued visitor session then uses the 30-day lifetime.

## Changed deployment files

- `backend/trade_app/config.py`
- `backend/trade_app/auth.py`
- `backend/trade_app/main.py`
- `backend/tests/test_visitor_session_ttl.py`

The deployed legacy application is not a Git checkout. This repository records the deployment boundary and verification evidence only.

## Verification

- Python compilation of the three changed application modules — passed.
- Focused Pytest suite — 2 passed.
- Test coverage confirms the visitor database session exceeds 29 days.
- Test coverage confirms the ordinary operator session remains between 11 and 13 hours.
- Test coverage confirms the visitor cookie includes `Max-Age=2592000`, `HttpOnly`, and `SameSite=lax`.
- `nemt-api.service` — active after restart.
- `nemt-web.service` — active.
- API health and readiness endpoints — HTTP 200.
- Public trade route — HTTP 200.

## Recovery

Source backup: `/opt/nemt-app/backups/visitor-session-before-20261103-211500`
