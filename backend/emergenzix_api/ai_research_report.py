"""Compile verified AWS strategy evidence into a bounded DeepSeek briefing."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .deepseek_analysis import DeepSeekAnalysisError, DeepSeekConfig, analyze_research_evidence
from .notification_outbox import NotificationOutbox
from .notifications import TelegramNotifier
from .telegram_credentials import load_follow_telegram_bot_token
from .watch_agent import WatchEvent


EVIDENCE_SCHEMA_VERSION = "emergenzix-strategy-evidence/v1"
REPORT_PROTOCOL_VERSION = "emergenzix-strategy-briefing/v2"
Analyzer = Callable[..., dict[str, Any]]


def run_ai_research_report(
    *, evidence_path: str | Path, output_dir: str | Path,
    outbox: NotificationOutbox, analyzer: Analyzer = analyze_research_evidence,
    model: str = "deepseek-flash", now: datetime | None = None,
) -> dict[str, Any]:
    """Summarize one verified strategy artifact; never perform market analysis."""
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    evidence = _load_verified_strategy_evidence(Path(evidence_path), now=observed_at)
    report_id = _report_id(evidence=evidence, model=model)
    target = Path(output_dir) / f"{report_id}.json"
    created = False
    if target.exists():
        report = json.loads(target.read_text(encoding="utf-8"))
        if report.get("id") != report_id:
            raise ValueError("stored report identity mismatch")
    else:
        reviewed = analyzer(
            title="Emergenzix strategy evidence briefing",
            question=("Review the supplied strategy calculation and prediction. Explain the decision, "
                      "disagreements, risks, data limitations, and PAPER/backtest deviation. "
                      "Do not independently analyze the market or invent signals. Write the summary "
                      "in Simplified Chinese using at most 80 Chinese characters. Keep every list item "
                      "in Simplified Chinese and under 40 Chinese characters."),
            evidence=evidence,
        )
        analysis = reviewed.get("analysis")
        if not isinstance(analysis, Mapping):
            raise DeepSeekAnalysisError("invalid_response")
        report = {
            "id": report_id, "protocol_version": REPORT_PROTOCOL_VERSION,
            "generated_at": observed_at.isoformat(), "evidence_record_sha256": evidence["record_sha256"],
            "market": evidence["market"], "strategy": evidence["strategy"],
            "prediction": evidence["prediction"], "portfolio": evidence["portfolio"],
            "risk": evidence["risk"], "evaluation": evidence["evaluation"],
            "microstructure": evidence.get("microstructure"),
            "provenance": evidence["provenance"], "judgement": dict(analysis),
            "provider": reviewed.get("provider"), "model": reviewed.get("model"),
            "deepseek_role": "secondary_reviewer_and_reporter", "advisory_only": True,
            "automatic_strategy_promotion": False, "paper_order_permission": False,
            "testnet_order_permission": False, "live_order_permission": False,
        }
        _write_json_atomic(target, report)
        created = True
    notification_id = queue_ai_research_report(outbox, report)
    return {"report_id": report_id, "report_path": str(target), "created": created,
            "notification_id": notification_id, "evidence_record_sha256": evidence["record_sha256"]}


def _load_verified_strategy_evidence(path: Path, *, now: datetime) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > 100_000:
        raise ValueError("strategy evidence is missing or too large")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise ValueError("unsupported strategy evidence schema")
    if payload.get("record_sha256") != _record_sha256(payload):
        raise ValueError("strategy evidence hash mismatch")
    required_objects = ("market", "strategy", "prediction", "portfolio", "risk", "evaluation")
    if any(not isinstance(payload.get(key), dict) for key in required_objects):
        raise ValueError("strategy evidence objects are incomplete")
    provenance = payload.get("provenance")
    if not isinstance(provenance, list) or not provenance:
        raise ValueError("strategy evidence provenance is required")
    roles = {str(item.get("role")) for item in provenance if isinstance(item, dict)}
    if not {"strategy_analysis", "model_prediction"}.issubset(roles):
        raise ValueError("AWS strategy analysis and model prediction are both required")
    generated_at = _timestamp(payload.get("generated_at"), "generated_at")
    if generated_at > now + timedelta(minutes=5) or now - generated_at > timedelta(hours=8):
        raise ValueError("strategy evidence is stale")
    market = payload["market"]
    if not all(str(market.get(key, "")).strip() for key in ("symbol", "timeframe", "completed_at")):
        raise ValueError("market identity is incomplete")
    strategy = payload["strategy"]
    if strategy.get("decision") not in {"BUY", "SELL", "HOLD"}:
        raise ValueError("strategy decision is invalid")
    if not all(str(strategy.get(key, "")).strip() for key in ("name", "version", "reason")):
        raise ValueError("strategy identity and reason are required")
    evaluation = payload["evaluation"]
    decisions, minimum = evaluation.get("independent_decisions"), evaluation.get("minimum_decisions_before_change")
    if not isinstance(decisions, int) or not isinstance(minimum, int) or decisions < 0 or minimum < 1:
        raise ValueError("evaluation decision counts are invalid")
    if payload.get("order_permission") is not False:
        raise ValueError("strategy evidence must deny order permission")
    return payload


def queue_ai_research_report(outbox: NotificationOutbox, report: Mapping[str, Any]) -> str:
    judgement = report["judgement"]
    event = WatchEvent(
        event_type="ai_research_report", severity="info", occurred_at=str(report["generated_at"]),
        exchange=str(report["market"].get("exchange", "-")), symbol=str(report["market"]["symbol"]),
        timeframe=str(report["market"]["timeframe"]), message="Strategy evidence briefing is available.",
        details={"alert_key": f"ai-research:{report['id']}", "report_id": report["id"],
                 "completed_at": report["market"]["completed_at"], "model": report["model"],
                 "strategy_name": report["strategy"]["name"], "strategy_version": report["strategy"]["version"],
                 "decision": report["strategy"]["decision"], "decision_reason": report["strategy"]["reason"],
                 "prediction": report["prediction"], "portfolio": report["portfolio"], "risk": report["risk"],
                 "microstructure": report.get("microstructure"),
                 "evaluation": report["evaluation"], "summary": judgement.get("summary"),
                 "risks": list(judgement.get("risks", []))[:3],
                 "limitations": list(judgement.get("limitations", []))[:2],
                 "deepseek_role": "secondary_reviewer_and_reporter", "paper_order_permission": False,
                 "testnet_order_permission": False, "live_order_permission": False},
    )
    return outbox.enqueue(event)


def _record_sha256(payload: Mapping[str, Any]) -> str:
    value = dict(payload)
    value.pop("record_sha256", None)
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _report_id(*, evidence: Mapping[str, Any], model: str) -> str:
    return hashlib.sha256(f"{REPORT_PROTOCOL_VERSION}:{evidence['record_sha256']}:{model}".encode()).hexdigest()


def _timestamp(value: object, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    try:
        config = DeepSeekConfig.from_env()
        token = load_follow_telegram_bot_token()
        chat_id = os.getenv("EMERGENZIX_FOLLOW_TELEGRAM_CHAT_ID", "").strip()
        evidence_path = os.getenv("EMERGENZIX_STRATEGY_EVIDENCE_PATH", "").strip()
        if not token or not chat_id or not evidence_path:
            raise ValueError("report input or Telegram configuration is incomplete")
        outbox = NotificationOutbox(os.getenv("EMERGENZIX_AI_REPORT_OUTBOX_PATH", "/home/ubuntu/.local/share/emergenzix-ai/report-outbox.sqlite3"))
        analyzer = lambda **kwargs: analyze_research_evidence(**kwargs, config=config)  # noqa: E731
        result = run_ai_research_report(evidence_path=evidence_path,
            output_dir=os.getenv("EMERGENZIX_AI_REPORT_DIR", "/home/ubuntu/.local/share/emergenzix-ai/reports"),
            outbox=outbox, analyzer=analyzer, model=config.model)
        deliveries = outbox.deliver_pending(TelegramNotifier(bot_token=token, chat_id=chat_id))
        print(json.dumps({**result, "deliveries": [{"notification_id": item.notification_id,
            "status": item.status, "attempts": item.attempts, "error_type": item.error_type}
            for item in deliveries], "paper_order_permission": False, "live_order_permission": False}, sort_keys=True))
        return 0
    except (DeepSeekAnalysisError, json.JSONDecodeError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
