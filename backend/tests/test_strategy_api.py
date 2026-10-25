"""Strategy and immutable-version API contracts after router extraction."""

from __future__ import annotations

import hashlib
import json

from fastapi.testclient import TestClient

from emergenzix_api.main import create_app


def _create(client, *, name: str = "Regression Strategy", symbol: str = "BTCUSDT") -> dict:
    result = client.post(
        "/api/v1/strategies",
        json={
            "name": name,
            "symbol": symbol,
            "strategy_type": "sma_crossover",
            "fast_window": 3,
            "slow_window": 10,
        },
    )
    assert result.status_code == 201, result.text
    return result.json()


def _digest(parameters: dict) -> str:
    canonical = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_strategy_openapi_methods_and_paths_remain_unchanged(client) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        "/api/v1/strategies": {"get", "post"},
        "/api/v1/strategies/{strategy_id}": {"get"},
        "/api/v1/strategies/{strategy_id}/versions": {"get", "post"},
    }
    for path, methods in expected.items():
        assert set(paths[path]) == methods
        for method in methods:
            assert "tags" not in paths[path][method]
    assert set(paths["/api/v1/backtests"]) == {"get", "post"}
    assert set(paths["/api/v1/paper/orders"]) == {"get", "post"}


def test_strategy_create_list_and_current_version_provenance(client) -> None:
    created = _create(client, name="  Regression Strategy  ")
    assert created["name"] == "Regression Strategy"
    assert created["current_version"] == 1
    assert created["current_version_id"]
    parameters = {
        "strategy_type": "sma_crossover",
        "fast_window": 3,
        "slow_window": 10,
    }
    assert created["current_parameters_sha256"] == _digest(parameters)
    identity = created["id"]

    fetched = client.get(f"/api/v1/strategies/{identity}")
    assert fetched.status_code == 200
    assert fetched.json() == created
    listed = client.get("/api/v1/strategies")
    assert listed.status_code == 200
    assert any(item == created for item in listed.json())

    versions = client.get(f"/api/v1/strategies/{identity}/versions")
    assert versions.status_code == 200
    assert len(versions.json()) == 1
    version = versions.json()[0]
    assert version["id"] == created["current_version_id"]
    assert version["strategy_id"] == identity
    assert version["version"] == 1
    assert version["parameters"] == parameters
    assert version["parameters_sha256"] == _digest(parameters)
    with client.app.state.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM paper_orders").fetchone()[0] == 0


def test_strategy_versions_append_without_rewriting_previous_snapshots(client) -> None:
    strategy = _create(client)
    identity = strategy["id"]
    before = client.get(f"/api/v1/strategies/{identity}/versions").json()
    added = client.post(
        f"/api/v1/strategies/{identity}/versions",
        json={"fast_window": 5, "slow_window": 15},
    )
    assert added.status_code == 201, added.text
    new_version = added.json()
    assert new_version["version"] == 2
    assert new_version["strategy_id"] == identity
    assert new_version["parameters_sha256"] == _digest({
        "strategy_type": "sma_crossover",
        "fast_window": 5,
        "slow_window": 15,
    })

    after = client.get(f"/api/v1/strategies/{identity}/versions").json()
    assert after[0] == before[0]
    assert after[1] == new_version
    current = client.get(f"/api/v1/strategies/{identity}").json()
    assert current["current_version"] == 2
    assert current["current_version_id"] == new_version["id"]
    assert current["current_parameters_sha256"] == new_version["parameters_sha256"]

    duplicate = client.post(
        f"/api/v1/strategies/{identity}/versions",
        json={"fast_window": 5, "slow_window": 15},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Parameters match the current strategy version"
    assert client.get(f"/api/v1/strategies/{identity}/versions").json() == after
    with client.app.state.database.connection() as connection:
        assert connection.execute(
            "SELECT count(*) FROM strategy_versions WHERE strategy_id = ?", (identity,)
        ).fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM paper_orders").fetchone()[0] == 0


def test_missing_strategy_paths_and_invalid_parameters_fail_closed(client) -> None:
    missing = "does-not-exist"
    assert client.get(f"/api/v1/strategies/{missing}").status_code == 404
    assert client.get(f"/api/v1/strategies/{missing}/versions").status_code == 404
    assert client.post(
        f"/api/v1/strategies/{missing}/versions",
        json={"fast_window": 3, "slow_window": 10},
    ).status_code == 404
    invalid = client.post(
        "/api/v1/strategies",
        json={
            "name": "Invalid",
            "symbol": "BTCUSDT",
            "fast_window": 12,
            "slow_window": 8,
        },
    )
    assert invalid.status_code == 422
    with client.app.state.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM strategies").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM strategy_versions").fetchone()[0] == 0


def test_versions_are_scoped_to_their_strategy_and_do_not_enable_orders(client) -> None:
    a = _create(client, name="A")
    b = _create(client, name="B")
    changed = client.post(
        f"/api/v1/strategies/{a['id']}/versions",
        json={"fast_window": 7, "slow_window": 20},
    )
    assert changed.status_code == 201
    versions_a = client.get(f"/api/v1/strategies/{a['id']}/versions").json()
    versions_b = client.get(f"/api/v1/strategies/{b['id']}/versions").json()
    assert [v["version"] for v in versions_a] == [1, 2]
    assert [v["version"] for v in versions_b] == [1]
    assert all(v["strategy_id"] == a["id"] for v in versions_a)
    assert all(v["strategy_id"] == b["id"] for v in versions_b)
    assert client.get(f"/api/v1/strategies/{b['id']}").json()["current_version"] == 1
    with client.app.state.database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM paper_orders").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM backtests").fetchone()[0] == 0


def test_two_application_instances_use_isolated_sqlite_databases(tmp_path) -> None:
    app_a = create_app(db_path=tmp_path / "a.db", initial_paper_cash=1_000.0)
    app_b = create_app(db_path=tmp_path / "b.db", initial_paper_cash=1_000.0)
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _create(client_a)
        assert len(client_a.get("/api/v1/strategies").json()) == 1
        assert client_b.get("/api/v1/strategies").json() == []
        assert client_b.get("/health").json()["live_trading_enabled"] is False
