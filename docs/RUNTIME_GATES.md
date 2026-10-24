# Runtime Readiness Gate

## Purpose

The API container must not report ready until its SQLite database is readable and the paper account has been initialized. This is a startup/readiness check, not a trading signal or proof that market data and Telegram are reachable.

## Endpoints

- \`GET /health\`: liveness probe. Confirms that the API process can respond; it does not query the database.
- \`GET /ready\`: readiness probe. Runs a read-only database query and verifies that the singleton paper account exists. Returns HTTP 200 only when both checks pass; otherwise returns HTTP 503.

The readiness response always identifies \`mode: PAPER\` and \`live_trading_enabled: false\`. This release does not support live order execution.

## Container behavior

- The API Dockerfile health check uses \`/ready\`.
- Docker Compose uses \`/ready\` for API health.
- The watcher service waits for the API to become healthy before its initial start.
- The watcher only evaluates public market data and sends paper-review notifications; it does not submit exchange orders.

Compose dependency conditions gate initial startup. They do not automatically stop an already-running watcher if the API later becomes unhealthy; runtime recovery and alerting are separate future work.

## Validation

CI tests cover the ready state, a missing paper account, and database errors. Docker image builds validate packaging, but the target cloud environment must still be checked after deployment.

This gate does not verify exchange connectivity, historical-data completeness, Telegram delivery, strategy profitability, or live-trading safety. Live trading remains disabled.


## Telegram startup gate

The Docker Compose watcher requires Telegram configuration by default. Set both `EMERGENZIX_TELEGRAM_BOT_TOKEN` and `EMERGENZIX_TELEGRAM_CHAT_ID` in the untracked local `.env` file; never commit real credentials. Set `EMERGENZIX_TELEGRAM_REQUIRED=false` only for intentional local log-only development.

When Telegram is configured, the watcher sends a PAPER-only startup message before polling market data. If the configuration is incomplete or Telegram rejects/fails the startup delivery, the process emits a sanitized structured error to stdout and exits without starting the market watcher. Docker restart policy can retry the process; it will not bypass this check.

The startup check is backed by the durable SQLite notification outbox. Failed deliveries are retained and retried with backoff; aggregate queue health is available at `GET /api/v1/notifications/status`.


## Durable Telegram notification outbox

The watcher writes each Telegram notification to a separate SQLite outbox on the persistent `/data` volume before attempting delivery. Queue records retain the structured event and optional chart image across process restarts. A stable event key deduplicates reprocessing of the same completed candle; event keys use the completed-candle timestamp when available.

Successful sends are marked delivered. Failures remain pending and are retried with exponential backoff (30 seconds up to one hour); the watcher checks the queue on every polling cycle, even if no new market event was generated. After eight failed attempts, a record is marked dead and retained for inspection rather than silently deleted. Error logs record exception types only and do not include the bot token or Telegram response body.

The watcher enqueues emitted events before saving its updated watcher state, reducing the crash window in which an event could be skipped. This is a single-consumer, SQLite-backed first version; it does not yet provide an operator UI for dead-letter replay or a multi-worker claim lease.


## Notification queue status endpoint

`GET /api/v1/notifications/status` reports aggregate queue metrics only; it never returns event bodies, chart images, chat IDs, or bot credentials. Metrics include total, pending, retrying, due, cumulative failed-attempt count, delivered, and dead-letter counts, the oldest pending timestamp, the latest successful delivery timestamp, and the most recent stored exception type. Failed-attempt counts remain cumulative even after a later successful retry.

The endpoint returns `not_initialized` when the outbox database has not yet been created, `ok` when no dead-letter records exist, and `attention_required` when at least one record is dead-lettered. It is an observability endpoint, not proof of strategy performance or exchange connectivity. The API currently has no authentication; keep it on a private network and do not expose it publicly without an access-control review.


## Watcher operational alerts and recovery

The watcher monitors the durable notification outbox after each polling cycle. It sends one operational alert when the dead-letter count becomes non-zero, and one when pending notifications reach `EMERGENZIX_NOTIFICATION_BACKLOG_THRESHOLD` (default `20`). It sends a recovery message after the backlog falls below the threshold or the dead-letter count returns to zero.

Operational incident state is persisted to `EMERGENZIX_OPERATIONAL_ALERT_STATE_PATH` (default `/data/operational-alert-state.json`) so an unchanged incident does not generate a new alert after restart. Transition keys are stable and use the outbox deduplication mechanism; failed enqueue attempts are retried on later watcher cycles. Stale market data already blocks scenario analysis; when fresh data returns, the watcher now emits a separate recovery event.

Dead-letter records are retained rather than silently deleted. A dead-letter alert remains active until the dead-letter count is cleared by operator intervention; this version does not yet include an authenticated dead-letter replay or resolution UI. If the outbox database itself is unavailable, the watcher emits a sanitized structured error to container logs because the same unavailable outbox cannot reliably carry a Telegram alert.

These are operational notifications, not trading signals. The watcher remains PAPER-only and never submits exchange orders.


The watcher also treats repeated market-cycle exceptions as one persistent operational incident. It sends a single alert while the failure continues and a recovery notification after a later cycle succeeds. Logs and alert details include only the exception type, not provider error text or credentials. In `--once` mode, the watcher still flushes the failure alert before exiting with a non-zero status.
