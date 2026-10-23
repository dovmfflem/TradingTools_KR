"""Coinone/DigitalX compatibility, using existing library transports only.

Official source comparison (checked 2026-10-22):
https://docs.coinone.co.kr/reference/place-order
  LIMIT/MARKET/STOP_LIMIT, post_only, user_order_id, MARKET limit_price;
  no documented FOK/IOC. This adapter never approximates either option.
https://docs.coinone.co.kr/reference/order-detail
  executed_qty is cumulative; market buys can omit original_qty. Cancellation
  can retain fills. PARTIALLY_CANCELED is not terminal.
https://docs.digitalx.miraeasset.com/llms-full.txt
  /v2/orders supports timeInForce=gtc/ioc/fok/po, clientOrderId, and native
  best orders (bestNth required). Status filled can describe a partial IOC.

The only extra endpoint surface here is DigitalX currencyPairs and extended
order parameters, both using KorbitRest.request for signing/timeouts/errors.
No new transport, retries, sleep, credentials or wallet operations. Individual
fill history is a separate explicitly bounded reconciliation read.
Single calls retain configured timeouts (DigitalX defaults to 3s per
connect/read). Explicit total budgets include time-sync. Books/ID reads cap fanout at
10 requests. Caller owns the end-to-end deadline and persistent order context.
"""
from collections import OrderedDict
from dataclasses import replace
from decimal import localcontext
import re
import time

from .spot_trading import balance_pair, decimal, exact
from .api_error import ExchangeRequestError


def _rows(value):
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError("INVALID_EXCHANGE_ROWS")
    return value


def _nonnegative(value):
    number = decimal(value)
    if number < 0:
        raise ValueError("NEGATIVE_EXCHANGE_VALUE")
    return exact(number)


def _positive(value):
    number = decimal(value)
    if number <= 0:
        raise ValueError("POSITIVE_ORDER_VALUE_REQUIRED")
    return exact(number)


class NativeArbitrageSpot:
    def __init__(self, exchange, client, spot):
        self.exchange, self.client, self.spot = exchange, client, spot
        self._markets = OrderedDict()

    def market(self, ticker):
        if not isinstance(ticker, str):
            raise ValueError("ORDER_MARKET_REQUIRED")
        value = ticker.strip().upper().replace("_", "-")
        if value.endswith("-KRW"):
            value = "KRW-" + value[:-4]
        if not re.fullmatch(r"KRW-[A-Z0-9]{1,20}", value) or value == "KRW-KRW":
            raise ValueError("UNSUPPORTED_SPOT_MARKET")
        self.spot.pair(value)
        return value

    def _identifier(self, value):
        pattern = r"[a-z0-9_.-]{1,150}" if self.exchange == "coinone" else r"[a-zA-Z0-9.:_-]{1,36}"
        if value is not None and (not isinstance(value, str) or not re.fullmatch(pattern, value)):
            raise ValueError("INVALID_ORDER_IDENTIFIER")

    def _remember(self, market, order_id=None, identifier=None):
        for kind, value in (("uuid", order_id), ("identifier", identifier)):
            if value is None:
                continue
            key = (kind, str(value))
            if key in self._markets and self._markets[key] != market:
                raise ValueError("ORDER_MARKET_MISMATCH")
            self._markets[key] = market
            self._markets.move_to_end(key)
        while len(self._markets) > 2048:
            self._markets.popitem(last=False)

    def _selector(self, order_id, identifier, ticker):
        if (order_id is None) == (identifier is None):
            raise ValueError("EXACTLY_ONE_ORDER_IDENTIFIER_REQUIRED")
        self._identifier(identifier)
        if order_id is not None and (isinstance(order_id, bool) or not isinstance(order_id, (str, int)) or not str(order_id)):
            raise ValueError("INVALID_ORDER_ID")
        key = ("uuid", str(order_id)) if order_id is not None else ("identifier", identifier)
        market = self.market(ticker) if ticker is not None else self._markets.get(key)
        if market is None:
            raise ValueError("ORDER_MARKET_REQUIRED_FOR_RESTART")
        if key in self._markets and self._markets[key] != market:
            raise ValueError("ORDER_MARKET_MISMATCH")
        return market

    def get_accounts(self):
        raw = self.client.get_all_balances()["balances"] if self.exchange == "coinone" else self.client.get_balances()
        result, currencies = [], set()
        with localcontext() as ctx:
            ctx.prec = 80
            for row in _rows(raw):
                currency = str(row["currency"]).upper()
                if not re.fullmatch(r"[A-Z0-9]{1,20}", currency) or currency in currencies:
                    raise ValueError("INVALID_BALANCE_CURRENCY")
                currencies.add(currency)
                locked = row["limit"] if self.exchange == "coinone" else decimal(row["balance"]) - decimal(row["available"])
                amounts = balance_pair(row["available"], locked)
                item = {"currency": currency, "balance": amounts["available"], "locked": amounts["locked"]}
                average = row.get("average_price" if self.exchange == "coinone" else "avgPrice")
                if average is not None:
                    item["avg_buy_price"] = _nonnegative(average)
                result.append(item)
        return result

    def get_order_fill_evidence(self, order_id, *, ticker, order, max_pages=4):
        from .arbitrage_fill_evidence import collect_order_fill_evidence
        market = self.market(ticker)
        return collect_order_fill_evidence(self.exchange, self.client, self.spot.pair(market),
                                          market, order_id, order, max_pages=max_pages)

    def list_wallet_statuses(self):
        """Public currency/network availability, never withdrawal authorization."""
        raw = self.client.list_currencies()
        rows = _rows(raw["currencies"] if self.exchange == "coinone" else raw)
        result = []
        for asset in rows:
            currency = str(asset["symbol"] if self.exchange == "coinone" else asset["name"]).upper()
            if not re.fullmatch(r"[A-Z0-9]{1,20}", currency):
                raise ValueError("INVALID_WALLET_CURRENCY")
            if self.exchange == "coinone":
                routes = [(None, asset["deposit_status"], asset["withdraw_status"])]
                enabled, disabled = "normal", "suspended"
            else:
                enabled, disabled = "launched", "stopped"
                networks = asset.get("networkList")
                if networks is None and currency == "KRW":
                    routes = [(None, asset["depositStatus"], asset["withdrawalStatus"])]
                else:
                    networks = _rows(networks)
                    if not networks:
                        raise ValueError("WALLET_NETWORK_STATUS_UNAVAILABLE")
                    routes = [(r["name"], r["depositStatus"], r["withdrawalStatus"]) for r in networks]
            for network, deposit, withdraw in routes:
                if deposit not in (enabled, disabled) or withdraw not in (enabled, disabled):
                    raise ValueError("UNKNOWN_WALLET_STATUS")
                state = ("working" if deposit == enabled and withdraw == enabled else
                         "deposit_only" if deposit == enabled else "withdraw_only" if withdraw == enabled else "paused")
                item = {"currency": currency, "wallet_state": state}
                if network is not None:
                    if not isinstance(network, str) or not network:
                        raise ValueError("INVALID_WALLET_NETWORK")
                    item["net_type"] = network
                result.append(item)
        return result

    @staticmethod
    def _limit(limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("ORDER_LIST_LIMIT_MUST_BE_1_TO_1000")
        return limit

    def list_open_orders(self, *, ticker=None, limit=100):
        limit = self._limit(limit)
        market = self.market(ticker) if ticker is not None else None
        if self.exchange == "coinone":
            # Current active_orders allows omitted currencies (all markets).
            # The older open_orders/all and active_orders/all are not aliases.
            raw = (self.client.list_active_orders(ticker=self.spot.pair(market)) if market else
                   self.client._request("/v2.1/order/active_orders"))
            rows = _rows(raw["active_orders"])
        else:
            if market is None:
                raise ValueError("KORBIT_OPEN_ORDERS_REQUIRE_TICKER")
            rows = _rows(self.client.get_open_orders(self.spot.pair(market), limit=limit))
        result = []
        for row in rows:
            if self.exchange == "coinone":
                actual = self.market(f'{row["quote_currency"]}-{row["target_currency"]}')
                if market is not None and actual != market:
                    raise ValueError("ORDER_MARKET_MISMATCH")
                state = "NOT_TRIGGERED" if row.get("type") == "STOP_LIMIT" and row.get("is_triggered") is False else "LIVE"
                item = self._normalize_order({**row, "status": state}, actual)
            else:
                # DigitalX openOrders does not return symbol; bind request context.
                item = self._normalize_order({"symbol": self.spot.pair(market), **row}, market)
            if item["terminal"]:
                raise ValueError("TERMINAL_ORDER_IN_OPEN_RESPONSE")
            result.append(item)
        return result[:limit]

    def list_order_history_probe(self, *, ticker, limit=1):
        """One read-only health probe, NOT closed-order/absence evidence.

        Coinone returns raw fills, DigitalX raw recent order rows. Keeping this
        separate prevents a partially filled live order being labeled closed.
        """
        limit = self._limit(limit)
        market = self.market(ticker)
        pair = self.spot.pair(market)
        if self.exchange == "coinone":
            if limit > 100:
                raise ValueError("COINONE_HISTORY_LIMIT_MAX_100")
            now = int(time.time() * 1000)
            rows = _rows(self.client.list_completed_orders(ticker=pair, size=limit,
                from_ts=now - 86400000, to_ts=now)["completed_orders"])
            for row in rows:
                if self.market(f'{row["quote_currency"]}-{row["target_currency"]}') != market or not row.get("trade_id") or not row.get("order_id"):
                    raise ValueError("INVALID_HISTORY_RESPONSE")
        else:
            rows = _rows(self.client.list_all_orders(pair, limit=limit))
            for row in rows:
                if row.get("symbol") != pair or not row.get("orderId"):
                    raise ValueError("INVALID_HISTORY_RESPONSE")
        return rows

    def get_trading_policy(self, ticker):
        market = self.market(ticker)
        policy = self.spot.policy(market)
        if self.exchange == "coinone":
            return policy
        matches = [r for r in self.list_trading_pairs(is_details=True) if r["market"] == market]
        if len(matches) != 1 or not matches[0]["trading_available"]:
            raise ValueError("MARKET_NOT_ACTIVE")
        row = matches[0]["native"]
        # Preserve the existing library quantity step; currencyPairs publishes
        # order-value bounds, not a per-market quantity precision.
        minimum = _nonnegative(row["minOrderValue"]) if row.get("minOrderValue") is not None else "0"
        maximum = _positive(row["maxOrderValue"]) if row.get("maxOrderValue") is not None else None
        return replace(policy, min_buy=minimum, min_sell=minimum, max_total=maximum)

    def list_trading_pairs(self, *, is_details=False):
        if self.exchange == "coinone":
            rows = _rows(self.client.list_markets(quote_currency="KRW")["markets"])
        else:
            rows = _rows(self.client.request("GET", "/v2/currencyPairs", private=False))
        result = []
        for row in rows:
            if self.exchange == "coinone":
                market = self.market(f'{row["quote_currency"]}-{row["target_currency"]}')
                available = row["maintenance_status"] == 0 and row["trade_status"] == 1
            else:
                if str(row["quoteCurrency"]).upper() != "KRW":
                    continue
                market = self.market(f'{row["quoteCurrency"]}-{row["baseCurrency"]}')
                if row["symbol"] != self.spot.pair(market):
                    raise ValueError("MARKET_IDENTITY_MISMATCH")
                available = row["status"] == "launched"
            item = {"market": market, "trading_available": available}
            if is_details:
                item["native"] = dict(row)
            result.append(item)
        return result

    def _book(self, ticker, count):
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError("ORDERBOOK_COUNT_MUST_BE_POSITIVE_INTEGER")
        market = self.market(ticker)
        pair = self.spot.pair(market)
        if self.exchange == "coinone":
            count = min(count, 16)
            size = next(n for n in (5, 10, 15, 16) if n >= count)
            raw = self.client.get_orderbook(pair, size=size)
            if self.market(f'{raw.get("quote_currency", "")}-{raw.get("target_currency", "")}') != market:
                raise ValueError("ORDERBOOK_MARKET_MISMATCH")
        else:
            raw = self.client.get_orderbook(pair)
            if raw.get("symbol", pair) != pair:
                raise ValueError("ORDERBOOK_MARKET_MISMATCH")
        result = {"market": market, "ticker": market[4:].lower(), "exchange": self.exchange,
                  "source": "orderbook", "timestamp": raw.get("timestamp", 0)}
        for side in ("bids", "asks"):
            rows = [{"price": _positive(r["price"]), "qty": _nonnegative(r["qty"])}
                    for r in _rows(raw[side])]
            active = sorted((r for r in rows if decimal(r["qty"]) > 0),
                            key=lambda r: decimal(r["price"]), reverse=side == "bids")
            if not active:
                raise ValueError("EMPTY_ORDERBOOK")
            result[side] = active[:count]
        if decimal(result["asks"][0]["price"]) < decimal(result["bids"][0]["price"]):
            raise ValueError("CROSSED_ORDERBOOK")
        return result

    def get_orderbook_parse(self, ticker, count=5):
        book = self._book(ticker, count)
        for side in ("bids", "asks"):
            book[side] = [{"price": float(r["price"]), "qty": float(r["qty"])} for r in book[side]]
        return book

    def get_best_quotes(self, tickers):
        """One public all-ticker read, then filter up to 599 KRW markets.

        Missing markets are omitted, never filled with last trades. Malformed
        requested quotes fail the read. DigitalX publishes no quote timestamp
        here: lastTradedAt is deliberately NOT a freshness substitute.
        Sources: Coinone /reference/tickers; DigitalX GET /v2/tickers.
        """
        if not isinstance(tickers, (list, tuple)) or not 1 <= len(tickers) <= 599:
            raise ValueError("BEST_QUOTES_REQUIRE_1_TO_599_MARKETS")
        markets = list(dict.fromkeys(self.market("KRW-" + t.strip().upper()
            if isinstance(t, str) and re.fullmatch(r"[A-Za-z0-9]{1,20}", t.strip()) else t)
            for t in tickers))
        wanted, found = set(markets), {}
        if self.exchange == "coinone":
            rows = _rows(self.client.list_tickers(quote_currency="KRW")["tickers"])
        else:
            rows = _rows(self.client.request("GET", "/v2/tickers", private=False))
        for row in rows:
            if self.exchange == "coinone":
                market = f'{row.get("quote_currency", "")}-{row.get("target_currency", "")}'.upper()
            else:
                market = "-".join(reversed(str(row.get("symbol", "")).upper().split("_")))
            if market not in wanted:
                continue
            if market in found:
                raise ValueError("DUPLICATE_MARKET_QUOTE")
            if self.exchange == "coinone":
                bids, asks = _rows(row["best_bids"]), _rows(row["best_asks"])
                if not bids or not asks:
                    continue
                bid = max(decimal(_positive(r["price"])) for r in bids)
                ask = min(decimal(_positive(r["price"])) for r in asks)
            else:
                bid, ask = decimal(_positive(row["bestBidPrice"])), decimal(_positive(row["bestAskPrice"]))
            if bid > ask:
                raise ValueError("CROSSED_ORDERBOOK")
            found[market] = {"market": market,
                             "orderbook_units": [{"bid_price": float(bid), "ask_price": float(ask)}]}
            if self.exchange == "coinone" and row.get("timestamp") is not None:
                found[market]["timestamp"] = int(decimal(_nonnegative(row["timestamp"])))
        return [found[m] for m in markets if m in found]

    def get_orderbooks(self, tickers):
        if not isinstance(tickers, (list, tuple)) or not 1 <= len(tickers) <= 10:
            raise ValueError("ORDERBOOKS_REQUIRE_1_TO_10_MARKETS")
        markets = [self.market(t) for t in tickers]  # Validate all before I/O.
        result = []
        for market in markets:
            book = self._book(market, 15)
            # Upbit units pair ranks. Preserve unequal depth with zero-size
            # absent sides, never duplicate liquidity from the shorter side.
            units = []
            for i in range(max(len(book["asks"]), len(book["bids"]))):
                unit = {}
                for side in ("ask", "bid"):
                    rows = book[side + "s"]
                    row = rows[i] if i < len(rows) else {"price": rows[-1]["price"], "qty": "0"}
                    unit.update({side + "_price": float(row["price"]), side + "_size": float(row["qty"])})
                units.append(unit)
            result.append({"market": market, "timestamp": book["timestamp"], "orderbook_units": units})
        return result

    def place_order(self, *, ticker, side, order_type, price=None, volume=None,
                    identifier=None, time_in_force=None, limit_price=None):
        market = self.market(ticker)
        self._identifier(identifier)
        if identifier is None:
            raise ValueError("ORDER_IDENTIFIER_REQUIRED")
        if side not in ("bid", "ask") or order_type not in ("limit", "price", "market", "best"):
            raise ValueError("INVALID_ORDER_SIDE_OR_TYPE")
        if time_in_force not in (None, "fok", "ioc", "post_only"):
            raise ValueError("UNSUPPORTED_TIME_IN_FORCE")
        if self.exchange == "coinone" and (time_in_force in ("fok", "ioc") or order_type == "best"):
            raise ValueError("COINONE_FOK_IOC_BEST_UNSUPPORTED")
        if order_type == "price" and side != "bid" or order_type == "market" and side != "ask":
            raise ValueError("MARKET_BUY_USES_PRICE_MARKET_SELL_USES_VOLUME")
        if order_type == "limit":
            price, volume = _positive(price), _positive(volume)
            if limit_price is not None:
                raise ValueError("LIMIT_PRICE_ONLY_FOR_COINONE_MARKET")
        else:
            if side == "bid":
                price = _positive(price)
                if volume is not None:
                    raise ValueError("MARKET_BUY_VOLUME_MUST_BE_ABSENT")
            else:
                volume = _positive(volume)
                if price is not None:
                    raise ValueError("MARKET_SELL_PRICE_MUST_BE_ABSENT")
            if order_type == "best":
                if time_in_force not in ("ioc", "fok"):
                    raise ValueError("BEST_REQUIRES_IOC_OR_FOK")
            elif time_in_force not in (None, "ioc"):
                raise ValueError("MARKET_TIME_IN_FORCE_UNSUPPORTED")
            if self.exchange == "coinone":
                limit_price = _positive(limit_price)
        if self.exchange == "korbit" and limit_price is not None:
            raise ValueError("PRICE_LIMITED_MARKET_UNSUPPORTED")
        self._remember(market, identifier=identifier)  # Timeout recovery by intent ID.
        pair = self.spot.pair(market)
        if self.exchange == "coinone":
            kwargs = dict(ticker=pair, side=side, user_order_id=identifier,
                          order_type="limit" if order_type == "limit" else "market")
            if order_type == "limit":
                kwargs.update(price=price, volume=volume, post_only=time_in_force == "post_only")
            else:
                kwargs.update(limit_price=limit_price)
                kwargs.update({"amount": price} if side == "bid" else {"volume": volume})
            raw = self.client.place_order(**kwargs)
            order_id = raw.get("order_id") if isinstance(raw, dict) else None
        else:
            params = dict(symbol=pair, side="buy" if side == "bid" else "sell",
                          orderType=order_type if order_type in ("limit", "best") else "market",
                          clientOrderId=identifier, accountSeq=1)
            if order_type == "limit":
                params.update(price=price, qty=volume)
            else:
                params.update({"amt": price} if side == "bid" else {"qty": volume})
            if order_type == "best":
                params["bestNth"] = 1
            if time_in_force is not None:
                params["timeInForce"] = "po" if time_in_force == "post_only" else time_in_force
            raw = self.client.request("POST", "/v2/orders", params)
            order_id = raw.get("orderId") if isinstance(raw, dict) else None
        if not order_id:
            raise ExchangeRequestError(self.exchange, "ORDER_RESULT_UNKNOWN", outcome_unknown=True)
        self._remember(market, order_id, identifier)
        return {"uuid": str(order_id), "identifier": identifier, "market": market,
                "side": side, "ord_type": order_type}  # Acknowledgement, not fill proof.

    def get_order(self, order_id=None, identifier=None, ticker=None):
        market = self._selector(order_id, identifier, ticker)
        pair = self.spot.pair(market)
        selector = {"order_id": order_id} if order_id is not None else {
            "user_order_id" if self.exchange == "coinone" else "client_order_id": identifier}
        if self.exchange == "coinone":
            raw = self.client.get_order(ticker=pair, **selector)["order"]
        else:
            raw = self.client.get_order(pair, **selector)
        result = self._normalize_order(raw, market)
        if order_id is not None and result["uuid"] != str(order_id):
            raise ValueError("ORDER_ID_MISMATCH")
        # Coinone documents selection by user_order_id, but does not promise
        # that field in its response. Only the selected ID may be attached.
        if identifier is not None:
            if result.get("identifier") not in (None, "", identifier):
                raise ValueError("ORDER_CLIENT_ID_MISMATCH")
            if self.exchange == "korbit" and result.get("identifier") != identifier:
                raise ValueError("ORDER_CLIENT_ID_MISMATCH")
            result["identifier"] = identifier
        self._remember(market, result["uuid"], result.get("identifier"))
        return result

    def _normalize_order(self, row, market):
        with localcontext() as ctx:
            ctx.prec = 80
            return self._normalize_order_exact(row, market)

    def _normalize_order_exact(self, row, market):
        if not isinstance(row, dict):
            raise ValueError("INVALID_ORDER_RESPONSE")
        if self.exchange == "coinone":
            if self.market(f'{row["quote_currency"]}-{row["target_currency"]}') != market:
                raise ValueError("ORDER_MARKET_MISMATCH")
            states = {"LIVE": "wait", "PARTIALLY_FILLED": "wait", "PARTIALLY_CANCELED": "wait",
                "FILLED": "done", "CANCELED": "cancel", "CANCELED_NO_ORDER": "cancel",
                "CANCELED_LIMIT_PRICE_EXCEED": "cancel", "CANCELED_UNDER_PRODUCT_UNIT": "cancel",
                "NOT_TRIGGERED": "watch", "NOT_TRIGGERED_PARTIALLY_CANCELED": "watch",
                "NOT_TRIGGERED_CANCELED": "cancel", "TRIGGERED": "wait"}
            oid, cid, side = row["order_id"], row.get("user_order_id"), row["side"].lower()
            kind = row["type"].lower()
            filled, qty = _nonnegative(row["executed_qty"]), row.get("original_qty")
            remain = row.get("remain_qty")
            funds = row.get("traded_amount")
            estimated = funds is None and decimal(filled) > 0
            average = row.get("average_executed_price")
            if funds is None:
                funds = exact(decimal(filled) * decimal(average)) if decimal(filled) else "0"
            fee = row.get("fee")
            fee_currency = "KRW" if fee is not None else None
        else:
            if row["symbol"] != self.spot.pair(market):
                raise ValueError("ORDER_MARKET_MISMATCH")
            states = {"pending": "wait", "open": "wait", "partiallyFilled": "wait",
                      "filled": "done", "canceled": "cancel", "partiallyFilledCanceled": "cancel", "expired": "cancel"}
            oid, cid, side = row["orderId"], row.get("clientOrderId"), row["side"]
            kind = row["orderType"]
            filled, qty = _nonnegative(row["filledQty"]), row.get("qty")
            remain = None
            funds, average, estimated = row["filledAmt"], row.get("avgPrice"), False
            fee, fee_currency = row.get("fee"), row.get("feeCurrency")
        if side not in ("buy", "sell") or not oid or kind not in ("limit", "market", "best", "stop_limit"):
            raise ValueError("INVALID_ORDER_RESPONSE")
        if row["status"] not in states:
            raise ValueError("UNKNOWN_ORDER_STATE")
        state = states[row["status"]]
        funds = _nonnegative(funds)
        if (decimal(filled) == 0) != (decimal(funds) == 0):
            raise ValueError("INCONSISTENT_ORDER_FUNDS")
        if qty is not None:
            qty = _nonnegative(qty)
            if decimal(filled) > decimal(qty):
                raise ValueError("ORDER_OVERFILLED")
        if remain is not None:
            remain = _nonnegative(remain)
            if qty is not None and decimal(filled) + decimal(remain) > decimal(qty):
                raise ValueError("INCONSISTENT_ORDER_REMAINDER")
        elif qty is not None:
            remain = exact(decimal(qty) - decimal(filled)) if state == "wait" else "0"
        result = {"uuid": str(oid), "identifier": cid, "market": market,
            "side": "bid" if side == "buy" else "ask",
            "ord_type": "price" if kind == "market" and side == "buy" else kind,
            "state": state, "native_state": row["status"], "terminal": state in ("done", "cancel"),
            "executed_volume": filled, "executed_funds": funds, "executed_funds_estimated": estimated,
            "volume": qty, "remaining_volume": remain, "trades_complete": False,
            "average_executed_price": _nonnegative(average) if average is not None else None,
            "paid_fee": _nonnegative(fee) if fee is not None and str(fee_currency).upper() == "KRW" else None,
            "fee_currency": fee_currency}
        if row.get("price") is not None:
            result["price"] = _nonnegative(row["price"])
        return result

    def cancel_order(self, order_id=None, identifier=None, ticker=None):
        market = self._selector(order_id, identifier, ticker)
        pair = self.spot.pair(market)
        selector = {"order_id": order_id} if order_id is not None else {
            "user_order_id" if self.exchange == "coinone" else "client_order_id": identifier}
        if self.exchange == "coinone":
            self.client.cancel_order(ticker=pair, **selector)
        else:
            self.client.cancel_order(symbol=pair, **selector)
        # Cancellation ACK is not a final-order snapshot; reconcile with get_order.
        return {"uuid": str(order_id) if order_id is not None else None,
                "identifier": identifier, "market": market, "cancel_requested": True}

    def list_orders(self, *, uuids=None, identifiers=None, ticker=None):
        if (uuids is None) == (identifiers is None):
            raise ValueError("EXPLICIT_ORDER_IDS_REQUIRED_NO_HISTORY_SCAN")
        values = uuids if uuids is not None else identifiers
        if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 10:
            raise ValueError("ORDER_LIST_REQUIRES_1_TO_10_IDS")
        selections = [(v, None) if uuids is not None else (None, v) for v in values]
        for oid, cid in selections:
            self._selector(oid, cid, ticker)
        return [self.get_order(oid, cid, ticker) for oid, cid in selections]
