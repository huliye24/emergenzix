# PAPER v2 Hong Kong staged deployment - 2026-10-30

## Status

- Deployment state: **staged, new PAPER orders paused**
- Runtime: Hong Kong host, `/opt/emergenzix`
- Product commit in API image: `9d2122f`
- Deployment override commit: `481b9cc`
- API image: `ghcr.io/huliye24/emergenzix-api:paper-v2-9d2122f`
- Local immutable image digest: `sha256:13dcb63ebce2ae5fd5c4949040fe181d11810e7a39cfa3ff49616c7c4dc1cfad`
- LIVE trading: disabled

The deployment does not authorize PAPER v2 order creation. The persisted kill
switch is paused with reason `paper_v2_not_activated`, and the legacy PAPER
order endpoint rejects new requests while paused. Existing idempotent results
remain readable.

## Implemented safety components

- Fail-closed order state reducer with partial fills, unknown outcomes, cancel
  races, terminal states, and duplicate-event suppression.
- Immutable SQLite order events and restart replay verification.
- Balanced fill journals and fill-derived long-only position projections.
- Freshness, uncertain-order, missing-stop, balance, and mode risk gates.
- Local/executor reconciliation result that requires account freeze on any
  discrepancy.
- Persistent revision-controlled PAPER kill switch.
- Durable, deduplicated Telegram execution alerts through the existing outbox.
- Transactional PAPER v2 intent submission and atomic fill/event/ledger/position
  writes with injected-failure rollback tests.

## Observed evidence

- Windows focused and API tests passed before image construction.
- Linux Python 3.12 candidate container: `96 passed` for API, order state,
  persistence, accounting, position, risk, reconciliation, kill switch,
  alerts, outbox, and notification tests.
- Pre-deployment SQLite online backup saved under
  `/opt/emergenzix/backups/20261030-paper-v2-predeploy/` with SHA-256 checksums.
- Backup-copy migration produced `new_orders_paused=1` before touching the live
  volume.
- Post-deployment API readiness: database and paper account `ok`.
- Post-deployment database `PRAGMA integrity_check`: `ok`.
- A legacy new-order smoke request returned HTTP 409 while paused.
- API restart preserved the paused kill switch.
- Controlled Telegram deployment alert was delivered in one attempt.
- Notification outbox after deployment: 56 delivered, 0 pending, 0 retrying,
  0 dead.
- Isolated rollback smoke used the pre-deployment database copy and old `0.1.0`
  image; the container started and database integrity was `ok`. It was removed
  after the test and never received production traffic.

## Remaining activation gates

PAPER new orders must remain paused until all of the following are implemented
and accepted:

1. A reviewed PAPER v2 API or internal adapter invokes `PaperOperations`; the
   legacy immediate-fill path must not be used as the v2 execution path.
2. Risk inputs come from documented, current market, balance, stop, and order
   sources rather than caller-supplied booleans.
3. Reconciliation runs automatically on startup and on a schedule, persists the
   freeze decision, and emits/retries alerts.
4. Ledger and position replay are checked across all persisted v2 orders on
   startup; any divergence keeps the account paused.
5. Operator authentication/authorization exists for changing the kill switch,
   or activation is performed through a documented local-only procedure.
6. A second Linux acceptance run covers concurrent submissions, partial fills,
   timeout/unknown state, restart during fill processing, notification outage,
   and restore from backup.

## Rollback boundary

The old `0.1.0` image does not enforce the new persisted kill switch. A rollback
must therefore stop externally reachable API/watch services before restoring
the backup and starting the old image. Do not perform a live-traffic hot
rollback to `0.1.0`, because that would re-enable its legacy PAPER order path.
