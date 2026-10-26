"""Reviewed NEMT research strategies available to Emergenzix backtests.

This package has no exchange execution adapter.  Every exported strategy is
research-only and LIVE remains disabled independently of backtest results.
"""

from .registry import DEFAULT_STRATEGY_ID, get_strategy, list_strategies

__all__ = ["DEFAULT_STRATEGY_ID", "get_strategy", "list_strategies"]
