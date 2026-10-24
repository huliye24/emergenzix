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
