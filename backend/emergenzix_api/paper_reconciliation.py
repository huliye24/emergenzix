"""Compare local PAPER projections with an execution-adapter snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ReconciliationState:
    cash: Decimal
    positions: dict[str, Decimal]
    open_orders: dict[str, str]


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    matched: bool
    freeze_account: bool
    discrepancies: tuple[str, ...]


def reconcile_paper_state(
    local: ReconciliationState,
    executor: ReconciliationState,
) -> ReconciliationResult:
    differences: list[str] = []
    if not local.cash.is_finite() or not executor.cash.is_finite():
        differences.append("cash_non_finite")
    elif local.cash != executor.cash:
        differences.append("cash_mismatch")

    symbols = sorted(set(local.positions) | set(executor.positions))
    for symbol in symbols:
        local_quantity = local.positions.get(symbol, Decimal("0"))
        executor_quantity = executor.positions.get(symbol, Decimal("0"))
        if not local_quantity.is_finite() or not executor_quantity.is_finite():
            differences.append(f"position_non_finite:{symbol}")
        elif local_quantity != executor_quantity:
            differences.append(f"position_mismatch:{symbol}")

    order_ids = sorted(set(local.open_orders) | set(executor.open_orders))
    for order_id in order_ids:
        local_state = local.open_orders.get(order_id)
        executor_state = executor.open_orders.get(order_id)
        if local_state != executor_state:
            differences.append(f"order_mismatch:{order_id}")

    return ReconciliationResult(
        matched=not differences,
        freeze_account=bool(differences),
        discrepancies=tuple(differences),
    )
