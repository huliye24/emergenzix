# Telegram Command Bot

## Scope and safety

The Telegram command bot is an opt-in process for private operational queries and historical PAPER research. It does not provide exchange order, account credential, or LIVE trading commands.

The bot accepts commands only when the incoming Telegram chat ID exactly matches **EMERGENZIX_TELEGRAM_ALLOWED_CHAT_ID**. Unauthorized chats are ignored without a reply. The bot token is read from the server environment and must never be committed to Git.

The current API has no user authentication. Keep the API and web service on the private Compose network or localhost; do not publish the API port. The bot talks to the API using the internal Compose hostname.

## Configuration

Set these values in the local .env file:

- **EMERGENZIX_TELEGRAM_BOT_TOKEN**: token created for your Telegram bot.
- **EMERGENZIX_TELEGRAM_CHAT_ID**: destination for outbound watch notifications.
- **EMERGENZIX_TELEGRAM_ALLOWED_CHAT_ID**: exact numeric chat ID allowed to issue commands. Set this explicitly even if it is the same chat used for notifications.
- **EMERGENZIX_TELEGRAM_API_BASE_URL**: defaults to http://api:8000 in Compose.
- **EMERGENZIX_TELEGRAM_BACKTEST_COOLDOWN_SECONDS**: minimum delay between accepted backtest requests; default 60 seconds.

Do not put real values in .env.example or commit .env.

## Start and stop

The command bot is behind the telegram Compose profile and is not started by the default Compose command. After configuring the token and allowed chat ID, run:

~~~bash
docker compose --profile telegram up -d --build
docker compose --profile telegram logs -f telegram-bot
~~~

Stop the services with:

~~~bash
docker compose --profile telegram down
~~~

The bot persists its update offset, initialization flag, and backtest cooldown timestamp in the existing emergenzix-data volume. On first startup it intentionally discards any commands already queued before the bot was initialized, preventing stale messages from unexpectedly starting work. Later updates are offset-tracked across restarts. The date-range API is idempotent for identical research inputs, but a Telegram response can still be lost if the process stops during a request.

## Commands

- **/help** — list available commands.
- **/status** — report API health/readiness, mode, live-trading state, and notification queue counts.
- **/latest** — summarize the most recent saved date-range backtest.
- **/backtest YYYY-MM-DD [days]** — run a historical BTC/USDT spot 4-hour backtest. Duration defaults to 7 days and must be 1–30 days. The full requested interval must end in the past.

The backtest command uses the existing date-range research API with a fixed 90-day training window, 10 basis points of fee per side, 5 basis points of slippage per side, and 10,000 USDT initial simulated cash. It reports the selected candidate, net PnL, return, maximum drawdown, completed trades, win rate, and validation-evidence status. These settings are intentionally fixed in this first command interface.

## Operational limitations

- The bot does not create paper orders or live orders; it only queries status and runs historical research.
- The Telegram chat allowlist is a command authorization boundary, not a substitute for API authentication if the service is later exposed to other networks.
- A long historical-data download can take time. The bot processes updates sequentially and enforces a persisted cooldown before starting another backtest.
- Telegram and API errors are sanitized; diagnostic logs record error classes rather than tokens or raw response bodies.
- No real Telegram call, cloud deployment, or profitability claim is part of this code change. Run the documented tests and operational acceptance steps before enabling the Compose profile.
