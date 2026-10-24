"""Long-running, paper-only market watcher emitting transition events as JSON lines."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from .market_data import fetch_public_ohlcv
from .operational_alerts import OperationalAlertMonitor, notification_health_conditions
from .scenarios import analyze_scenarios
from .schemas import Bar


TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}
ACTIONABLE_SCENARIOS = frozenset({"continuation_down", "false_breakdown", "reversal_up"})


@dataclass(frozen=True)
class WatchState:
    scenario: str | None = None
    last_completed_at: str | None = None
    trigger_key: str | None = None
    risk_key: str | None = None


@dataclass(frozen=True)
class WatchEvent:
    event_type: str
    severity: str
    occurred_at: str
    exchange: str
    symbol: str
    timeframe: str
    message: str
    details: dict[str, Any]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _env_bool(name: str, *, default: bool = False) -> bool:
    """Read a strict boolean environment variable without silently weakening gates."""
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _parse_completed_bars(payload: dict[str, Any]) -> list[Bar]:
    raw_bars = payload.get("bars")
    if not isinstance(raw_bars, list) or len(raw_bars) < 2:
        raise ValueError("market response must contain at least two candles")
    completed = [Bar.model_validate(item) for item in raw_bars[:-1]]
    timestamps = [bar.timestamp for bar in completed]
    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
        raise ValueError("market candles must be in strictly increasing timestamp order")
    return completed


def evaluate_market_payload(
    payload: dict[str, Any], previous: WatchState, *, lookback: int, now: datetime
) -> tuple[WatchState, list[WatchEvent]]:
    """Evaluate one public market response and suppress unchanged notifications."""
    exchange = str(payload.get("exchange", "unknown"))
    symbol = str(payload.get("symbol", "unknown"))
    timeframe = str(payload.get("timeframe", "unknown"))
    if timeframe not in TIMEFRAME_SECONDS:
        raise ValueError(f"unsupported watch timeframe: {timeframe}")

    completed = _parse_completed_bars(payload)
    last_bar = completed[-1]
    age_seconds = (now - last_bar.timestamp.astimezone(timezone.utc)).total_seconds()
    stale_limit = TIMEFRAME_SECONDS[timeframe] * 3
    if age_seconds < 0:
        raise ValueError("latest completed candle is in the future")
    if age_seconds > stale_limit:
        risk_key = f"stale:{last_bar.timestamp.isoformat()}"
        state = WatchState(previous.scenario, last_bar.timestamp.isoformat(), previous.trigger_key, risk_key)
        if previous.risk_key == risk_key:
            return state, []
        return state, [WatchEvent(
            "risk_alert", "warning", now.isoformat(), exchange, symbol, timeframe,
            "Market data is stale; analysis and trading action are blocked.",
            {
                "last_completed_at": last_bar.timestamp.isoformat(),
                "stale_limit_seconds": stale_limit,
                "alert_key": f"market-data-stale:{last_bar.timestamp.isoformat()}:active",
            },
        )]

    recovered_stale_key = (
        previous.risk_key
        if previous.risk_key and previous.risk_key.startswith("stale:")
        else None
    )
    result = analyze_scenarios(completed, lookback=lookback)
    completed_at = last_bar.timestamp.isoformat()
    trigger_key = f"{result.scenario}:{completed_at}" if result.scenario in ACTIONABLE_SCENARIOS else None
    state = WatchState(result.scenario, completed_at, trigger_key, None)
    details = result.as_dict()
    details["bar_timestamp"] = completed_at
    events: list[WatchEvent] = []
    if recovered_stale_key is not None:
        stale_timestamp = recovered_stale_key[len("stale:"):]
        events.append(
            WatchEvent(
                "risk_recovered",
                "info",
                now.isoformat(),
                exchange,
                symbol,
                timeframe,
                "Market data has recovered; analysis is enabled again.",
                {
                    "condition": "market_data_stale",
                    "last_completed_at": stale_timestamp,
                    "stale_limit_seconds": stale_limit,
                    "alert_key": f"market-data-stale:{stale_timestamp}:recovered",
                },
            )
        )
    if previous.scenario is None:
        events.append(WatchEvent("initial_snapshot", "info", now.isoformat(), exchange, symbol, timeframe, f"Initial market scenario: {result.scenario}.", details))
    elif previous.scenario != result.scenario:
        events.append(WatchEvent("scenario_changed", "warning", now.isoformat(), exchange, symbol, timeframe, f"Scenario changed from {previous.scenario} to {result.scenario}.", details))
    if trigger_key is not None and previous.trigger_key != trigger_key:
        events.append(WatchEvent("trade_trigger", "warning", now.isoformat(), exchange, symbol, timeframe, f"New paper-review trigger: {result.scenario}.", details))
    return state, events


def load_state(path: Path) -> WatchState:
    """Load watcher state; a missing file means this is the first observation."""
    if not path.exists():
        return WatchState()
    return WatchState(**json.loads(path.read_text(encoding="utf-8")))


def save_state(path: Path, state: WatchState) -> None:
    """Persist state atomically so interruption cannot leave partial JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(state), sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def run_once(
    *, exchange: str, symbol: str, timeframe: str, limit: int, lookback: int,
    state_path: Path, fetcher: Callable[..., dict[str, Any]] = fetch_public_ohlcv,
    now: datetime | None = None,
    observer: Callable[[WatchEvent, Sequence[Bar]], None] | None = None,
) -> list[WatchEvent]:
    """Fetch and evaluate one cycle, persisting state after successful evaluation."""
    previous = load_state(state_path)
    payload = fetcher(exchange_id=exchange, symbol=symbol, timeframe=timeframe, limit=limit)
    state, events = evaluate_market_payload(payload, previous, lookback=lookback, now=now or _utc_now())
    if observer is not None:
        completed = _parse_completed_bars(payload)
        for event in events:
            observer(event, completed)
    save_state(state_path, state)
    return events


class NotificationQueuePersistenceError(RuntimeError):
    """Raised when a watch event cannot be durably recorded."""


def _flush_outbox(outbox: Any, notifier: Any) -> None:
    """Retry due messages on every watcher cycle, even when no new event occurs."""
    try:
        results = outbox.deliver_pending(notifier)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event_type": "notification_queue_failed",
                    "severity": "error",
                    "channel": "telegram",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return

    for result in results:
        if result.status in {"pending", "dead"} and result.error_type:
            print(
                json.dumps(
                    {
                        "event_type": "notification_delivery_failed",
                        "severity": "error",
                        "channel": "telegram",
                        "notification_id": result.notification_id,
                        "attempts": result.attempts,
                        "status": result.status,
                        "error_type": result.error_type,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )


def _emit(
    event: WatchEvent,
    notifier: Any | None = None,
    bars: Sequence[Bar] = (),
    outbox: Any | None = None,
) -> None:
    """Log an event and durably queue Telegram delivery before advancing state."""
    print(json.dumps(asdict(event), sort_keys=True, ensure_ascii=False), flush=True)
    if notifier is None:
        return

    image = None
    if bars and event.details.get("scenario"):
        try:
            from .watch_chart import render_watch_chart

            image = render_watch_chart(event, bars)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event_type": "chart_render_failed",
                        "severity": "warning",
                        "error_type": type(exc).__name__,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    if outbox is None:
        # Compatibility fallback for callers that explicitly use _emit outside main().
        try:
            notifier.send(event, image=image)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event_type": "notification_delivery_failed",
                        "severity": "error",
                        "channel": "telegram",
                        "error_type": type(exc).__name__,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        return

    try:
        outbox.enqueue(event, image=image)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event_type": "notification_queue_failed",
                    "severity": "error",
                    "channel": "telegram",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        # run_once calls observers before saving state. Propagating here means the
        # same candle is evaluated again after recovery instead of silently skipped.
        raise NotificationQueuePersistenceError(
            "notification event could not be persisted"
        ) from exc


def _monitor_operational_health(
    outbox: Any,
    monitor: OperationalAlertMonitor,
    notifier: Any,
    args: argparse.Namespace,
    *,
    backlog_threshold: int,
) -> None:
    """Convert outbox health transitions into durable, deduplicated Telegram events."""
    try:
        conditions = notification_health_conditions(
            outbox.summary(), backlog_threshold=backlog_threshold
        )

        def emit_transition(transition: Any) -> None:
            event_type = (
                "operational_alert"
                if transition.transition == "active"
                else "operational_recovery"
            )
            _emit(
                WatchEvent(
                    event_type,
                    transition.severity,
                    transition.occurred_at,
                    args.exchange,
                    args.symbol,
                    args.timeframe,
                    transition.message,
                    transition.details,
                ),
                notifier,
                outbox=outbox,
            )

        monitor.reconcile(conditions, emit=emit_transition)
    except Exception as exc:
        # The monitor must never crash the watcher; the durable transition is retried
        # on a later cycle if enqueueing or state persistence fails.
        print(
            json.dumps(
                {
                    "event_type": "operational_monitor_failed",
                    "severity": "error",
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            ),
            flush=True,
        )


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the paper-only Emergenzix watch agent")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--exchange", default=os.getenv("EMERGENZIX_WATCH_EXCHANGE", "binance"))
    parser.add_argument("--symbol", default=os.getenv("EMERGENZIX_WATCH_SYMBOL", "BTCUSDT"))
    parser.add_argument("--timeframe", default=os.getenv("EMERGENZIX_WATCH_TIMEFRAME", "4h"))
    parser.add_argument("--limit", type=int, default=int(os.getenv("EMERGENZIX_WATCH_LIMIT", "200")))
    parser.add_argument("--lookback", type=int, default=int(os.getenv("EMERGENZIX_WATCH_LOOKBACK", "20")))
    parser.add_argument("--interval", type=int, default=int(os.getenv("EMERGENZIX_WATCH_INTERVAL_SECONDS", "300")))
    parser.add_argument("--state-path", type=Path, default=Path(os.getenv("EMERGENZIX_WATCH_STATE_PATH", "/data/watch-state.json")))
    parser.add_argument(
        "--notification-backlog-threshold",
        type=int,
        default=int(os.getenv("EMERGENZIX_NOTIFICATION_BACKLOG_THRESHOLD", "20")),
    )
    parser.add_argument(
        "--operational-alert-state-path",
        type=Path,
        default=(
            Path(os.environ["EMERGENZIX_OPERATIONAL_ALERT_STATE_PATH"])
            if os.getenv("EMERGENZIX_OPERATIONAL_ALERT_STATE_PATH")
            else None
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    if args.interval < 10:
        raise ValueError("watch interval must be at least 10 seconds")
    backlog_threshold = getattr(args, "notification_backlog_threshold", 20)
    if backlog_threshold < 1:
        raise ValueError("notification backlog threshold must be at least one")
    token = os.getenv("EMERGENZIX_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("EMERGENZIX_TELEGRAM_CHAT_ID", "").strip()
    telegram_required = _env_bool("EMERGENZIX_TELEGRAM_REQUIRED", default=False)
    if bool(token) != bool(chat_id):
        _emit(WatchEvent(
            "risk_alert", "error", _utc_now().isoformat(), args.exchange,
            args.symbol, args.timeframe,
            "Telegram configuration is incomplete; watcher will not start.",
            {"error_type": "IncompleteTelegramConfiguration"},
        ))
        return 2
    if telegram_required and not (token and chat_id):
        _emit(WatchEvent(
            "risk_alert", "error", _utc_now().isoformat(), args.exchange,
            args.symbol, args.timeframe,
            "Telegram is required but bot token/chat ID are missing; watcher will not start.",
            {"error_type": "MissingTelegramConfiguration"},
        ))
        return 2

    notifier = None
    outbox = None
    if token and chat_id:
        from .notification_outbox import NotificationOutbox
        from .notifications import TelegramNotifier

        notifier = TelegramNotifier(bot_token=token, chat_id=chat_id)
        outbox_path = Path(
            os.getenv(
                "EMERGENZIX_NOTIFICATION_DB_PATH",
                str(args.state_path.parent / "notification-outbox.sqlite3"),
            )
        )
        outbox = NotificationOutbox(outbox_path)
        operational_state_path = getattr(args, "operational_alert_state_path", None)
        if operational_state_path is None:
            operational_state_path = Path(
                os.getenv(
                    "EMERGENZIX_OPERATIONAL_ALERT_STATE_PATH",
                    str(args.state_path.with_name("operational-alert-state.json")),
                )
            )
        operational_monitor = OperationalAlertMonitor(operational_state_path)
        startup_event = WatchEvent(
            "watcher_startup", "info", _utc_now().isoformat(), args.exchange,
            args.symbol, args.timeframe,
            "Telegram delivery check passed; paper-only watcher is starting.",
            {"mode": "PAPER", "live_trading_enabled": False},
        )
        try:
            startup_id = outbox.enqueue(startup_event)
            startup_result = outbox.deliver_one(
                notifier, startup_id, force=True
            )
        except Exception as exc:
            _emit(WatchEvent(
                "risk_alert", "error", _utc_now().isoformat(), args.exchange,
                args.symbol, args.timeframe,
                "Telegram startup delivery check failed; watcher will not start.",
                {"error_type": type(exc).__name__},
            ))
            return 2
        if startup_result.status != "delivered":
            _emit(WatchEvent(
                "risk_alert", "error", _utc_now().isoformat(), args.exchange,
                args.symbol, args.timeframe,
                "Telegram startup delivery check failed; watcher will not start.",
                {
                    "error_type": startup_result.error_type or "TelegramDeliveryUnavailable",
                    "notification_id": startup_result.notification_id,
                },
            ))
            return 2
    while True:
        try:
            run_once(
                exchange=args.exchange, symbol=args.symbol, timeframe=args.timeframe,
                limit=args.limit, lookback=args.lookback, state_path=args.state_path,
                observer=lambda event, bars: _emit(event, notifier, bars, outbox),
            )
        except Exception as exc:  # process boundary: report and retry without trading
            try:
                _emit(
                    WatchEvent(
                        "risk_alert", "error", _utc_now().isoformat(),
                        args.exchange, args.symbol, args.timeframe,
                        "Watch cycle failed; no trading action was taken.",
                        {"error_type": type(exc).__name__},
                    ),
                    notifier,
                    outbox=outbox,
                )
            except NotificationQueuePersistenceError:
                # The original failure is already logged; do not recurse if the
                # queue itself is unavailable. Docker restart policy remains active.
                pass
            if args.once:
                return 1
        if notifier is not None and outbox is not None:
            _flush_outbox(outbox, notifier)
            _monitor_operational_health(
                outbox,
                operational_monitor,
                notifier,
                args,
                backlog_threshold=backlog_threshold,
            )
            _flush_outbox(outbox, notifier)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
