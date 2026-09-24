"""Strict normalized reads for the currency-spread caller."""
from ..spot_trading import BithumbSpot, decimal, exact


class BithumbSpread(BithumbSpot):
    def quote(self, market):
        row = self.client.get_orderbook(self.pair(market))
        level = row["orderbook_units"][0]
        bid, ask = decimal(level["bid_price"]), decimal(level["ask_price"])
        if not 0 < bid <= ask:
            raise ValueError("INVALID_SPOT_QUOTE")
        return {"bid": exact(bid), "ask": exact(ask), "timestamp": float(row["timestamp"]) / 1000}

    def open_orders(self, market):
        # Strict response validation: the legacy list helper returns [] for malformed data.
        rows = self.client._request("GET", "/v1/orders", params={"market": self.pair(market), "state": "wait", "limit": 100, "page": 1})
        if not isinstance(rows, list) or len(rows) >= 100:
            raise ValueError("SPOT_ORDER_LIST_INCOMPLETE")
        return [self.normalize(row, market) for row in rows]

    def holdings(self, asset):
        rows = self.client.get_accounts()
        if not isinstance(rows, list):
            raise ValueError("INVALID_SPOT_ACCOUNT")
        result = {key: {"available": "0", "total": "0"} for key in ("KRW", asset)}
        for row in rows:
            currency = row["currency"]
            if currency in result:
                available, locked = decimal(row["balance"]), decimal(row["locked"])
                if min(available, locked) < 0:
                    raise ValueError("INVALID_SPOT_ACCOUNT")
                result[currency] = {"available": exact(available), "total": exact(available + locked)}
        return result
