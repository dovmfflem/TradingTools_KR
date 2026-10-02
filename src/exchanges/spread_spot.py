"""Currency-spread reads over the existing domestic spot adapters.

Order placement, policy, REST reconciliation and private events remain owned by
spot_trading. No independent authentication or HTTP transport is introduced.
"""
import time
from .spot_trading import spot_adapter, decimal, exact


class SpreadSpot:
    def __init__(self, exchange, client, *, clock=time.time):
        self.adapter = spot_adapter(exchange, client)
        self.exchange, self.client, self.clock = exchange, client, clock

    def __getattr__(self, key):
        return getattr(self.adapter, key)

    def holdings(self, asset):
        balances = self.adapter.balance_details()
        result = {}
        for key in ("KRW", asset):
            row = balances.get(key, {"available": "0", "locked": "0"})
            available = decimal(row["available"])
            total = available + decimal(row["locked"])
            if not 0 <= available <= total:
                raise ValueError("INVALID_SPOT_ACCOUNT")
            result[key] = {"available": exact(available), "total": exact(total)}
        return result

    def quote(self, market):
        book = self.client.get_orderbook(self.adapter.pair(market))
        if self.exchange in {"upbit", "bithumb"}:
            units = book["orderbook_units"]
            bid = max(decimal(row["bid_price"]) for row in units)
            ask = min(decimal(row["ask_price"]) for row in units)
            bid_sizes = [row.get("bid_size") for row in units if decimal(row["bid_price"]) == bid]
            ask_sizes = [row.get("ask_size") for row in units if decimal(row["ask_price"]) == ask]
        else:
            bid = max(decimal(row["price"]) for row in book["bids"])
            ask = min(decimal(row["price"]) for row in book["asks"])
            bid_sizes = [row.get("qty") for row in book["bids"] if decimal(row["price"]) == bid]
            ask_sizes = [row.get("qty") for row in book["asks"] if decimal(row["price"]) == ask]
        if not 0 < bid <= ask:
            raise ValueError("INVALID_SPOT_QUOTE")
        # Some exchanges publish no book timestamp: the completed REST read is
        # the observation time; the application also bounds total request age.
        stamp = decimal(book.get("timestamp", self.clock()))
        if stamp >= 100_000_000_000:
            stamp /= 1000
        def size(values):
            if any(value is None for value in values):
                return None
            parsed = [decimal(value) for value in values]
            if any(value < 0 for value in parsed):
                raise ValueError("INVALID_SPOT_DEPTH")
            return exact(sum(parsed))
        return {"bid": exact(bid), "ask": exact(ask), "bid_size": size(bid_sizes),
                "ask_size": size(ask_sizes), "timestamp": float(stamp)}
