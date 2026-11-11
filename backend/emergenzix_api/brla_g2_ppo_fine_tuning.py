"""Fail-closed authorization for PPO fine-tuning from a behavioral warm start."""

from __future__ import annotations

from typing import Mapping

PROTOCOL = "emergenzix-brla-g2-ppo-fine-tuning-v1"


def authorize_warm_start(report: Mapping[str, object], model_file_sha256: str) -> None:
    if report.get("protocol") != "emergenzix-brla-g2-behavioral-warm-start-v3":
        raise PermissionError("PPO fine-tuning requires behavioral warm-start v3")
    if report.get("decision") != "READY_FOR_PPO_FINE_TUNING":
        raise PermissionError("warm-start report is not qualified for PPO fine-tuning")
    if report.get("model_file_sha256") != model_file_sha256:
        raise PermissionError("warm-start model hash does not match its qualification report")
    if (report.get("mode") != "PAPER" or report.get("automatic_promotion") is not False
            or report.get("position_permission") is not False
            or report.get("order_permission") is not False):
        raise PermissionError("warm-start safety boundary is invalid")
