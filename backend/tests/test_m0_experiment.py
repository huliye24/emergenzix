from emergenzix_api.m0_experiment import M0_ARMS, M0_PROTOCOL, m0_arm_ids


def test_m0_defines_the_requested_comparison_outside_the_engine() -> None:
    assert m0_arm_ids() == ("donchian_only", "nemt_v4_4", "nemt_v5_2_7")
    assert [arm.role for arm in M0_ARMS] == [
        "control", "research_baseline", "experimental_challenger",
    ]


def test_m0_is_quarterly_18_3_3_and_cannot_enable_live() -> None:
    assert (
        M0_PROTOCOL.training_months,
        M0_PROTOCOL.selection_months,
        M0_PROTOCOL.test_months,
        M0_PROTOCOL.step_months,
    ) == (18, 3, 3, 3)
    assert all(arm.live_enabled is False for arm in M0_ARMS)
