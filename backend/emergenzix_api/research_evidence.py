"""Conservative, read-only screening of immutable BTC date-range research records.

This is a descriptive research evidence check, never an execution permission.
It does not reselect candidates or mutate the persisted experiment protocol.
"""

from __future__ import annotations

import math
from typing import Any


EVIDENCE_ASSESSMENT_VERSION = "date-range-evidence-v1"
SUPPORTED_PROTOCOL = "btc-date-range-backtest-v4"
_EXPECTED_COST_PAIRS = {
    (float(fee), float(slippage))
    for fee in (5, 10, 20)
    for slippage in (0, 5, 10)
}


class EvidenceAssessmentError(ValueError):
    """An existing record cannot be interpreted safely under this assessment."""


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceAssessmentError("unsupported research evidence shape")
    return value


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceAssessmentError("invalid research evidence count")
    return value


def _finite(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceAssessmentError("invalid research evidence metric")
    number = float(value)
    if not math.isfinite(number):
        raise EvidenceAssessmentError("non-finite research evidence metric")
    return number


def _require_false(data: dict[str, Any], key: str) -> None:
    if data.get(key) is not False:
        raise EvidenceAssessmentError("unsafe or unsupported research evidence protocol")


def assess_saved_backtest_evidence(record: dict[str, Any]) -> dict[str, Any]:
    """Screen an already verified, immutable v4 report without placing any orders.

    A 'checks_met' verdict is descriptive only; never sufficient for promotion,
    confidence claims, TESTNET access, or LIVE order authorization.
    """
    if (
        record.get("record_type") != "btc_date_range_backtest"
        or record.get("protocol_version") != SUPPORTED_PROTOCOL
        or not isinstance(record.get("id"), str)
        or len(record["id"]) != 64
        or any(ch not in "0123456789abcdef" for ch in record["id"])
    ):
        raise EvidenceAssessmentError("unsupported research record protocol or identity")

    report = _object(record.get("report"))
    if report.get("protocol_version") != SUPPORTED_PROTOCOL:
        raise EvidenceAssessmentError("inconsistent research protocol version")
    safety = _object(report.get("safety"))
    if safety.get("mode") != "PAPER":
        raise EvidenceAssessmentError("unsafe or unsupported research evidence mode")
    _require_false(safety, "orders_created")
    _require_false(safety, "live_trading_enabled")
    _require_false(safety, "future_visible_during_selection")
    _require_false(safety, "test_period_used_for_selection")

    period = _object(report.get("period"))
    if period.get("test_data_complete") is not True:
        raise EvidenceAssessmentError("test data coverage is not confirmed")
    configuration = _object(report.get("configuration"))
    min_trades = _count(configuration.get("minimum_validation_trades"))
    if min_trades != 2:  # Pinned by the supported v4 backtest protocol.
        raise EvidenceAssessmentError("unsupported validation trade threshold")

    selection = _object(report.get("strategy_selection"))
    _require_false(selection, "future_visible_during_selection")
    selected_id = _object(selection.get("selected_candidate")).get("candidate_id")
    if not isinstance(selected_id, str) or not selected_id:
        raise EvidenceAssessmentError("missing selected candidate")
    candidates = selection.get("candidates")
    if not isinstance(candidates, list):
        raise EvidenceAssessmentError("missing pre-test candidate evidence")
    matches = [
        item for item in candidates
        if isinstance(item, dict) and item.get("candidate_id") == selected_id
    ]
    if len(matches) != 1:
        raise EvidenceAssessmentError("ambiguous selected candidate evidence")
    validation_trades = _count(matches[0].get("number_of_trades"))
    if matches[0].get("eligible") is not (validation_trades >= min_trades):
        raise EvidenceAssessmentError("inconsistent pre-test eligibility")
    selection_status = selection.get("status")
    if selection_status not in {
        "selected_with_minimum_trade_evidence",
        "insufficient_validation_trades",
    }:
        raise EvidenceAssessmentError("unsupported selection status")
    if (selection_status == "selected_with_minimum_trade_evidence") != (
        validation_trades >= min_trades
    ):
        raise EvidenceAssessmentError("inconsistent pre-test selection status")
    pretest_sample_met = validation_trades >= min_trades

    holdout = _object(report.get("test_result"))
    holdout_trades = _count(holdout.get("number_of_trades"))
    holdout_return = _finite(holdout.get("total_return_pct"))

    walk = _object(report.get("walk_forward_validation"))
    if (
        walk.get("method") != "three_fold_expanding_window"
        or walk.get("folds_count") != 3
    ):
        raise EvidenceAssessmentError("unsupported walk-forward protocol")
    _require_false(walk, "future_visible_during_fold_selection")
    _require_false(walk, "automatic_promotion")
    folds = walk.get("folds")
    if not isinstance(folds, list) or len(folds) != 3:
        raise EvidenceAssessmentError("incomplete walk-forward folds")
    fold_candidates: list[str] = []
    fold_returns: list[float] = []
    fold_trades: list[int] = []
    for index, fold_raw in enumerate(folds, start=1):
        fold = _object(fold_raw)
        if fold.get("fold") != index:
            raise EvidenceAssessmentError("invalid walk-forward fold order")
        candidate = _object(fold.get("selected_candidate")).get("candidate_id")
        if not isinstance(candidate, str) or not candidate:
            raise EvidenceAssessmentError("invalid walk-forward candidate")
        metrics = _object(fold.get("metrics"))
        trades = _count(metrics.get("number_of_trades"))
        outcome = _finite(metrics.get("total_return_pct"))
        evidence_label = (
            "sufficient_completed_trades"
            if trades >= min_trades else "insufficient_completed_trades"
        )
        if fold.get("evidence_status") != evidence_label:
            raise EvidenceAssessmentError("inconsistent walk-forward evidence label")
        fold_candidates.append(candidate)
        fold_returns.append(outcome)
        fold_trades.append(trades)
    aggregate = _object(walk.get("aggregate"))
    evidenced_folds = sum(trades >= min_trades for trades in fold_trades)
    if _count(aggregate.get("folds_with_minimum_trade_evidence")) != evidenced_folds:
        raise EvidenceAssessmentError("inconsistent walk-forward trade aggregate")
    stable_candidate = len(set(fold_candidates)) == 1 and fold_candidates[0] == selected_id
    if aggregate.get("same_candidate_selected_in_all_folds") is not (
        len(set(fold_candidates)) == 1
    ):
        raise EvidenceAssessmentError("inconsistent walk-forward candidate aggregate")

    stress = _object(report.get("cost_sensitivity"))
    if stress.get("method") != "fixed_fee_slippage_matrix_after_candidate_selection":
        raise EvidenceAssessmentError("unsupported cost stress protocol")
    _require_false(stress, "strategy_reselected_per_scenario")
    _require_false(stress, "test_window_changed_per_scenario")
    if stress.get("stress_test_only") is not True:
        raise EvidenceAssessmentError("cost scenarios not marked stress-test only")
    scenarios = stress.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 9:
        raise EvidenceAssessmentError("incomplete cost stress matrix")
    pairs: set[tuple[float, float]] = set()
    stress_returns: list[float] = []
    stress_trade_counts: list[int] = []
    for raw in scenarios:
        item = _object(raw)
        if item.get("candidate_id") != selected_id:
            raise EvidenceAssessmentError("cost stress reselected a different candidate")
        pair = (
            _finite(item.get("fee_bps_per_side")),
            _finite(item.get("slippage_bps_per_side")),
        )
        if pair not in _EXPECTED_COST_PAIRS or pair in pairs:
            raise EvidenceAssessmentError("invalid cost stress scenario coverage")
        pairs.add(pair)
        stress_returns.append(_finite(item.get("net_return_pct")))
        stress_trade_counts.append(_count(item.get("number_of_trades")))
    if pairs != _EXPECTED_COST_PAIRS:
        raise EvidenceAssessmentError("incomplete cost stress coverage")

    positive_folds = sum(outcome > 0 for outcome in fold_returns)
    positive_stress_scenarios = sum(outcome > 0 for outcome in stress_returns)
    # These are deliberately transparent screening heuristics, not significance tests.
    checks = {
        "pretest_trade_sample": pretest_sample_met,
        "holdout_trade_sample": holdout_trades >= min_trades,
        "positive_holdout_return": holdout_return > 0,
        "walk_forward_trade_coverage": evidenced_folds >= 2,
        "walk_forward_positive_folds": positive_folds >= 2,
        "walk_forward_candidate_consistency": stable_candidate,
        "cost_stress_positive_grid": (
            positive_stress_scenarios == 9
            and min(stress_trade_counts) >= min_trades
        ),
    }
    checks_met = all(checks.values())
    return {
        "assessment_version": EVIDENCE_ASSESSMENT_VERSION,
        "record_id": record["id"],
        "record_sha256": record.get("record_sha256"),
        "protocol_version": SUPPORTED_PROTOCOL,
        "screening_status": "checks_met" if checks_met else "limited_evidence",
        "screening_checks": checks,
        "observations": {
            "validation_completed_trades": validation_trades,
            "holdout_completed_trades": holdout_trades,
            "holdout_net_return_pct": holdout_return,
            "walk_forward_folds_with_trade_evidence": evidenced_folds,
            "walk_forward_positive_folds": positive_folds,
            "walk_forward_same_selected_candidate": stable_candidate,
            "stress_scenarios_positive": positive_stress_scenarios,
            "stress_scenarios_total": len(stress_returns),
            "worst_stress_net_return_pct": min(stress_returns),
        },
        "automatic_strategy_promotion": False,
        "paper_order_permission": False,
        "testnet_order_permission": False,
        "live_order_permission": False,
        "limitations": [
            "These thresholds are screening heuristics; two completed trades do not establish statistical significance.",
            "Walk-forward folds select their own candidates and are pre-test diagnostics, not multiple independent future tests.",
            "Historical net returns and fixed cost scenarios cannot establish future profitability.",
            "Passing every check does not authorize paper, testnet, or live order execution.",
        ],
    }
