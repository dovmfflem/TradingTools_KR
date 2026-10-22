"""Native market/limit orders with exact final fills. Existing transports only.

Unlike the limit-only Order contract, market buys may have no original volume
and a terminal order may have filled less than the caller's target quantity.
No financial operation is retried by this adapter.
"""
import re
from decimal import Decimal, localcontext
from functools import wraps
from .spot_trading import spot_adapter, decimal, exact


def precise(fn):
    """Keep products of bounded API decimals exact without changing global context."""
    @wraps(fn)
    def call(*args, **kwargs):
        with localcontext() as context:
            context.prec = 80
            return fn(*args, **kwargs)
    return call


class DomesticSpot:
    def __init__(self, exchange, client):
        self.exchange, self.client = exchange, client
        self.spot = spot_adapter(exchange, client)

    def balances(self):
        return self.spot.balance_details()

    def policy(self, market):
        return self.spot.policy(market)

    def submit(self, market, side, quantity, client_id, *, amount=None, price=None):
        if side not in {"buy", "sell"} or not re.fullmatch(r"[a-z0-9_-]{1,36}", client_id):
            raise ValueError("INVALID_ORDER_INTENT")
        if decimal(quantity) <= 0:
            raise ValueError("INVALID_QUANTITY")
        if price is not None:
            return self.spot.submit(market, side, price, quantity, client_id)
        if side == "buy" and (amount is None or decimal(amount) <= 0):
            raise ValueError("MARKET_BUY_AMOUNT_REQUIRED")
        pair = self.spot.pair(market)
        if self.exchange == "korbit":
            row = self.client.place_order(symbol=pair, side=side, order_type="market", client_order_id=client_id,
                                         **({"amount": exact(amount)} if side == "buy" else {"qty": exact(quantity)}))
            key = "orderId"
        elif self.exchange == "coinone":
            row = self.client.place_order(ticker=pair, side=side, order_type="market", user_order_id=client_id,
                                         **({"amount": exact(amount)} if side == "buy" else {"volume": exact(quantity)}))
            key = "order_id"
        else:
            identity = "identifier" if self.exchange == "upbit" else "client_order_id"
            row = self.client.place_order(ticker=pair, side="bid" if side == "buy" else "ask",
                                         order_type="price" if side == "buy" else "market", **{identity: client_id},
                                         **({"price": exact(amount)} if side == "buy" else {"volume": exact(quantity)}))
            key = "uuid"
        identifier = (row.get("order_id") or row.get("uuid")) if isinstance(row, dict) and self.exchange == "bithumb" else row.get(key) if isinstance(row, dict) else None
        if not identifier:
            raise ValueError("ORDER_RESULT_UNKNOWN")
        return str(identifier)

    @precise
    def lookup(self, market, *, order_id=None, client_id=None):
        pair = self.spot.pair(market)
        if self.exchange == "korbit":
            row = self.client.get_order(pair, **({"order_id": order_id} if order_id else {"client_order_id": client_id}))
            if row["symbol"] != pair:
                raise ValueError("ORDER_MARKET_MISMATCH")
            oid, cid, side = row["orderId"], row.get("clientOrderId"), row["side"]
            state, filled, turnover = row["status"], row["filledQty"], row["filledAmt"]
            terminal = state in {"filled", "canceled", "partiallyFilledCanceled", "expired"}
            if state not in {"pending", "open", "unfilled", "partiallyFilled", "filled", "canceled", "partiallyFilledCanceled", "expired"}:
                raise ValueError("UNKNOWN_ORDER_STATE")
            fee = row.get("fee")
            fee_currency = row.get("feeCurrency")
        elif self.exchange == "coinone":
            row = self.client.get_order(ticker=pair, **({"order_id": order_id} if order_id else {"user_order_id": client_id}))["order"]
            if f'{row["quote_currency"]}-{row["target_currency"]}' != market:
                raise ValueError("ORDER_MARKET_MISMATCH")
            oid, cid, side = row["order_id"], row.get("user_order_id"), row["side"].lower()
            state, filled = row["status"], row["executed_qty"]
            terminal_states = {"FILLED", "CANCELED", "CANCELED_NO_ORDER", "CANCELED_LIMIT_PRICE_EXCEED", "CANCELED_UNDER_PRODUCT_UNIT"}
            if state not in terminal_states | {"LIVE", "PARTIALLY_FILLED", "PARTIALLY_CANCELED"}:
                raise ValueError("UNKNOWN_ORDER_STATE")
            terminal = state in terminal_states
            turnover = row.get("traded_amount")
            if turnover is None:
                turnover = decimal(filled) * decimal(row["average_executed_price"] or "0")
            fee, fee_currency = row.get("fee"), "KRW"
        else:
            key = "identifier" if self.exchange == "upbit" else "client_order_id"
            row = self.client.get_order(order_id, **({key: client_id} if not order_id else {}))
            if row["market"] != market:
                raise ValueError("ORDER_MARKET_MISMATCH")
            oid, cid, side = row["uuid"], row.get(key), {"bid": "buy", "ask": "sell"}[row["side"]]
            state, filled = row["state"], row["executed_volume"]
            if state not in {"wait", "watch", "done", "cancel"}:
                raise ValueError("UNKNOWN_ORDER_STATE")
            terminal = state in {"done", "cancel"}
            trades = row.get("trades")
            turnover = sum((decimal(t["funds"]) for t in trades), Decimal(0)) if isinstance(trades, list) else None
            fee, fee_currency = row.get("paid_fee"), "KRW"
        if order_id and str(oid) != str(order_id):
            raise ValueError("ORDER_ID_MISMATCH")
        if not order_id and str(cid or "") != client_id:
            raise ValueError("ORDER_CLIENT_ID_MISMATCH")
        if side not in {"buy", "sell"} or decimal(filled) < 0 or (turnover is not None and decimal(turnover) < 0):
            raise ValueError("INVALID_ORDER_FILL")
        return {"id": str(oid), "clientId": str(cid or ""), "market": market, "side": side,
                "state": state, "terminal": terminal, "filled": exact(filled),
                "turnover": exact(turnover) if turnover is not None else None,
                "fee": exact(fee) if fee is not None else None, "feeCurrency": str(fee_currency or "").upper()}

    def cancel(self, market, order_id):
        return self.spot.cancel(market, order_id)

    def open_orders(self, market):
        pair = self.spot.pair(market)
        if self.exchange == "upbit":
            rows = self.client.list_open_orders(ticker=pair, limit=100)
        elif self.exchange == "korbit":
            rows = self.client.get_open_orders(pair)
        elif self.exchange == "coinone":
            rows = self.client.list_active_orders(ticker=pair)
        else:
            rows = self.client.get_open_orders(ticker=pair, limit=100)
        if isinstance(rows, dict):
            rows = rows.get("active_orders", rows.get("orders"))
        if not isinstance(rows, list):
            raise ValueError("INVALID_OPEN_ORDERS")
        return rows
