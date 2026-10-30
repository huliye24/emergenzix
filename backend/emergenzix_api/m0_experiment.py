"""Identity-only configuration for the M0 shadow comparison.

Strategy runners are intentionally not imported here.  Each runner will be
connected through an adapter after it can consume the same in-memory dataset
and shared research assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass

from .walk_forward import WalkForwardProtocol


@dataclass(frozen=True)
class ResearchArmDefinition:
    arm_id: str
    label: str
    role: str
    research_status: str
    live_enabled: bool = False


M0_EXPERIMENT_ID = "m0-nemt-three-arm-shadow-001"
M0_PROTOCOL = WalkForwardProtocol()
M0_ARMS = (
    ResearchArmDefinition(
        arm_id="donchian_only",
        label="Donchian-only",
        role="control",
        research_status="system control; adapter pending",
    ),
    ResearchArmDefinition(
        arm_id="nemt_v4_4",
        label="NEMT v4.4",
        role="research_baseline",
        research_status="reviewed research baseline; not live; adapter pending",
    ),
    ResearchArmDefinition(
        arm_id="nemt_v5_2_7",
        label="NEMT v5.2.7",
        role="experimental_challenger",
        research_status="experimental; not promoted; adapter pending",
    ),
)


def m0_arm_ids() -> tuple[str, ...]:
    return tuple(arm.arm_id for arm in M0_ARMS)
