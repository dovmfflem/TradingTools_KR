"""Normalize USD-M order responses, keeping ACKs distinct from confirmed fills."""
from decimal import localcontext
from typing import Any

from ...core.order_models import OrderSnapshot, OrderState, decimal_field, identifier


def normalize_order(response: dict[str, Any]) -> OrderSnapshot:
    if not isinstance(response, dict):
        raise TypeError("INVALID_BINANCE_ORDER_RESPONSE")
    issues: list[str] = []
    raw_state = str(response.get("status") or "")
    state = {"NEW": OrderState.OPEN, "PARTIALLY_FILLED": OrderState.PARTIALLY_FILLED,
             "FILLED": OrderState.FILLED, "CANCELED": OrderState.CANCELED,
             "EXPIRED": OrderState.EXPIRED, "EXPIRED_IN_MATCH": OrderState.EXPIRED,
             "REJECTED": OrderState.REJECTED}.get(raw_state.upper(), OrderState.UNKNOWN)
    filled = decimal_field(response.get("executedQty", response.get("cumQty")), "executed_qty", issues)
    requested = decimal_field(response.get("origQty"), "orig_qty", issues)
    average = decimal_field(response.get("avgPrice"), "avg_price", issues)
    quote = decimal_field(response.get("cumQuote"), "cum_quote", issues)
    remaining = None
    with localcontext() as ctx:
        ctx.prec = 80
        if requested is not None and filled is not None:
            if filled <= requested:
                remaining = requested - filled
            else:
                issues.append("FILLED_EXCEEDS_REQUESTED")
        if not average or filled is None or filled <= 0:
            average = quote / filled if quote is not None and quote > 0 and filled is not None and filled > 0 else None
    if state == OrderState.FILLED and (filled is None or filled <= 0 or (remaining is not None and remaining > 0)):
        issues.append("INCOMPLETE_FILL_EVIDENCE")
    if state == OrderState.UNKNOWN:
        issues.append("UNKNOWN_ORDER_STATE")
    return OrderSnapshot("binance", identifier(response.get("orderId") or response.get("order_id")),
                         identifier(response.get("clientOrderId") or response.get("origClientOrderId")),
                         state, raw_state, filled, remaining, average, tuple(issues),
                         market=str(response.get("symbol") or "").strip().upper() or None,
                         side=str(response.get("side") or "").strip().upper() or None)
