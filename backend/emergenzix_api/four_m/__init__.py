"""4M v0.1: pure, research-only market state assessments.

No exchange adapters, network IO, account mutations or order functions.
"""
from .engine import assess, Assessment, Snapshot
__all__ = ["assess", "Assessment", "Snapshot"]
