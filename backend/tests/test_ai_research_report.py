from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from emergenzix_api.ai_research_report import _record_sha256, run_ai_research_report
from emergenzix_api.notification_outbox import NotificationOutbox
from emergenzix_api.notifications import format_watch_event
from emergenzix_api.watch_agent import WatchEvent


NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


def _evidence(*, generated_at=NOW):
    value = {
        "schema_version": "emergenzix-strategy-evidence/v1", "generated_at": generated_at.isoformat(),
        "market": {"exchange": "binance", "symbol": "BTCUSDT", "timeframe": "4h",
                   "completed_at": "2026-11-01T08:00:00+00:00", "reference_close": 76500.0},
        "strategy": {"name": "turtle_breakout", "version": "v1", "decision": "HOLD",
                     "reason": "No completed breakout."},
        "prediction": {"direction": "NEUTRAL", "horizon": "4h", "agreement": "mixed"},
        "portfolio": {"position": "FLAT", "exposure_pct": 0.0},
        "risk": {"status": "PASS", "stale_data": False},
        "evaluation": {"paper_vs_backtest_deviation": "insufficient sample",
                       "independent_decisions": 4, "minimum_decisions_before_change": 180},
        "provenance": [{"node": "aws-cpu", "role": "strategy_analysis", "revision": "abc123"},
                       {"node": "aws-gpu", "role": "model_prediction", "revision": "def456"}],
        "order_permission": False,
    }
    value["record_sha256"] = _record_sha256(value)
    return value


def _analyzer(**kwargs):
    assert kwargs["evidence"]["strategy"]["decision"] == "HOLD"
    return {"provider": "deepseek", "model": "deepseek-flash", "analysis": {
        "summary": "策略维持观望，模型意见不一致。", "observations": ["策略没有确认突破。"],
        "risks": ["独立决策样本不足。"], "data_quality": ["来源记录完整。"],
        "limitations": ["不能据此评价盈利能力。"], "recommended_research": ["继续积累 PAPER 决策。"]}}


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_report_compiles_verified_aws_evidence_and_queues_once(tmp_path):
    evidence_path = tmp_path / "latest.json"
    _write(evidence_path, _evidence())
    outbox = NotificationOutbox(tmp_path / "outbox.sqlite3")
    kwargs = {"evidence_path": evidence_path, "output_dir": tmp_path / "reports", "outbox": outbox,
              "analyzer": _analyzer, "now": NOW}
    first = run_ai_research_report(**kwargs)
    second = run_ai_research_report(**kwargs)
    assert first["created"] is True and second["created"] is False
    assert first["report_id"] == second["report_id"] and outbox.pending_count() == 1
    report = json.loads(
        (tmp_path / "reports" / f"{first['report_id']}.json").read_text(encoding="utf-8")
    )
    assert report["strategy"]["decision"] == "HOLD"
    assert report["deepseek_role"] == "secondary_reviewer_and_reporter"
    assert report["paper_order_permission"] is False and report["live_order_permission"] is False


@pytest.mark.parametrize("mutation, message", [
    (lambda value: value["provenance"].pop(), "analysis and model prediction"),
    (lambda value: value.update(generated_at=(NOW - timedelta(hours=9)).isoformat()), "stale"),
    (lambda value: value["strategy"].update(decision="WAIT"), "decision is invalid"),
])
def test_report_rejects_incomplete_or_stale_upstream_evidence(tmp_path, mutation, message):
    value = _evidence()
    mutation(value)
    value["record_sha256"] = _record_sha256(value)
    evidence_path = tmp_path / "latest.json"
    _write(evidence_path, value)
    with pytest.raises(ValueError, match=message):
        run_ai_research_report(evidence_path=evidence_path, output_dir=tmp_path / "reports",
            outbox=NotificationOutbox(tmp_path / "outbox.sqlite3"), analyzer=_analyzer, now=NOW)


def test_report_rejects_tampered_evidence_without_calling_deepseek(tmp_path):
    value = _evidence()
    value["strategy"]["reason"] = "tampered after signing"
    evidence_path = tmp_path / "latest.json"
    _write(evidence_path, value)
    called = False

    def analyzer(**_kwargs):
        nonlocal called
        called = True

    with pytest.raises(ValueError, match="hash mismatch"):
        run_ai_research_report(evidence_path=evidence_path, output_dir=tmp_path / "reports",
            outbox=NotificationOutbox(tmp_path / "outbox.sqlite3"), analyzer=analyzer, now=NOW)
    assert called is False


def test_report_notification_uses_strategy_first_template(tmp_path):
    evidence_path = tmp_path / "latest.json"
    _write(evidence_path, _evidence())
    outbox_path = tmp_path / "outbox.sqlite3"
    result = run_ai_research_report(evidence_path=evidence_path, output_dir=tmp_path / "reports",
        outbox=NotificationOutbox(outbox_path), analyzer=_analyzer, now=NOW)
    connection = sqlite3.connect(outbox_path)
    try:
        payload = connection.execute("SELECT event_json FROM notification_outbox WHERE id = ?",
                                     (result["notification_id"],)).fetchone()[0]
    finally:
        connection.close()
    message = format_watch_event(WatchEvent(**json.loads(payload)))
    assert "策略：HOLD" in message
    assert "模型：NEUTRAL" in message
    assert "操作：继续观察，不自动下单。" in message
    assert "PAPER 研究记录｜非投资建议" in message
    assert "报告ID" not in message
    assert "复核风险" not in message
    assert len(message) < 600
