"""Normalize Upbit order responses without inventing missing fill evidence."""
from decimal import localcontext
from typing import Any

from ...core.order_models import OrderSnapshot, OrderState, decimal_field, identifier


def normalize_order(response: dict[str, Any]) -> OrderSnapshot:
    if not isinstance(response, dict):
        raise TypeError("INVALID_UPBIT_ORDER_RESPONSE")
    issues: list[str] = []
    raw_state = str(response.get("state") or "")
    state = {"wait": OrderState.OPEN, "watch": OrderState.OPEN, "done": OrderState.FILLED,
             "cancel": OrderState.CANCELED, "prevented": OrderState.CANCELED}.get(raw_state.lower(), OrderState.UNKNOWN)
    filled = decimal_field(response.get("executed_volume"), "executed_volume", issues)
    remaining = decimal_field(response.get("remaining_volume"), "remaining_volume", issues)
    funds = decimal_field(response.get("executed_funds"), "executed_funds", issues)
    if state == OrderState.OPEN and filled is not None and filled > 0:
        state = OrderState.PARTIALLY_FILLED
    if funds is None:
        trades = response.get("trades")
        if isinstance(trades, list) and trades:
            values = [decimal_field(trade.get("funds"), "trade_funds", issues)
                      if isinstance(trade, dict) else None for trade in trades]
            quantities = [decimal_field(trade.get("volume"), "trade_volume", issues)
                          if isinstance(trade, dict) else None for trade in trades]
            # A partial trade list cannot establish the average for the entire order.
            if all(value is not None for value in values + quantities) and filled is not None:
                with localcontext() as ctx:
                    ctx.prec = 80
                    if sum(quantities) == filled:
                        funds = sum(values)
    average = None
    if filled is not None and filled > 0 and funds is not None and funds > 0:
        with localcontext() as ctx:
            ctx.prec = 80
            average = funds / filled
    if state == OrderState.FILLED and (filled is None or filled <= 0 or (remaining is not None and remaining > 0)):
        issues.append("INCOMPLETE_FILL_EVIDENCE")
    if state == OrderState.UNKNOWN:
        issues.append("UNKNOWN_ORDER_STATE")
    return OrderSnapshot("upbit", identifier(response.get("uuid") or response.get("order_id")),
                         identifier(response.get("identifier")), state, raw_state, filled, remaining, average, tuple(issues),
                         market=str(response.get("market") or "").strip().upper() or None,
                         side={"bid": "BUY", "ask": "SELL"}.get(str(response.get("side") or "").lower(),
                              str(response.get("side") or "").upper() or None))
