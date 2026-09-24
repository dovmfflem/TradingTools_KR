"""Exact, venue-independent spot trading contract. No implicit mutation retries.

Exchange wire fields belong here; strategy/account allocation belongs to callers.
Register another adapter to extend the engine without changing its order logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re


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

    def __init__(self, client):
        self.client = client

    def pair(self, market):
        quote, base = market.split("-", 1)
        if quote not in self.quotes or not re.fullmatch(r"[A-Z0-9]{1,20}", base):
            raise ValueError("UNSUPPORTED_SPOT_MARKET")
        return market

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
                     exact(turnover) if turnover is not None else None)

    def submit(self, market, side, price, quantity, client_id):
        if side not in {"buy", "sell"} or not re.fullmatch(r"[a-z0-9_-]{1,36}", client_id):
            raise ValueError("INVALID_ORDER_INTENT")
        key = "identifier" if self.exchange == "upbit" else "client_order_id"
        data = self.client.place_order(ticker=self.pair(market), side="bid" if side == "buy" else "ask",
            order_type="limit", price=exact(price), volume=exact(quantity), **{key: client_id})
        order_id = data.get("uuid")
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
                     exact(fields["fee"]) if fields.get("fee") is not None else None)


class UpbitSpot(SpotAdapter):
    exchange = "upbit"


class BithumbSpot(SpotAdapter):
    exchange = "bithumb"


class CoinoneSpot(SpotAdapter):
    exchange = "coinone"

    def pair(self, market):
        super().pair(market)
        quote, base = market.split("-")
        return f"{base}-{quote}"

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
                     status, str(row.get("clientOrderId") or ""))

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
