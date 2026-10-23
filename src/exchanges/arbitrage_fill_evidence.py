"""Bounded native fill evidence, separate from the latency-sensitive hedge path.

Sources: Coinone /reference/find-completed-orders; DigitalX #REST-get-_v2_myTrades.
Only native trade IDs/times are evidence. No aggregate-to-trade fabrication.
"""
from copy import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import re
import time

from urllib3.util import Timeout

from .arbitrage_timeout import bounded_timeout
from .spot_trading import decimal, exact


def _id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).strip():
        raise ValueError("INVALID_FILL_ID")
    return str(value)


def _amount(value, *, positive=False):
    number = decimal(value)
    if number < 0 or positive and number == 0:
        raise ValueError("INVALID_FILL_AMOUNT")
    return number


def _timestamp(value, lower, upper):
    number = decimal(value)
    if number != number.to_integral_value() or not lower <= number <= upper or number <= 0:
        raise ValueError("INVALID_FILL_TIME")
    return int(number)


def _trade(exchange, row, market, pair, lower, upper):
    if not isinstance(row, dict):
        raise ValueError("INVALID_FILL_ROWS")
    if exchange == "coinone":
        if f'{row["quote_currency"]}-{row["target_currency"]}'.upper() != market:
            raise ValueError("FILL_MARKET_MISMATCH")
        if not isinstance(row["is_ask"], bool):
            raise ValueError("INVALID_FILL_SIDE")
        side = "ask" if row["is_ask"] else "bid"
        tid, oid, timestamp = row["trade_id"], row["order_id"], row["timestamp"]
        fee, currency = row.get("fee"), row.get("fee_currency")
    else:
        if row["symbol"] != pair:
            raise ValueError("FILL_MARKET_MISMATCH")
        if row["side"] not in ("buy", "sell"):
            raise ValueError("INVALID_FILL_SIDE")
        side = "bid" if row["side"] == "buy" else "ask"
        tid, oid, timestamp = row["tradeId"], row["orderId"], row["tradedAt"]
        fee, currency = row.get("feeQty"), row.get("feeCurrency")
    price, qty = _amount(row["price"], positive=True), _amount(row["qty"], positive=True)
    funds = price * qty if exchange == "coinone" else _amount(row["amt"], positive=True)
    ms = _timestamp(timestamp, lower, upper)
    created = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms)
    result = dict(uuid=_id(tid), order_id=_id(oid), market=market, side=side,
                  price=exact(price), volume=exact(qty), funds=exact(funds), timestamp=ms,
                  created_at=created.isoformat(timespec="milliseconds").replace("+00:00", "Z"))
    if fee is not None:
        result["fee"] = exact(_amount(fee))
    if currency is not None:
        currency = str(currency).upper()
        if not re.fullmatch(r"[A-Z0-9]{1,20}", currency):
            raise ValueError("INVALID_FILL_FEE_CURRENCY")
        result["fee_currency"] = currency
    return result


def _budget(client):
    configured = bounded_timeout(client.timeout_seconds)
    if isinstance(configured, Timeout):
        configured.start_connect()
        connect, read = configured.connect_timeout, configured.read_timeout
        total = configured.total if configured.total is not None else connect + read
    else:
        connect, read = configured if isinstance(configured, tuple) else (configured, configured)
        total = connect + read
    return min(5.0, total), connect, read


def collect_order_fill_evidence(exchange, client, pair, market, order_id, order, *, max_pages=4):
    """Return a copied terminal order; incomplete evidence never exposes trades.

    max_pages=1..8 (default4); at most5s shared I/O budget, shortened by a
    configured total timeout. Transport errors propagate; no retries/sleeps.
    The caller still owns an absolute job deadline (requests socket semantics).
    """
    with localcontext() as ctx:
        ctx.prec = 80
        return _collect(exchange, client, pair, market, order_id, order, max_pages)


def _collect(exchange, client, pair, market, order_id, order, max_pages):
    oid = _id(order_id)
    if not isinstance(order, dict) or _id(order.get("uuid")) != oid or order.get("market") != market:
        raise ValueError("FILL_ORDER_IDENTITY_MISMATCH")
    if order.get("terminal") is not True or order.get("state") not in ("done", "cancel"):
        raise ValueError("FILL_EVIDENCE_REQUIRES_TERMINAL_ORDER")
    if order.get("side") not in ("bid", "ask"):
        raise ValueError("INVALID_FILL_SIDE")
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 8:
        raise ValueError("FILL_PAGES_REQUIRE_1_TO_8")
    expected = _amount(order["executed_volume"])
    expected_funds = _amount(order["executed_funds"])
    if (expected == 0) != (expected_funds == 0):
        raise ValueError("INCONSISTENT_ORDER_FUNDS")
    if not isinstance(order.get("executed_funds_estimated"), bool):
        raise ValueError("FILL_FUNDS_PROVENANCE_REQUIRED")
    result = {k: v for k, v in order.items() if k not in ("trades", "fill_evidence_error", "fees_complete")}
    result.update(trades_complete=False, fill_evidence_pages=0)
    if expected == 0:
        return {**result, "trades": [], "trades_complete": True}
    budget, connect, read = _budget(client)
    deadline = time.monotonic() + budget
    # A shallow request client shares only the existing transport, not timeout
    # mutation. This must not change the caller's client settings or credentials.
    reader = copy(client)
    now = int(time.time() * 1000)
    lower = now - (90 * 24 * 3600 * 1000 if exchange == "coinone" else 36 * 3600 * 1000)
    end, cursor, seen = now, None, {}
    limit = 100 if exchange == "coinone" else 1000
    reason = "FILL_PAGE_LIMIT"
    for page in range(max_pages):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("FILL_EVIDENCE_DEADLINE")
        reader.timeout_seconds = Timeout(total=remaining, connect=min(connect, remaining), read=min(read, remaining))
        if exchange == "coinone":
            rows = reader.list_completed_orders(ticker=pair, size=limit, from_ts=lower,
                to_ts=end, **({"to_trade_id": cursor} if cursor is not None else {}))["completed_orders"]
        else:
            # Omit start_time: the server applies its moving 36h retention floor.
            rows = reader.get_my_trades(pair, limit=limit, end_time=end)
        if time.monotonic() >= deadline:
            raise TimeoutError("FILL_EVIDENCE_DEADLINE")
        result["fill_evidence_pages"] = page + 1
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError("INVALID_FILL_ROWS")
        trades = [_trade(exchange, row, market, pair, lower, end if exchange == "coinone" else end - 1)
                  for row in rows]
        old_count = len(seen)
        for trade in trades:
            key = trade["uuid"]
            if key in seen and seen[key] != trade:
                raise ValueError("CONFLICTING_FILL_ID")
            seen[key] = trade
        matching = [t for t in seen.values() if t["order_id"] == oid]
        if any(t["side"] != order["side"] for t in matching):
            raise ValueError("FILL_SIDE_MISMATCH")
        qty = sum((decimal(t["volume"]) for t in matching), Decimal(0))
        funds = sum((decimal(t["funds"]) for t in matching), Decimal(0))
        if qty > expected:
            raise ValueError("FILL_QUANTITY_MISMATCH")
        if qty == expected:
            if not order["executed_funds_estimated"] and funds != expected_funds:
                raise ValueError("FILL_FUNDS_MISMATCH")
            matching.sort(key=lambda t: (t["timestamp"], t["uuid"]))
            result.update(trades=matching, trades_complete=True, executed_funds=exact(funds),
                          executed_funds_estimated=False, average_executed_price=exact(funds / expected))
            fees_complete = all("fee" in t and t.get("fee_currency") == "KRW" for t in matching)
            result["fees_complete"] = fees_complete
            if fees_complete:
                fee = sum((decimal(t["fee"]) for t in matching), Decimal(0))
                if order.get("paid_fee") is not None and _amount(order["paid_fee"]) != fee:
                    raise ValueError("FILL_FEE_MISMATCH")
                result.update(paid_fee=exact(fee), fee_currency="KRW")
            return result
        if len(trades) < limit:
            reason = "FILL_HISTORY_INCOMPLETE"
            break
        if exchange == "coinone":
            next_cursor = trades[-1]["uuid"]
            if next_cursor == cursor or len(seen) == old_count:
                reason = "FILL_CURSOR_NOT_PROGRESSING"
                break
            cursor = next_cursor
        else:
            # Keep the boundary millisecond in the next page. Exclusive end=min
            # would silently skip other trades sharing that millisecond.
            next_end = min(t["timestamp"] for t in trades) + 1
            if next_end >= end or len(seen) == old_count:
                reason = "FILL_TIMESTAMP_PAGE_SATURATED"
                break
            end = next_end
    result["fill_evidence_error"] = reason
    return result
