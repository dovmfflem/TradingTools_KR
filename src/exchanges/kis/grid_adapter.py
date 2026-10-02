"""KIS protocol adapter for the shared grid order contract.

Order keys include session and date because a broker ODNO alone is not durable.
Application code supplies session selection and buying-power policy.
"""
import re
import time
from decimal import ROUND_HALF_UP
from ..spot_trading import Order, Policy, decimal
from ..api_error import ExchangeRequestError


class KisGridAdapter:
    exchange = "korea-investment"

    def __init__(self, client, session, *, clock=time.monotonic):
        self.client, self.session = client, session
        self.clock, self.history = clock, {}

    def pair(self, market):
        if not re.fullmatch(r"KRW-[A-Z0-9]{6,9}", market):
            raise ValueError("INVALID_FUTURES_SYMBOL")
        return market

    def policy(self, market):
        self.pair(market)
        # USD futures: KRW 0.1 tick, integer contracts, USD 10,000 multiplier.
        return Policy((("0", "0.1"),), "0", "0", "0", quantity_step="1", min_quantity="1")

    def balance_details(self):
        # Futures buying power is queried for the actual order by the caller.
        # Never misrepresent collateral as spendable spot coins.
        return {}

    def normalize_quote(self, market, bid, ask):
        # This adapter supports USD futures (0.1 KRW). Normalize feed precision
        # before strategy comparisons, not just when formatting notifications.
        policy = self.policy(market)
        def price(value):
            value = decimal(value)
            tick = next(decimal(tick) for lower, tick in reversed(policy.bands) if value >= decimal(lower))
            return (value / tick).to_integral_value(rounding=ROUND_HALF_UP) * tick
        bid, ask = decimal(bid), decimal(ask)
        if bid <= 0 or ask < bid:
            return bid, ask  # The engine discards invalid/crossed quotes.
        return price(bid), price(ask)

    def submit(self, market, side, price, quantity, client_id):
        self.policy(market).validate(side, price, quantity)
        window = self.session()
        if not window or window["cleanup"]:
            raise ValueError("KIS_SESSION_CLOSED")
        order_id = self.client.future_order(market[4:], side, night=window["night"],
                                            quantity=int(decimal(quantity)), price=price)
        self.history.clear()
        return window["id"] + ":" + order_id

    @staticmethod
    def identity(order_id):
        if not re.fullmatch(r"\d{8}[DN]:\d{1,20}", order_id or ""):
            raise ValueError("INVALID_KIS_ORDER_ID")
        return order_id[:8], order_id[8] == "N", order_id[10:]

    def lookup(self, market, order_id, client_id):
        date, night, raw_id = self.identity(order_id)
        key = (market, night, date)
        cached = self.history.get(key)
        if cached is None or self.clock() - cached[0] >= 1:
            rows = self.client.future_orders(market[4:], night=night, since=date)
            self.history[key] = (self.clock(), rows)
        else:
            rows = cached[1]
        matches = [r for r in rows if r["id"] == raw_id]
        if len(matches) != 1:
            raise ExchangeRequestError("kis", "ORDER_NOT_FOUND" if not matches else "ORDER_ID_AMBIGUOUS")
        row = matches[0]
        status = "FILLED" if decimal(row["filled"]) == decimal(row["quantity"]) else "OPEN" if decimal(row["remaining"]) else "CANCELED"
        return Order(order_id, market, row["side"], row["quantity"], row["filled"], status)

    def cancel(self, market, order_id):
        _, night, raw_id = self.identity(order_id)
        window = self.session()
        if not window or window["id"] != order_id[:9]:
            # The broker may reuse ODNO on another trading day. Never send an old
            # session's cancellation against a new session with the same number.
            raise ValueError("KIS_ORDER_SESSION_ENDED")
        self.history.clear()
        return self.client.future_cancel(raw_id, night=night)

    def invalidate_history(self):
        self.history.clear()

    def recover_order(self, market, order_id, client_id, side, quantity, probe):
        try:
            return self.lookup(market, order_id, client_id), True
        except ExchangeRequestError as error:
            if error.code == "ORDER_NOT_FOUND":
                # future_orders includes open, completed and canceled records.
                return None, True
            raise

    def event(self, message, quantity, side):
        return None  # Notices trigger an authoritative cumulative history read.
