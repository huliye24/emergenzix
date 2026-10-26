from datetime import date

import pytest

from emergenzix_api.nemt_strategy.v44_adapter import route_for_month


@pytest.mark.parametrize(
    ("month", "variant"),
    [
        (date(2026, 1, 1), "v4b"),
        (date(2026, 2, 1), "v4a"),
        (date(2026, 3, 1), "v4b"),
        (date(2026, 4, 1), "v4b"),
        (date(2026, 5, 1), "v4a"),
    ],
)
def test_replays_reviewed_causal_routes(month: date, variant: str) -> None:
    decision = route_for_month(month)
    assert decision["selected_variant"] == variant
    assert decision["history_months"] >= 24


def test_unreviewed_month_fails_closed() -> None:
    with pytest.raises(ValueError, match="no reviewed v4.4 router decision"):
        route_for_month(date(2027, 1, 1))
