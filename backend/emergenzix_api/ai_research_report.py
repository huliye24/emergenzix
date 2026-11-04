"""Scheduled, advisory-only DeepSeek market research reports."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .deepseek_analysis import (
    DeepSeekAnalysisError,
    DeepSeekConfig,
    analyze_research_evidence,
)
from .market_ai import attach_deepseek_market_analysis
from .market_data import fetch_public_ohlcv
from .market_intelligence import build_market_intelligence
from .notification_outbox import NotificationOutbox
from .notifications import TelegramNotifier
from .telegram_credentials import load_follow_telegram_bot_token
from .watch_agent import WatchEvent


PROTOCOL_VERSION = "btc-4h-deepseek-report-v1"
MarketFetcher = Callable[..., dict[str, Any]]
Analyzer = Callable[..., dict[str, Any]]


def run_ai_research_report(
    *,
    output_dir: str | Path,
    outbox: NotificationOutbox,
    market_fetcher: MarketFetcher = fetch_public_ohlcv,
    analyzer: Analyzer = analyze_research_evidence,
    model: str = "deepseek-flash",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create at most one report per completed candle and queue it once."""
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    market_payload = market_fetcher(
        exchange_id="binance", symbol="BTCUSDT", timeframe="4h", limit=200
    )
    snapshot = build_market_intelligence(market_payload, now=observed_at)
    completed_at = str(snapshot["freshness"]["latest_completed_at"])
    report_id = _report_id(completed_at=completed_at, model=model)
    target_dir = Path(output_dir)
    target = target_dir / f"{report_id}.json"
    created = False
    if target.exists():
        report = json.loads(target.read_text(encoding="utf-8"))
        if report.get("id") != report_id:
            raise ValueError("stored AI report identity mismatch")
    else:
        enriched = attach_deepseek_market_analysis(snapshot, analyzer=analyzer)
        advisory = enriched.get("ai_analysis")
        if not isinstance(advisory, Mapping) or advisory.get("status") != "verified_advisory":
            reason = advisory.get("reason", "unavailable") if isinstance(advisory, Mapping) else "unavailable"
            raise DeepSeekAnalysisError(str(reason))
        report = {
            "id": report_id,
            "protocol_version": PROTOCOL_VERSION,
            "generated_at": observed_at.isoformat(),
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "latest_completed_at": completed_at,
            "price": enriched.get("price"),
            "market_state": enriched.get("market_state"),
            "signal": enriched.get("signal"),
            "indicators": enriched.get("indicators"),
            "analysis": advisory.get("analysis"),
            "provider": advisory.get("provider"),
            "model": advisory.get("model"),
            "advisory_only": True,
            "automatic_strategy_promotion": False,
            "paper_order_permission": False,
            "testnet_order_permission": False,
            "live_order_permission": False,
        }
        _write_json_atomic(target, report)
        created = True
    notification_id = queue_ai_research_report(outbox, report)
    return {
        "report_id": report_id,
        "report_path": str(target),
        "created": created,
        "notification_id": notification_id,
        "latest_completed_at": completed_at,
    }


def queue_ai_research_report(
    outbox: NotificationOutbox, report: Mapping[str, Any]
) -> str:
    analysis = report.get("analysis")
    if not isinstance(analysis, Mapping):
        raise ValueError("AI report analysis is unavailable")
    event = WatchEvent(
        event_type="ai_research_report",
        severity="info",
        occurred_at=str(report["generated_at"]),
        exchange=str(report["exchange"]),
        symbol=str(report["symbol"]),
        timeframe=str(report["timeframe"]),
        message="Scheduled DeepSeek research report is available.",
        details={
            "alert_key": f"ai-research:{report['id']}",
            "report_id": report["id"],
            "latest_completed_at": report["latest_completed_at"],
            "model": report["model"],
            "summary": analysis.get("summary"),
            "observations": list(analysis.get("observations", []))[:4],
            "risks": list(analysis.get("risks", []))[:4],
            "limitations": list(analysis.get("limitations", []))[:3],
            "advisory_only": True,
            "paper_order_permission": False,
            "testnet_order_permission": False,
            "live_order_permission": False,
        },
    )
    return outbox.enqueue(event)


def _report_id(*, completed_at: str, model: str) -> str:
    identity = json.dumps(
        {
            "protocol_version": PROTOCOL_VERSION,
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "latest_completed_at": completed_at,
            "model": model,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    try:
        config = DeepSeekConfig.from_env()
        token = load_follow_telegram_bot_token()
        chat_id = os.getenv("EMERGENZIX_FOLLOW_TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            raise ValueError("AI report Telegram configuration is incomplete")
        outbox = NotificationOutbox(
            os.getenv(
                "EMERGENZIX_AI_REPORT_OUTBOX_PATH",
                "/home/ubuntu/.local/share/emergenzix-ai/report-outbox.sqlite3",
            )
        )
        analyzer = lambda **kwargs: analyze_research_evidence(  # noqa: E731
            **kwargs, config=config
        )
        result = run_ai_research_report(
            output_dir=os.getenv(
                "EMERGENZIX_AI_REPORT_DIR",
                "/home/ubuntu/.local/share/emergenzix-ai/reports",
            ),
            outbox=outbox,
            analyzer=analyzer,
            model=config.model,
        )
        notifier = TelegramNotifier(bot_token=token, chat_id=chat_id)
        deliveries = outbox.deliver_pending(notifier)
        print(
            json.dumps(
                {
                    **result,
                    "deliveries": [
                        {
                            "notification_id": item.notification_id,
                            "status": item.status,
                            "attempts": item.attempts,
                            "error_type": item.error_type,
                        }
                        for item in deliveries
                    ],
                    "paper_order_permission": False,
                    "live_order_permission": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except (DeepSeekAnalysisError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "error", "error_type": type(exc).__name__},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
