import pytest

from emergenzix_api.database import Database
from emergenzix_api.paper_kill_switch import (
    get_paper_kill_switch,
    set_paper_kill_switch,
)


def test_kill_switch_persists_across_restart_and_keeps_emergency_actions(tmp_path):
    path = tmp_path / "paper.db"
    database = Database(str(path))
    initial = get_paper_kill_switch(database)
    paused = set_paper_kill_switch(
        database,
        paused=True,
        reason="operator emergency stop",
        expected_revision=initial.revision,
    )
    restored = get_paper_kill_switch(Database(str(path)))
    assert paused == restored
    assert restored.new_orders_paused is True
    assert restored.allow_cancel_orders is True
    assert restored.allow_reduce_risk is True


def test_compare_and_swap_prevents_lost_operator_updates(tmp_path):
    database = Database(str(tmp_path / "paper.db"))
    initial = get_paper_kill_switch(database)
    set_paper_kill_switch(
        database, paused=True, reason="risk mismatch", expected_revision=initial.revision
    )
    with pytest.raises(ValueError, match="revision conflict"):
        set_paper_kill_switch(
            database, paused=False, reason="", expected_revision=initial.revision
        )


def test_pause_requires_reason_and_invalid_type_fails_closed(tmp_path):
    database = Database(str(tmp_path / "paper.db"))
    initial = get_paper_kill_switch(database)
    with pytest.raises(ValueError, match="reason"):
        set_paper_kill_switch(
            database, paused=True, reason=" ", expected_revision=initial.revision
        )
    with pytest.raises(ValueError, match="boolean"):
        set_paper_kill_switch(
            database, paused=1, reason="bad", expected_revision=initial.revision  # type: ignore[arg-type]
        )
