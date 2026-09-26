"""Read-only, OrderID-based reconciliation. One HTTP operation per advance.

The caller owns the deadline/request budget and the decision to reset a line.
No mutation, client-ID lookup, or inferred price/quantity match occurs here.
"""
from decimal import Decimal

from .spot_trading import Order, decimal, exact, execution_time


def _match(rows, field, order_id, limit, *, filtered=False):
    if (not isinstance(rows, list) or len(rows) > limit
            or any(not isinstance(r, dict) or r.get(field) in (None, "") for r in rows)):
        raise ValueError("INVALID_RECOVERY_LIST")
    matches = [r for r in rows if str(r[field]) == str(order_id)]
    if filtered and len(matches) != len(rows):
        raise ValueError("ORDER_IDENTITY_MISMATCH")
    if len(matches) > 1:
        raise ValueError("AMBIGUOUS_RECOVERY_ORDER")
    return matches[0] if matches else None


def _window(probe, maximum, *, creation_only=False):
    stamp, now = probe.get("submittedAt"), probe["wallTime"]
    if stamp is None or not 0 < stamp <= now:
        raise ValueError("ORDER_CREATION_TIME_UNKNOWN")
    start = max(0, int(stamp * 1000) - 300_000)
    end = min(int(now * 1000), int(stamp * 1000) + 300_000) if creation_only else int(now * 1000)
    if end - start > maximum:
        raise ValueError("ORDER_HISTORY_COVERAGE_INCOMPLETE")
    return start, end


def _digital_history(adapter, market, order_id, side, quantity, probe):
    stage = probe["stage"]
    if "windows" not in probe:
        start, end = _window(probe, 36 * 3600 * 1000)
        probe["windows"] = [(start, end + 1)]  # API end is exclusive.
    start, end = probe["windows"][0]
    method = adapter.client.get_my_trades if stage == "trades" else adapter.client.list_all_orders
    rows = method(adapter.pair(market), limit=1000, start_time=start, end_time=end)
    row = _match(rows, "orderId", order_id, 1000) if stage != "trades" else None
    if stage == "trades":
        if (not isinstance(rows, list) or len(rows) > 1000 or
                any(not isinstance(r, dict) or not r.get("tradeId") or not r.get("orderId") for r in rows)):
            raise ValueError("INVALID_RECOVERY_TRADES")
        trades = dict(probe.get("trades", {}))
        for trade in rows:
            if str(trade["orderId"]) != str(order_id):
                continue
            if trade["symbol"] != adapter.pair(market) or trade["side"] != side:
                raise ValueError("ORDER_IDENTITY_MISMATCH")
            value = (exact(trade["qty"]), exact(trade["price"]), int(trade["tradedAt"]))
            if decimal(value[0]) <= 0 or decimal(value[1]) <= 0 or not start <= value[2] < end:
                raise ValueError("INVALID_RECOVERY_TRADE")
            key = str(trade["tradeId"])
            if key in trades and trades[key] != value:
                raise ValueError("CONFLICTING_COMPLETED_TRADE")
            trades[key] = value
        filled = sum((decimal(t[0]) for t in trades.values()), Decimal(0))
        if filled > decimal(quantity):
            raise ValueError("COMPLETED_ORDER_QUANTITY_MISMATCH")
        probe.update(trades=trades, filled=exact(filled))
        if filled == decimal(quantity):
            return Order(str(order_id), market, side, exact(quantity), exact(filled), "FILLED",
                         filled_at=execution_time(max(t[2] for t in trades.values()))), True
    elif row:
        return adapter.normalize(row, market), True
    remaining = list(probe["windows"][1:])
    if len(rows) == 1000:
        if end - start <= 1:
            raise ValueError("ORDER_HISTORY_TRUNCATED")
        middle = (start + end) // 2
        remaining = [(middle, end), (start, middle), *remaining]
    probe["windows"] = remaining
    if remaining:
        return None, False
    probe.pop("windows")
    if stage == "history":
        probe["stage"] = "trades"
    else:
        if decimal(probe.get("filled", "0")):
            raise ValueError("PARTIAL_ORDER_UNRESOLVED")
        probe["stage"] = "final"
    return None, False


def recover_order(adapter, market, order_id, client_id, side, quantity, probe):
    """Return (snapshot, complete). (None, True) is verified absence.

    Any error, incomplete history or unsupported window raises instead of
    returning absence. A final ID lookup closes the list/detail race.
    """
    if not order_id:
        raise ValueError("ORDER_ID_REQUIRED")
    exchange, stage = adapter.exchange, probe.setdefault("stage", "active")
    if exchange not in {"upbit", "bithumb", "coinone", "korbit"}:
        raise ValueError("ORDER_RECOVERY_UNSUPPORTED")
    if stage == "final":
        try:
            return adapter.lookup(market, order_id=order_id), True
        except Exception as error:
            if adapter.is_order_missing(error):
                return None, True
            raise
    if exchange == "upbit":
        if stage == "active":
            rows = adapter.client.list_orders_by_ids(uuids=[order_id])
            row = _match(rows, "uuid", order_id, 1, filtered=True)
            if row:
                return adapter.normalize(row, market), True
            probe["stage"] = "history"
        else:
            if "windows" not in probe:
                probe["windows"] = [_window(probe, 7 * 86400 * 1000, creation_only=True)]
            result, remaining = adapter.resolve_closed_order(market, order_id, client_id, probe["windows"])
            if result:
                return result, True
            probe["windows"] = remaining
            if not remaining:
                probe["stage"] = "final"
    elif exchange == "bithumb":
        state = {"active": "wait", "history": "done", "canceled": "cancel"}[stage]
        rows = adapter.client.list_orders(ticker=adapter.pair(market), uuids=[order_id], state=state, limit=100)
        row = _match(rows, "uuid", order_id, 1, filtered=True)
        if row:
            return adapter.normalize(row, market), True
        probe["stage"] = {"active": "history", "history": "canceled", "canceled": "final"}[stage]
    elif exchange == "coinone":
        if stage == "active":
            result, covered = adapter.resolve_active_order(market, order_id, client_id)
            if result:
                return result, True
            if not covered:
                raise ValueError("ORDER_ACTIVE_CHECK_INCOMPLETE")
            probe["stage"] = "history"
        else:
            if "from" not in probe:
                probe["from"], probe["to"] = _window(probe, 90 * 86400 * 1000)
            result, more = adapter.resolve_completed_order(market, order_id, client_id, side, quantity, probe)
            if result:
                return result, True
            if not more:
                if not probe.get("complete") or decimal(probe.get("filled", "0")):
                    raise ValueError("PARTIAL_ORDER_UNRESOLVED")
                probe["stage"] = "final"
    elif stage == "active":
        rows = adapter.client.get_open_orders(adapter.pair(market), limit=1000)
        row = _match(rows, "orderId", order_id, 1000)
        if row:
            # openOrders is pair-scoped and omits symbol in its documented rows.
            return adapter.normalize({"symbol": adapter.pair(market), **row}, market), True
        if len(rows) == 1000:
            raise ValueError("ORDER_ACTIVE_CHECK_INCOMPLETE")
        probe["stage"] = "history"
    else:
        return _digital_history(adapter, market, order_id, side, quantity, probe)
    return None, False
