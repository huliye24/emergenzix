from __future__ import annotations

import json

import httpx
import pytest
from fastapi import HTTPException

from emergenzix_api.deepseek_analysis import (
    DeepSeekAnalysisError,
    DeepSeekConfig,
    analyze_research_evidence,
)
from emergenzix_api.deepseek_analysis_api import (
    AIAnalysisCreate,
    _authorize_internal_request,
    create_ai_analysis,
)


def _content(**overrides: object) -> dict[str, object]:
    content: dict[str, object] = {
        "summary": "The sample is descriptive and insufficient for deployment.",
        "observations": ["The fixture contains 32 completed paper orders."],
        "risks": ["A single market regime can dominate the result."],
        "data_quality": ["The dataset identity should remain pinned."],
        "limitations": ["Historical performance cannot establish future returns."],
        "recommended_research": ["Add a chronological holdout."],
    }
    content.update(overrides)
    return content


def _transport(content: dict[str, object], *, finish_reason: str = "stop") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["thinking"] == {"type": "disabled"}
        assert body["stream"] is False
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": finish_reason,
                        "message": {"content": json.dumps(content)},
                    }
                ]
            },
        )

    return httpx.MockTransport(handler)


def test_analysis_is_structured_and_cannot_grant_order_permission() -> None:
    result = analyze_research_evidence(
        title="Qlib comparison",
        question="What remains uncertain?",
        evidence={"dataset_sha256": "a" * 64, "orders": 32},
        config=DeepSeekConfig(api_key="test-key"),
        transport=_transport(_content()),
    )

    assert result["advisory_only"] is True
    assert result["automatic_strategy_promotion"] is False
    assert result["paper_order_permission"] is False
    assert result["testnet_order_permission"] is False
    assert result["live_order_permission"] is False


def test_model_permission_field_is_rejected_fail_closed() -> None:
    with pytest.raises(DeepSeekAnalysisError, match="invalid_response"):
        analyze_research_evidence(
            title="Unsafe response",
            question="Can this trade?",
            evidence={"return_pct": 1.0},
            config=DeepSeekConfig(api_key="test-key"),
            transport=_transport(_content(live_order_permission=True)),
        )


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls"])
def test_incomplete_provider_response_is_rejected(finish_reason: str) -> None:
    with pytest.raises(DeepSeekAnalysisError, match="incomplete_response"):
        analyze_research_evidence(
            title="Incomplete",
            question="Review",
            evidence={"metric": 1},
            config=DeepSeekConfig(api_key="test-key"),
            transport=_transport(_content(), finish_reason=finish_reason),
        )


def test_secret_bearing_evidence_is_rejected_before_network() -> None:
    with pytest.raises(DeepSeekAnalysisError, match="sensitive_evidence"):
        analyze_research_evidence(
            title="Secrets",
            question="Review",
            evidence={"exchange_api_key": "must-not-leave-host"},
            config=DeepSeekConfig(api_key="test-key"),
            transport=httpx.MockTransport(
                lambda request: pytest.fail("network must not be called")
            ),
        )


def test_missing_proxy_runtime_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingProxyClient:
        def __init__(self, **kwargs: object) -> None:
            raise ImportError("SOCKS proxy support is unavailable")

    monkeypatch.setattr("emergenzix_api.deepseek_analysis.httpx.Client", MissingProxyClient)

    with pytest.raises(DeepSeekAnalysisError, match="upstream_unavailable"):
        analyze_research_evidence(
            title="Proxy failure",
            question="Review",
            evidence={"metric": 1},
            config=DeepSeekConfig(api_key="test-key"),
        )


def test_missing_api_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMERGENZIX_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("EMERGENZIX_DEEPSEEK_API_KEY_FILE", raising=False)

    with pytest.raises(DeepSeekAnalysisError, match="not_configured"):
        analyze_research_evidence(
            title="No key",
            question="Review",
            evidence={"metric": 1},
        )


def test_api_key_can_be_loaded_from_secret_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    secret_file = tmp_path / "deepseek_api_key"
    secret_file.write_text("file-test-key\n", encoding="utf-8")
    monkeypatch.delenv("EMERGENZIX_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("EMERGENZIX_DEEPSEEK_API_KEY_FILE", str(secret_file))

    assert DeepSeekConfig.from_env().api_key == "file-test-key"


def test_api_reports_unconfigured_provider_without_leaking_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EMERGENZIX_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("EMERGENZIX_AI_ANALYSIS_ACCESS_TOKEN", "internal-test-token")

    with pytest.raises(HTTPException) as captured:
        create_ai_analysis(
            AIAnalysisCreate(
                title="No key", question="Review", evidence={"metric": 1}
            ),
            "internal-test-token",
        )

    assert captured.value.status_code == 503
    assert captured.value.detail == "AI analysis is unavailable"


def test_api_route_is_disabled_without_separate_internal_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EMERGENZIX_AI_ANALYSIS_ACCESS_TOKEN", raising=False)

    with pytest.raises(HTTPException) as captured:
        _authorize_internal_request(None)

    assert captured.value.status_code == 503


def test_api_route_rejects_wrong_internal_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMERGENZIX_AI_ANALYSIS_ACCESS_TOKEN", "expected-token")

    with pytest.raises(HTTPException) as captured:
        _authorize_internal_request("wrong-token")

    assert captured.value.status_code == 401
