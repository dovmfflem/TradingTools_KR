"""Exact, venue-independent spot trading contract. No implicit mutation retries.

Exchange wire fields belong here; strategy/account allocation belongs to callers.
Register another adapter to extend the engine without changing its order logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from .api_error import ExchangeRequestError


def decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("INVALID_DECIMAL") from None
    if not result.is_finite() or abs(result) > Decimal("1e30"):
        raise ValueError("INVALID_DECIMAL")
    return result


def exact(value):
    return format(decimal(value), "f")


def execution_time(value):
    """Optional execution timestamp in epoch seconds, never order creation time."""
    try:
        if isinstance(value, str) and "T" in value:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                return None
            seconds = stamp.timestamp()
        else:
            number = decimal(value)
            seconds = float(number / 1000 if number >= 1000000000000 else number)
        return seconds if 0 < seconds < 253402214400 else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def balance_pair(available, locked):
    available, locked = decimal(available), decimal(locked)
    if available < 0 or locked < 0:
        raise ValueError("INVALID_ACCOUNT_BALANCE")
    return {"available": exact(available), "locked": exact(locked)}


@dataclass(frozen=True)
class Order:
    id: str
    market: str
    side: str
    quantity: str
    filled: str
    status: str
    client_id: str = ""
    fee: str | None = None
    turnover: str | None = None
    filled_at: float | None = None

    def __post_init__(self):
        if not self.id or self.side not in {"buy", "sell"} or self.status not in {"OPEN", "FILLED", "CANCELED"}:
            raise ValueError("INVALID_ORDER_RESPONSE")
        if not 0 <= decimal(self.filled) <= decimal(self.quantity) or decimal(self.quantity) <= 0:
            raise ValueError("INVALID_ORDER_QUANTITY")
        if self.status == "FILLED" and decimal(self.filled) != decimal(self.quantity):
            raise ValueError("INCOMPLETE_TERMINAL_ORDER")


# Full KRW bands, checked against the official policies 2026-09-24.
UPBIT_BANDS = [("0", "0.00000001"), ("0.00001", "0.0000001"), ("0.0001", "0.000001"),
    ("0.001", "0.00001"), ("0.01", "0.0001"), ("0.1", "0.001"), ("1", "0.01"),
    ("10", "0.1"), ("100", "1"), ("5000", "5"), ("10000", "10"),
    ("50000", "50"), ("100000", "100"), ("500000", "500"), ("1000000", "1000")]
BITHUMB_BANDS = [("0", "0.0001"), ("1", "0.001"), ("10", "0.01"), ("100", "1"),
    ("5000", "5"), ("10000", "10"), ("50000", "50"), ("100000", "100"),
    ("500000", "500"), ("1000000", "1000")]


@dataclass(frozen=True)
class Policy:
    bands: tuple
    min_buy: str
    min_sell: str
    fee: str
    quantity_step: str = "0.00000001"
    max_total: str | None = None
    min_quantity: str = "0"
    max_quantity: str | None = None
    max_price: str | None = None

    def validate(self, side, price, quantity):
        price, quantity = decimal(price), decimal(quantity)
        if side not in {"buy", "sell"} or price <= 0 or quantity <= 0:
            raise ValueError("INVALID_LIMIT_ORDER")
        bands = sorted((decimal(lower), decimal(tick)) for lower, tick in self.bands)
        if not bands or bands[0][0] != 0 or any(tick <= 0 for _, tick in bands):
            raise ValueError("INVALID_PRICE_POLICY")
        tick = next(tick for lower, tick in reversed(bands) if price >= lower)
        if price % tick or quantity % decimal(self.quantity_step):
            raise ValueError("PRICE_OR_QUANTITY_STEP_INVALID")
        if price * quantity < decimal(self.min_buy if side == "buy" else self.min_sell):
            raise ValueError("MINIMUM_ORDER_NOT_MET")
        if quantity < decimal(self.min_quantity) or (self.max_quantity and quantity > decimal(self.max_quantity)):
            raise ValueError("QUANTITY_LIMIT_EXCEEDED")
        if (self.max_total and price * quantity > decimal(self.max_total)) or (self.max_price and price >= decimal(self.max_price)):
            raise ValueError("ORDER_LIMIT_EXCEEDED")
        if not 0 <= decimal(self.fee) < 1:
            raise ValueError("INVALID_FEE_POLICY")


class SpotAdapter:
    exchange = ""
    quotes = ("KRW",)
    submission_id_fields = ("uuid",)

    def is_order_missing(self, error):
        return False

    def __init__(self, client):
        self.client = client

    def pair(self, market):
        quote, base = market.split("-", 1)
        if quote not in self.quotes or not re.fullmatch(r"[A-Z0-9]{1,20}", base):
            raise ValueError("UNSUPPORTED_SPOT_MARKET")
        return market

    def balance_details(self):
        return {str(row["currency"]).upper(): balance_pair(row["balance"], row["locked"])
                for row in self.client.get_accounts()}

    def balances(self):
        return {str(row["currency"]).upper(): exact(row["balance"]) for row in self.client.get_accounts()}

    def policy(self, market):
        data = self.client.get_order_chance(self.pair(market))
        rules = data["market"]
        if rules.get("state", "active") != "active":
            raise ValueError("MARKET_NOT_ACTIVE")
        fee = max(decimal(data[key]) for key in ("bid_fee", "ask_fee"))
        return Policy(tuple(UPBIT_BANDS if self.exchange == "upbit" else BITHUMB_BANDS),
                      exact(rules["bid"]["min_total"]), exact(rules["ask"]["min_total"]), exact(fee),
                      max_total=exact(rules["max_total"]) if rules.get("max_total") else None)

    def normalize(self, data, market):
        self.pair(market)
        if data.get("market") != market:
            raise ValueError("ORDER_MARKET_MISMATCH")
        states = {"wait": "OPEN", "watch": "OPEN", "done": "FILLED", "cancel": "CANCELED"}
        trades = data.get("trades")
        turnover = (sum((decimal(t["funds"]) for t in trades), Decimal(0))
                    if isinstance(trades, list) and all("funds" in t for t in trades) else None)
        return Order(str(data["uuid"]), market, {"bid": "buy", "ask": "sell"}[data["side"]],
                     exact(data["volume"]), exact(data["executed_volume"]), states[data["state"]],
                     str(data.get("identifier") or data.get("client_order_id") or ""),
                     exact(data["paid_fee"]) if data.get("paid_fee") is not None else None,
                     exact(turnover) if turnover is not None else None,
                     max((stamp for t in (trades or []) if isinstance(t, dict)
                          if (stamp := execution_time(t.get("created_at"))) is not None), default=None))

    def submit(self, market, side, price, quantity, client_id):
        if side not in {"buy", "sell"} or not re.fullmatch(r"[a-z0-9_-]{1,36}", client_id):
            raise ValueError("INVALID_ORDER_INTENT")
        key = "identifier" if self.exchange == "upbit" else "client_order_id"
        data = self.client.place_order(ticker=self.pair(market), side="bid" if side == "buy" else "ask",
            order_type="limit", price=exact(price), volume=exact(quantity), **{key: client_id})
        order_id = next((data.get(key) for key in self.submission_id_fields if data.get(key)), None)
        if not order_id:
            raise ValueError("ORDER_RESULT_UNKNOWN")
        return str(order_id)

    def lookup(self, market, order_id=None, client_id=None):
        key = "identifier" if self.exchange == "upbit" else "client_order_id"
        return self.normalize(self.client.get_order(order_id, **({key: client_id} if not order_id else {})), market)

    def cancel(self, market, order_id):
        self.pair(market)
        self.client.cancel_order(order_id)

    def event(self, message, quantity, side):
        fields = message.get("exact") or {}
        if message.get("event") != "order" or fields.get("cumulativeFilled") is None:
            return None
        if fields.get("originalVolume") is not None and decimal(fields["originalVolume"]) != decimal(quantity):
            raise ValueError("ORDER_QUANTITY_MISMATCH")
        if message.get("order", {}).get("side", side) != side:
            raise ValueError("ORDER_SIDE_MISMATCH")
        state = str(message.get("state", "")).lower()
        status = ({"done": "FILLED", "trade_done": "FILLED", "filled": "FILLED",
                   "cancel": "CANCELED", "cancel_post_only": "CANCELED", "canceled": "CANCELED",
                   "partiallyfilledcanceled": "CANCELED", "expired": "CANCELED"}).get(state, "OPEN")
        if message.get("terminal") and status == "OPEN":
            return None
        return Order(str(fields["exchangeOrderId"]), message["market"], side, exact(quantity),
                     exact(fields["cumulativeFilled"]), status, str(fields.get("clientOrderId") or ""),
                     exact(fields["fee"]) if fields.get("fee") is not None else None,
                     filled_at=execution_time(fields.get("fillTimestamp")))


class UpbitSpot(SpotAdapter):
    exchange = "upbit"

    def is_order_missing(self, error):
        return (isinstance(error, ExchangeRequestError) and error.exchange == self.exchange
                and error.status_code == 404 and error.code == "order_not_found")

    def resolve_missing_order(self, market, client_id):
        """One independent identifier-list read; None means a valid empty list.

        The caller must establish repeated negative single-order reads and must
        not infer non-acceptance for an order with an acknowledgement or fills.
        """
        self.pair(market)
        if not re.fullmatch(r"[a-z0-9_-]{1,36}", client_id):
            raise ValueError("INVALID_ORDER_INTENT")
        # No market filter: a mismatched response must fail, not look absent.
        rows = self.client.list_orders_by_ids(identifiers=[client_id])
        if not isinstance(rows, list) or len(rows) > 1:
            raise ValueError("INVALID_ORDER_LIST_RESPONSE")
        if not rows:
            return None
        if not isinstance(rows[0], dict) or rows[0].get("identifier") != client_id:
            raise ValueError("ORDER_CLIENT_ID_MISMATCH")
        return self.normalize(rows[0], market)

    def resolve_closed_order(self, market, order_id, client_id, windows):
        """Read one time window. Return (matching order, remaining windows).

        Closed orders have no page cursor: a full result requires splitting the
        creation-time window. Never interpret a truncated response as absence.
        """
        self.pair(market)
        if not windows or not client_id:
            raise ValueError("INVALID_CLOSED_ORDER_QUERY")
        start, end = windows[0]
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start <= end
                and end - start <= 7 * 86400 * 1000):
            raise ValueError("INVALID_CLOSED_ORDER_WINDOW")
        rows = self.client.list_closed_orders(ticker=market, states=["done", "cancel"],
            start_time=str(start), end_time=str(end), limit=1000, order_by="desc")
        if (not isinstance(rows, list) or len(rows) > 1000
                or any(not isinstance(r, dict) or not r.get("uuid") or r.get("state") not in {"done", "cancel"}
                       or r.get("market") != market for r in rows)):
            raise ValueError("INVALID_CLOSED_ORDER_RESPONSE")
        matches = [r for r in rows if (order_id and r["uuid"] == order_id) or r.get("identifier") == client_id]
        if len(matches) > 1:
            raise ValueError("AMBIGUOUS_CLOSED_ORDER")
        if matches:
            row = matches[0]
            if ((order_id and row["uuid"] != order_id)
                    or (row.get("identifier") and row["identifier"] != client_id)):
                raise ValueError("ORDER_IDENTITY_MISMATCH")
            return self.normalize(row, market), []
        remaining = list(windows[1:])
        if len(rows) == 1000:
            if end - start <= 1:
                raise ValueError("CLOSED_ORDER_WINDOW_TRUNCATED")
            middle = (start + end) // 2
            remaining = [(middle, end), (start, middle), *remaining]
        return None, remaining


class BithumbSpot(SpotAdapter):
    exchange = "bithumb"
    submission_id_fields = ("order_id", "uuid")

    def event(self, message, quantity, side):
        fields = dict(message.get("exact") or {})
        state = str(message.get("state", "")).lower()
        if message.get("event") == "order":
            if state == "wait" and fields.get("originalVolume") is not None and fields.get("cumulativeFilled") is None:
                fields["cumulativeFilled"] = "0"
            # v2 done messages can omit cumulative fields. A trade that already
            # proves full execution needs neither that message nor a REST lookup.
            if (state == "trade" and fields.get("cumulativeFilled") is not None
                    and fields.get("remainingVolume") is not None
                    and decimal(fields["cumulativeFilled"]) == decimal(quantity)
                    and decimal(fields["remainingVolume"]) == 0):
                state = "done"
        return super().event({**message, "state": state, "exact": fields}, quantity, side)

    def pair(self, market):
        super().pair(market)
        quote, base = market.split("-")
        # BithumbRest accepts BASE-QUOTE, then encodes QUOTE-BASE on the wire.
        return f"{base}-{quote}"


class CoinoneSpot(SpotAdapter):
    exchange = "coinone"

    def resolve_completed_order(self, market, order_id, client_id, side, quantity, probe):
        """Read one fill-history page. Only exact, cumulative full fills are terminal.

        The caller bounds requests/time and owns the scan state. History cannot
        prove cancellation, absence or acceptance of an unacknowledged intent.
        """
        if not order_id or side not in {"buy", "sell"} or decimal(quantity) <= 0:
            raise ValueError("COMPLETED_ORDER_ID_REQUIRED")
        start, end = int(probe["from"]), int(probe["to"])
        if not 0 <= start <= end or end - start > 90 * 86400 * 1000:
            raise ValueError("INVALID_COMPLETED_ORDER_WINDOW")
        cursor = probe.get("cursor")
        response = self.client.list_completed_orders(ticker=self.pair(market), size=100,
            from_ts=start, to_ts=end, **({"to_trade_id": cursor} if cursor else {}))
        rows = response["completed_orders"]
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("INVALID_COMPLETED_ORDERS")
        trades = dict(probe.get("trades", {}))
        for row in rows:
            if not isinstance(row, dict) or not row.get("trade_id") or not row.get("order_id"):
                raise ValueError("INVALID_COMPLETED_TRADE")
            if str(row["order_id"]) != order_id:
                continue
            if f'{row["quote_currency"]}-{row["target_currency"]}' != market:
                raise ValueError("ORDER_MARKET_MISMATCH")
            if not isinstance(row["is_ask"], bool) or row["is_ask"] != (side == "sell"):
                raise ValueError("ORDER_SIDE_MISMATCH")
            qty, price, stamp = decimal(row["qty"]), decimal(row["price"]), decimal(row["timestamp"])
            if qty <= 0 or price <= 0 or not start <= stamp <= end:
                raise ValueError("INVALID_COMPLETED_TRADE")
            fee = exact(row["fee"]) if row.get("fee") is not None and row.get("fee_currency") == market.split("-")[0] else None
            value = (exact(qty), exact(price), int(stamp), fee)
            trade_id = str(row["trade_id"])
            if trade_id in trades and trades[trade_id] != value:
                raise ValueError("CONFLICTING_COMPLETED_TRADE")
            trades[trade_id] = value
        filled = sum((decimal(t[0]) for t in trades.values()), Decimal(0))
        if filled > decimal(quantity):
            raise ValueError("COMPLETED_ORDER_QUANTITY_MISMATCH")
        more = len(rows) == 100
        next_cursor = str(rows[-1]["trade_id"]) if more else None
        cursors = set(probe.get("cursors", []))
        if more and next_cursor in cursors:
            raise ValueError("COMPLETED_ORDER_CURSOR_STALLED")
        if more:
            cursors.add(next_cursor)
        # Commit only after validating the whole page; a failed read can retry it.
        probe.update(trades=trades, cursor=next_cursor, cursors=list(cursors), filled=exact(filled))
        if filled != decimal(quantity):
            return None, more
        fee = exact(sum((decimal(t[3]) for t in trades.values()), Decimal(0))) if all(t[3] is not None for t in trades.values()) else None
        turnover = sum((decimal(t[0]) * decimal(t[1]) for t in trades.values()), Decimal(0))
        return Order(order_id, market, side, exact(quantity), exact(filled), "FILLED", client_id,
            fee, exact(turnover), max(t[2] for t in trades.values()) / 1000), False

    def is_order_missing(self, error):
        return (isinstance(error, ExchangeRequestError) and error.exchange == self.exchange
                and str(error.code) == "104")

    def resolve_missing_order(self, market, client_id):
        # Independent selector, same read-only endpoint. A 104 remains unknown,
        # never proof that an acknowledged order was filled/canceled/unaccepted.
        result = self.lookup(market, client_id=client_id)
        if result.client_id != client_id:
            raise ValueError("ORDER_CLIENT_ID_MISMATCH")
        return result

    def event(self, message, quantity, side):
        if (message.get("event") != "order" or message.get("reconcileRequired")
                or message.get("order", {}).get("orderType") != "limit"):
            return None
        fields = dict(message.get("exact") or {})
        state = str(message.get("state", "")).lower()
        # executed_qty is THIS fill (or a cancellation), never the cumulative
        # amount. Use the known limit-order quantity and post-trade remainder.
        # Cancellations have post-cancel remainder zero too; reconcile them.
        if decimal(fields.get("preventedVolume", "0")) != 0:
            return None
        if state == "wait" and fields.get("originalVolume") is not None:
            fields["cumulativeFilled"] = "0"
        elif state in {"trade", "trade_done"} and fields.get("remainingVolume") is not None:
            remaining, original = decimal(fields["remainingVolume"]), decimal(quantity)
            if not 0 <= remaining <= original or (state == "trade_done" and remaining != 0):
                return None
            fields["cumulativeFilled"] = exact(original - remaining)
            if remaining == 0:
                state = "trade_done"
        else:
            return None
        return super().event({**message, "state": state, "exact": fields}, quantity, side)

    def pair(self, market):
        super().pair(market)
        quote, base = market.split("-")
        return f"{base}-{quote}"

    def balance_details(self):
        return {row["currency"].upper(): balance_pair(row["available"], row["limit"])
                for row in self.client.get_all_balances()["balances"]}

    def balances(self):
        return {row["currency"].upper(): exact(row["available"]) for row in self.client.get_all_balances()["balances"]}

    def policy(self, market):
        pair = self.pair(market)
        row = self.client.get_market(pair)["markets"][0]
        if row["maintenance_status"] != 0 or row["trade_status"] != 1:
            raise ValueError("MARKET_NOT_ACTIVE")
        ranges = self.client.get_range_units(pair)["range_price_units"]
        fees = self.client.get_trade_fee(pair)["fee_rates"]
        fee = fees[0] if isinstance(fees, list) else fees
        return Policy(tuple((exact(r["range_min"]), exact(r["price_unit"])) for r in ranges),
                      exact(row["min_order_amount"]), exact(row["min_order_amount"]),
                      exact(max(decimal(fee["maker"]), decimal(fee["taker"]))), exact(row["qty_unit"]),
                      exact(row["max_order_amount"]), exact(row["min_qty"]), exact(row["max_qty"]),
                      exact(ranges[-1]["next_range_min"]))

    def submit(self, market, side, price, quantity, client_id):
        result = self.client.place_order(ticker=self.pair(market), side=side, order_type="limit",
            price=exact(price), volume=exact(quantity), user_order_id=client_id)
        if not result.get("order_id"):
            raise ValueError("ORDER_RESULT_UNKNOWN")
        return str(result["order_id"])

    def lookup(self, market, order_id=None, client_id=None):
        row = self.client.get_order(ticker=self.pair(market),
            **({"order_id": order_id} if order_id else {"user_order_id": client_id}))["order"]
        if f'{row["quote_currency"]}-{row["target_currency"]}' != market:
            raise ValueError("ORDER_MARKET_MISMATCH")
        status = {"LIVE": "OPEN", "PARTIALLY_FILLED": "OPEN", "PARTIALLY_CANCELED": "OPEN",
                  "FILLED": "FILLED", "CANCELED": "CANCELED"}[row["status"]]
        return Order(str(row["order_id"]), market, row["side"].lower(), exact(row["original_qty"]),
                     exact(row["executed_qty"]), status, str(row.get("user_order_id") or ""),
                     exact(row["fee"]) if row.get("fee") is not None else None,
                     exact(decimal(row["executed_qty"]) * decimal(row["average_executed_price"]))
                     if row.get("average_executed_price") is not None else None)

    def cancel(self, market, order_id):
        self.client.cancel_order(order_id, ticker=self.pair(market))


class KorbitSpot(SpotAdapter):
    exchange = "korbit"

    def pair(self, market):
        super().pair(market)
        quote, base = market.split("-")
        return f"{base.lower()}_{quote.lower()}"

    def balance_details(self):
        return {row["currency"].upper(): balance_pair(row["available"], decimal(row["balance"]) - decimal(row["available"]))
                for row in self.client.get_balances()}

    def balances(self):
        return {row["currency"].upper(): exact(row["available"]) for row in self.client.get_balances()}

    def policy(self, market):
        symbol = self.pair(market)
        ticks = next(r for r in self.client.get_tick_size_policy(symbol) if r["symbol"] == symbol)
        fee = next(r for r in self.client.get_trading_fee_policy(symbol) if r["symbol"] == symbol)
        return Policy(tuple((exact(r["priceGte"]), exact(r["tickSize"])) for r in ticks["tickSizePolicy"]),
                      "0", "0", exact(max(Decimal(0), decimal(fee["maxFeeRate"]))))

    def submit(self, market, side, price, quantity, client_id):
        row = self.client.place_order(symbol=self.pair(market), side=side, order_type="limit",
            price=exact(price), qty=exact(quantity), client_order_id=client_id)
        if not row.get("orderId"):
            raise ValueError("ORDER_RESULT_UNKNOWN")
        return str(row["orderId"])

    def lookup(self, market, order_id=None, client_id=None):
        row = self.client.get_order(self.pair(market),
            **({"order_id": order_id} if order_id else {"client_order_id": client_id}))
        if row.get("symbol") != self.pair(market):
            raise ValueError("ORDER_MARKET_MISMATCH")
        status = {"pending": "OPEN", "unfilled": "OPEN", "open": "OPEN", "partiallyFilled": "OPEN",
                  "filled": "FILLED", "canceled": "CANCELED", "partiallyFilledCanceled": "CANCELED",
                  "expired": "CANCELED"}[row["status"]]
        return Order(str(row["orderId"]), market, row["side"], exact(row["qty"]), exact(row["filledQty"]),
                     status, str(row.get("clientOrderId") or ""),
                     filled_at=execution_time(row.get("lastFilledAt")))

    def cancel(self, market, order_id):
        self.client.cancel_order(symbol=self.pair(market), order_id=order_id)


ADAPTERS = {adapter.exchange: adapter for adapter in (UpbitSpot, BithumbSpot, CoinoneSpot, KorbitSpot)}


def register_spot_adapter(exchange, adapter):
    if exchange in ADAPTERS or not issubclass(adapter, SpotAdapter) or adapter.exchange != exchange:
        raise ValueError("INVALID_ADAPTER_REGISTRATION")
    ADAPTERS[exchange] = adapter


def spot_adapter(exchange, client):
    if exchange not in ADAPTERS:
        raise ValueError("SPOT_ADAPTER_NOT_REGISTERED")
    return ADAPTERS[exchange](client)
