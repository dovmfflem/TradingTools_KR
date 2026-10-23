"""Synthetic source-to-wire comparison. No network or real credentials.

Sources and deliberate gaps are documented in arbitrage_native_spot.py.
Artifacts record sanitized synthetic requests/responses, never auth material.
"""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import socket
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs
from urllib3.util import Timeout

import requests
from requests.adapters import HTTPAdapter

from src.exchanges.api_error import ExchangeRequestError
from src.exchanges.arbitrage_spot import wrap_spot, SPOT_CAPABILITIES
from src.exchanges.coinone.coinone_rest import CoinoneRest
from src.exchanges.korbit.korbit_rest import KorbitRest


class NativeSpotFixture(unittest.TestCase):
    http_evidence = []

    @classmethod
    def tearDownClass(cls):
        root = Path(__file__).parent / "results" / "arbitrage-native-spot"
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        (root / f"synthetic-http-{stamp}.json").write_text(json.dumps({
            "mode": "synthetic only; network blocked", "requests": cls.http_evidence,
            "sources": ["https://docs.coinone.co.kr/reference/place-order",
                        "https://docs.coinone.co.kr/reference/order-detail",
                        "https://docs.coinone.co.kr/reference/find-completed-orders",
                        "https://docs.coinone.co.kr/reference/find-balance",
                        "https://docs.digitalx.miraeasset.com/llms-full.txt"],
        }, indent=2), encoding="utf-8")

    def setUp(self):
        self.enterContext(patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")))
        self.coin = CoinoneRest("synthetic-key", "synthetic-secret", timeout_seconds=2)
        self.korb = KorbitRest("synthetic-key", "synthetic-secret")
        self.korb._server_time, self.korb._synced_at = 1700000000000, time.monotonic()
        self.addCleanup(self.coin._session.close)
        self.addCleanup(self.korb._session.close)
        self.coin._session.post = Mock(side_effect=lambda url, **kw: self.http("coinone", "POST", url, **kw))
        self.coin._session.get = Mock(side_effect=lambda url, **kw: self.http("coinone", "GET", url, **kw))
        self.korb._session.request = Mock(side_effect=lambda method, url, **kw: self.http("korbit", method, url, **kw))
        self.adapters = {"coinone": wrap_spot("coinone", self.coin), "korbit": wrap_spot("digitalx", self.korb)}
        self.payload = {"order_id": "synthetic-order"}
        self.status = 200
        self.error = None
        self.calls = []
        self.bounded_fill_timeout = False

    def http(self, exchange, method, url, **kw):
        if exchange == "coinone":
            params = dict(kw.get("json") or kw.get("params") or {})
            if method == "POST":
                encoded = kw["headers"]["X-COINONE-PAYLOAD"]
                self.assertEqual(json.loads(base64.b64decode(encoded)), params)
                self.assertEqual(kw["headers"]["X-COINONE-SIGNATURE"],
                    hmac.new(b"synthetic-secret", encoded.encode(), hashlib.sha512).hexdigest())
            params = {k: v for k, v in params.items() if k not in {"access_token", "nonce"}}
            body = self.payload if self.status >= 400 else {"result": "success", "error_code": "0", **self.payload}
            if self.bounded_fill_timeout:
                self.assertIsInstance(kw["timeout"], Timeout)
                self.assertLessEqual(kw["timeout"].total, 5)
            else:
                self.assertEqual(kw["timeout"], self.coin.timeout_seconds)
        else:
            encoded = kw.get("data", kw.get("params", ""))
            params = {k: v[0] for k, v in parse_qs(encoded).items()}
            if "signature" in params:
                self.assertEqual(params["signature"], hmac.new(b"synthetic-secret",
                    encoded.rsplit("&signature=", 1)[0].encode(), hashlib.sha256).hexdigest())
            params = {k: v for k, v in params.items() if k not in {"signature", "timestamp", "recvWindow"}}
            body = self.payload if self.status >= 400 else {"success": True, "data": self.payload}
            configured = self.korb.timeout_seconds
            if self.bounded_fill_timeout:
                self.assertIsInstance(kw["timeout"], Timeout)
                self.assertLessEqual(kw["timeout"].total, 5)
            elif isinstance(configured, Timeout):
                self.assertLessEqual(kw["timeout"].total, configured.total)
                self.assertLessEqual(kw["timeout"].connect_timeout, configured.connect_timeout)
                self.assertLessEqual(kw["timeout"].read_timeout, configured.read_timeout)
            else:
                self.assertEqual(kw["timeout"], configured)
        self.assertFalse(kw["allow_redirects"])
        timeout = kw["timeout"]
        if isinstance(timeout, Timeout):
            timeout = dict(total=timeout.total, connect=timeout.connect_timeout, read=timeout.read_timeout)
        private = any(k in kw.get("headers", {}) for k in ("X-COINONE-PAYLOAD", "X-KAPI-KEY"))
        entry = dict(exchange=exchange, api_category="private" if private else "public",
                     api_name=self._testMethodName, http_method=method, endpoint=url,
                     request_parameters=params, captured_at=datetime.now(timezone.utc).isoformat(),
                     state="FAILED" if self.error or self.status >= 400 else "HEALTHY",
                     response_or_error=type(self.error).__name__ if self.error else body,
                     timeout_seconds=timeout)
        self.http_evidence.append(entry)
        self.calls.append(entry)
        if self.error:
            raise self.error
        response = requests.Response()
        response.status_code = self.status
        response.headers["Retry-After"] = "2"
        response._content = json.dumps(body).encode()
        return response

    def place(self, exchange="coinone", **kwargs):
        params = dict(ticker="KRW-XRP", side="bid", order_type="price", price="6000.00",
                      identifier="intent-1", limit_price="3000.00")
        params.update(kwargs)
        return self.adapters[exchange].place_order(**params)

    def order(self, exchange="coinone", **kwargs):
        if exchange == "coinone":
            row = dict(order_id="synthetic-order", user_order_id="intent-1", type="MARKET",
                quote_currency="KRW", target_currency="XRP", side="BUY", status="CANCELED_LIMIT_PRICE_EXCEED",
                executed_qty="1.5", original_qty=None, average_executed_price="3000",
                traded_amount="4500", fee="2.25")
        else:
            row = dict(orderId=123, clientOrderId="intent-1", symbol="xrp_krw", side="buy",
                orderType="limit", status="filled", qty="2", filledQty="1.5", filledAmt="4500",
                avgPrice="3000", price="3000")
        row.update(kwargs)
        self.payload = {"order": row} if exchange == "coinone" else row
        return row


class NativeSpotTests(NativeSpotFixture):
    def test_alias_capabilities_and_no_unbounded_surface(self):
        self.assertEqual(self.adapters["korbit"].exchange, "korbit")
        self.assertFalse(SPOT_CAPABILITIES["coinone"].fok)
        self.assertFalse(SPOT_CAPABILITIES["coinone"].ioc)
        self.assertTrue(SPOT_CAPABILITIES["coinone"].price_limited_market)
        self.assertTrue(SPOT_CAPABILITIES["korbit"].fok)
        self.assertTrue(SPOT_CAPABILITIES["korbit"].ioc)
        for adapter in self.adapters.values():
            self.assertTrue(adapter.capabilities.individual_fills)
            self.assertFalse(adapter.capabilities.order_history)
            self.assertTrue(adapter.capabilities.depth_stream)
            for method in (adapter.list_closed_orders,):
                with self.assertRaises(ValueError):
                    method()
            self.assertFalse(hasattr(adapter, "request"))
            self.assertFalse(hasattr(adapter, "create_coin_withdraw"))
        self.assertEqual(self.calls, [])

    def test_coinone_native_price_limited_buy_sell_and_ack_only(self):
        ack = self.place()
        self.assertEqual(ack["uuid"], "synthetic-order")
        self.assertNotIn("state", ack)
        self.assertNotIn("executed_volume", ack)
        params = self.calls[-1]["request_parameters"]
        self.assertEqual(params, dict(quote_currency="KRW", target_currency="XRP", side="BUY",
            type="MARKET", amount="6000.00", limit_price="3000.00", user_order_id="intent-1", post_only=False))
        self.place(side="ask", order_type="market", price=None, volume="1.5000", identifier="intent-2")
        params = self.calls[-1]["request_parameters"]
        self.assertEqual(params["qty"], "1.5000")
        self.assertEqual(params["side"], "SELL")
        self.assertNotIn("amount", params)

    def test_coinone_limit_and_post_only(self):
        self.place(order_type="limit", volume="2", limit_price=None, time_in_force="post_only")
        self.assertTrue(self.calls[-1]["request_parameters"]["post_only"])

    def test_capability_and_input_guards_do_not_submit(self):
        invalid = [dict(time_in_force=x) for x in ("fok", "ioc", "FOK", "", [], {})]
        invalid += [dict(limit_price=x) for x in (None, "NaN", "Infinity", "0", "-1")]
        invalid += [dict(identifier=x) for x in (None, "", "UPPER", "a"*151, "bad id")]
        invalid += [dict(side="buy"), dict(ticker="USDT-XRP"), dict(order_type="best"),
                    dict(volume="2"), dict(price="-1"), dict(price="NaN"), dict(price=True)]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.place(**kwargs)
        self.assertEqual(self.calls, [])

    def test_korbit_native_tifs_exact_signed_wire(self):
        self.payload = {"orderId": 123}
        for tif, wire in (("fok", "fok"), ("ioc", "ioc"), ("post_only", "po"), (None, None)):
            with self.subTest(tif=tif):
                self.place("korbit", order_type="limit", price="3000.000", volume="2.00000001",
                           time_in_force=tif, limit_price=None)
                params = self.calls[-1]["request_parameters"]
                self.assertEqual(params.get("timeInForce"), wire)
                self.assertEqual(params["price"], "3000.000")
                self.assertEqual(params["qty"], "2.00000001")
                self.assertEqual(params["symbol"], "xrp_krw")
                self.assertEqual(params["clientOrderId"], "intent-1")
                self.assertEqual(self.calls[-1]["http_method"], "POST")
                self.assertTrue(self.calls[-1]["endpoint"].endswith("/v2/orders"))

    def test_korbit_best_and_market_combinations(self):
        self.payload = {"orderId": 123}
        self.place("korbit", order_type="best", time_in_force="fok", limit_price=None)
        self.assertEqual(self.calls[-1]["request_parameters"]["bestNth"], "1")
        self.assertEqual(self.calls[-1]["request_parameters"]["amt"], "6000.00")
        self.place("korbit", limit_price=None, time_in_force="ioc")
        self.assertEqual(self.calls[-1]["request_parameters"]["orderType"], "market")
        count = len(self.calls)
        for kwargs in (dict(time_in_force="fok"), dict(time_in_force="post_only"),
                       dict(order_type="best"), dict(limit_price="3000")):
            with self.assertRaises(ValueError):
                self.place("korbit", **{**dict(limit_price=None), **kwargs})
        self.assertEqual(len(self.calls), count)

    def test_coinone_terminal_partial_cancellations_and_zero_fill(self):
        for state in ("CANCELED", "CANCELED_NO_ORDER", "CANCELED_LIMIT_PRICE_EXCEED", "CANCELED_UNDER_PRODUCT_UNIT", "FILLED"):
            self.order(status=state)
            result = self.adapters["coinone"].get_order("synthetic-order", ticker="KRW-XRP")
            self.assertTrue(result["terminal"])
            self.assertEqual(result["executed_volume"], "1.5")
            self.assertEqual(result["executed_funds"], "4500")
            self.assertIsNone(result["volume"])
            self.assertNotIn("trades", result)
        self.order(status="CANCELED_NO_ORDER", executed_qty="0", traded_amount="0", average_executed_price=None)
        result = self.adapters["coinone"].get_order("synthetic-order")
        self.assertTrue(result["terminal"])
        self.assertEqual(result["executed_volume"], "0")

    def test_coinone_live_partial_canceled_and_unknown_not_terminal(self):
        for state in ("LIVE", "PARTIALLY_FILLED", "PARTIALLY_CANCELED"):
            self.order(status=state)
            self.assertFalse(self.adapters["coinone"].get_order("synthetic-order", ticker="KRW-XRP")["terminal"])
        self.order(status="UNRECOGNIZED")
        with self.assertRaisesRegex(ValueError, "UNKNOWN_ORDER_STATE"):
            self.adapters["coinone"].get_order("synthetic-order")

    def test_korbit_terminal_short_fill_is_never_requested_quantity(self):
        for state in ("filled", "canceled", "partiallyFilledCanceled", "expired"):
            self.order("korbit", status=state)
            result = self.adapters["korbit"].get_order("123", ticker="xrp_krw")
            self.assertTrue(result["terminal"])
            self.assertEqual(result["executed_volume"], "1.5")
            self.assertIsNone(result["paid_fee"])
        for state in ("pending", "open", "partiallyFilled"):
            self.order("korbit", status=state)
            self.assertFalse(self.adapters["korbit"].get_order("123")["terminal"])

    def test_restart_requires_ticker_cached_submit_and_timeout_recovery(self):
        with self.assertRaisesRegex(ValueError, "MARKET_REQUIRED"):
            self.adapters["coinone"].get_order("unknown")
        self.place()
        self.order()
        self.assertEqual(self.adapters["coinone"].get_order("synthetic-order")["market"], "KRW-XRP")
        self.error = requests.Timeout()
        with self.assertRaises(ExchangeRequestError) as caught:
            self.place(identifier="timed-out")
        self.assertTrue(caught.exception.outcome_unknown)
        self.error = None
        self.order(user_order_id="timed-out")
        self.assertEqual(self.adapters["coinone"].get_order(identifier="timed-out")["identifier"], "timed-out")
        self.assertEqual(len(self.calls), 4)

    def test_identity_mismatch_invalid_fills_and_missing_values_fail(self):
        for changes in (dict(order_id="other"), dict(target_currency="BTC"), dict(executed_qty="NaN"),
                        dict(executed_qty="-1"), dict(original_qty="1"), dict(executed_qty=None),
                        dict(executed_qty="0", traded_amount="1"), dict(side="OTHER")):
            with self.subTest(changes=changes):
                self.order(**changes)
                with self.assertRaises(ValueError):
                    self.adapters["coinone"].get_order("synthetic-order", ticker="KRW-XRP")
        self.order(user_order_id="other")
        with self.assertRaisesRegex(ValueError, "CLIENT_ID_MISMATCH"):
            self.adapters["coinone"].get_order(identifier="intent-1", ticker="KRW-XRP")

    def test_coinone_identifier_not_echoed_and_estimated_funds(self):
        row = self.order(type="LIMIT", original_qty="2", traded_amount=None)
        row.pop("user_order_id")
        result = self.adapters["coinone"].get_order(identifier="intent-1", ticker="KRW-XRP")
        self.assertEqual(result["identifier"], "intent-1")
        self.assertTrue(result["executed_funds_estimated"])
        self.assertEqual(result["executed_funds"], "4500.0")

    def test_cancel_is_ack_only_and_identifier_selector(self):
        for exchange in self.adapters:
            self.payload = {}  # DigitalX successful cancel may have no data.
            ack = self.adapters[exchange].cancel_order(identifier="intent-1", ticker="KRW-XRP")
            self.assertTrue(ack["cancel_requested"])
            self.assertNotIn("terminal", ack)
            self.assertNotIn("executed_volume", ack)
        count = len(self.calls)
        with self.assertRaises(ValueError):
            self.adapters["coinone"].cancel_order("id", identifier="intent-1", ticker="KRW-XRP")
        self.assertEqual(len(self.calls), count)

    def test_balances_preserve_available_and_locked(self):
        self.payload = {"balances": [{"currency": "xrp", "available": "1.5", "limit": "2.5"}]}
        self.assertEqual(self.adapters["coinone"].get_accounts(), [{"currency": "XRP", "balance": "1.5", "locked": "2.5"}])
        self.payload = [{"currency": "xrp", "available": "1.5", "balance": "4.0"}]
        self.assertEqual(self.adapters["korbit"].get_accounts(), [{"currency": "XRP", "balance": "1.5", "locked": "2.5"}])
        self.payload[0]["balance"] = "1"
        with self.assertRaises(ValueError):
            self.adapters["korbit"].get_accounts()

    def test_market_list_normalizes_status_and_native_details(self):
        self.payload = {"markets": [dict(quote_currency="KRW", target_currency="XRP", maintenance_status=0, trade_status=2)]}
        result = self.adapters["coinone"].list_trading_pairs(is_details=True)[0]
        self.assertFalse(result["trading_available"])
        self.assertEqual(result["market"], "KRW-XRP")
        self.assertIn("native", result)
        self.payload = [dict(symbol="xrp_krw", baseCurrency="xrp", quoteCurrency="krw", status="launched")]
        result = self.adapters["korbit"].list_trading_pairs()[0]
        self.assertTrue(result["trading_available"])
        self.assertEqual(self.calls[-1]["http_method"], "GET")
        self.assertEqual(self.calls[-1]["request_parameters"], {})

    def book(self):
        self.payload = dict(quote_currency="KRW", target_currency="XRP", symbol="xrp_krw", timestamp=1700000000000,
            asks=[dict(price="3002", qty="4"), dict(price="3001", qty="2")], bids=[dict(price="3000", qty="3")])

    def test_public_books_sorted_validated_and_batch_is_bounded(self):
        for exchange in self.adapters:
            self.book()
            result = self.adapters[exchange].get_orderbook_parse("xrp-krw", count=1)
            self.assertEqual(result["asks"], [dict(price=3001., qty=2.)])
            self.assertEqual(result["ticker"], "xrp")
            result = self.adapters[exchange].get_orderbooks(["KRW-XRP"])[0]
            self.assertEqual(result["market"], "KRW-XRP")
            self.assertEqual(result["orderbook_units"][1]["bid_size"], 0)
            count = len(self.calls)
            for args in ([], ["KRW-XRP"] * 11, ["KRW-XRP", "BTC-XRP"]):
                with self.assertRaises(ValueError):
                    self.adapters[exchange].get_orderbooks(args)
            self.assertEqual(len(self.calls), count)
            self.payload["asks"][0]["price"] = "NaN"
            with self.assertRaises(ValueError):
                self.adapters[exchange].get_orderbook_parse("KRW-XRP", count=1)

    def test_runner_depth_twenty_caps_native_request_without_fake_liquidity(self):
        for exchange in self.adapters:
            self.book()
            length = 16 if exchange == "coinone" else 25
            self.payload["asks"] = [dict(price=str(3001 + i), qty="1") for i in range(length)]
            self.payload["bids"] = [dict(price=str(3000 - i), qty="2") for i in range(3)]
            result = self.adapters[exchange].get_orderbook_parse("KRW-XRP", count=20)
            self.assertEqual(len(result["asks"]), min(length, 20))
            self.assertEqual(len(result["bids"]), 3)
            self.assertEqual(len({r["price"] for r in result["asks"]}), min(length, 20))
            params = self.calls[-1]["request_parameters"]
            if exchange == "coinone":
                self.assertEqual(params["size"], 16)
            else:
                self.assertEqual(params, {"symbol": "xrp_krw"})
            self.book()
            result = self.adapters[exchange].get_orderbook_parse("KRW-XRP", count=1000000)
            self.assertEqual((len(result["asks"]), len(result["bids"])), (2, 1))

    def test_invalid_native_depth_does_not_send(self):
        for exchange in self.adapters:
            for count in (0, -1, True, 1.5, "20", None):
                with self.assertRaises(ValueError):
                    self.adapters[exchange].get_orderbook_parse("KRW-XRP", count=count)
        self.assertEqual(self.calls, [])

    def test_error_metadata_timeout_and_no_mutation_retries(self):
        for exchange, raw in (("coinone", self.coin), ("korbit", self.korb)):
            before = len(self.calls)
            self.error = requests.Timeout()
            with self.assertRaises(ExchangeRequestError) as caught:
                self.place(exchange, **({"order_type": "limit", "volume": "2", "limit_price": None} if exchange == "korbit" else {}))
            self.assertTrue(caught.exception.outcome_unknown)
            self.assertEqual(len(self.calls), before + 1)
            self.error = None
            self.status, self.payload = 429, {"error": {"message": "RATE_LIMITED"}}
            with self.assertRaises(ExchangeRequestError) as caught:
                self.adapters[exchange].get_order("123", ticker="KRW-XRP")
            self.assertEqual(caught.exception.retry_after_seconds, 2)
            self.status = 200
            raw._session.mount("https://", HTTPAdapter(max_retries=2))
            count = len(self.calls)
            with self.assertRaises(Exception):
                self.place(exchange, **({"order_type": "limit", "volume": "2", "limit_price": None} if exchange == "korbit" else {}))
            self.assertEqual(len(self.calls), count)

    def test_timeout_facade_reaches_transport_without_losing_total(self):
        self.assertEqual(self.adapters["korbit"].timeout_seconds, (3, 3))
        for exchange in self.adapters:
            for timeout in (1.25, (3, 5), Timeout(total=5, connect=2, read=3)):
                self.adapters[exchange].timeout_seconds = timeout
                self.order(exchange)
                self.adapters[exchange].get_order("synthetic-order" if exchange == "coinone" else "123", ticker="KRW-XRP")
                sent = self.calls[-1]["timeout_seconds"]
                if isinstance(timeout, Timeout):
                    self.assertLessEqual(sent["total"], 5)
                    self.assertGreater(sent["total"], 0)
                    self.assertLessEqual(sent["connect"], 2)
                    self.assertLessEqual(sent["read"], 3)
                else:
                    self.assertEqual(sent, timeout)
            for invalid in (None, 0, True, float("inf"), (-1, 3), (1, None), Timeout()):
                with self.assertRaises(ValueError):
                    self.adapters[exchange].timeout_seconds = invalid

    def test_digitalx_signing_time_consumes_total_before_mutation(self):
        self.adapters["korbit"].timeout_seconds = Timeout(total=5, connect=2, read=3)
        self.payload = {"orderId": 123}
        with patch.object(self.korb, "signed_params", return_value="symbol=xrp_krw"), \
             patch("src.exchanges.korbit.korbit_rest.time", Mock(monotonic=Mock(side_effect=[100, 102]))):
            self.place("korbit", order_type="limit", volume="2", limit_price=None)
        self.assertEqual(self.calls[-1]["timeout_seconds"]["total"], 3)
        count = len(self.calls)
        with patch.object(self.korb, "signed_params", return_value="symbol=xrp_krw"), \
             patch("src.exchanges.korbit.korbit_rest.time", Mock(monotonic=Mock(side_effect=[100, 106]))):
            with self.assertRaises(ExchangeRequestError) as caught:
                self.place("korbit", order_type="limit", volume="2", limit_price=None)
        self.assertFalse(caught.exception.outcome_unknown)
        self.assertEqual(len(self.calls), count)

    def test_best_quotes_one_all_ticker_request_and_no_last_trade_substitution(self):
        for exchange in self.adapters:
            with self.subTest(exchange=exchange):
                if exchange == "coinone":
                    self.payload = {"tickers": [dict(quote_currency="krw", target_currency="xrp",
                        timestamp=1700000000000, last="9999", best_bids=[dict(price="3000", qty="1")],
                        best_asks=[dict(price="3001", qty="2")])]}
                else:
                    self.payload = [dict(symbol="xrp_krw", close="9999", bestBidPrice="3000",
                                         bestAskPrice="3001", lastTradedAt=1700000000000)]
                before = len(self.calls)
                result = self.adapters[exchange].get_best_quotes(["XRP", "KRW-BTC"])
                self.assertEqual(len(self.calls), before + 1)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["orderbook_units"], [dict(bid_price=3000., ask_price=3001.)])
                self.assertEqual("timestamp" in result[0], exchange == "coinone")
                self.assertNotIn("bid_size", result[0]["orderbook_units"][0])
                self.assertEqual(self.calls[-1]["request_parameters"], {})
                before = len(self.calls)
                self.adapters[exchange].get_best_quotes(["KRW-XRP"] * 599)
                self.assertEqual(len(self.calls), before + 1)
                before = len(self.calls)
                for tickers in ([], ["KRW-XRP"] * 600, ["KRW-XRP", "BTC-XRP"]):
                    with self.assertRaises(ValueError):
                        self.adapters[exchange].get_best_quotes(tickers)
                self.assertEqual(len(self.calls), before)

    def test_best_quotes_bad_requested_quote_fails_not_last_price(self):
        self.payload = [dict(symbol="xrp_krw", close="3000", bestBidPrice="NaN", bestAskPrice="3001")]
        with self.assertRaises(ValueError):
            self.adapters["korbit"].get_best_quotes(["KRW-XRP"])

    def test_policy_reuses_library_rules_and_overlays_digitalx_order_bounds(self):
        from src.exchanges.spot_trading import Policy
        for exchange in self.adapters:
            native = self.adapters[exchange]._native
            policy = Policy((("0", "1"),), "0", "0", "0.001")
            with patch.object(native.spot, "policy", return_value=policy) as read:
                self.payload = [dict(symbol="xrp_krw", baseCurrency="xrp", quoteCurrency="krw",
                    status="launched", minOrderValue="5000", maxOrderValue="1000000")]
                result = self.adapters[exchange].get_trading_policy("KRW-XRP")
                read.assert_called_once_with("KRW-XRP")
                self.assertEqual(result.quantity_step, "0.00000001")
                if exchange == "korbit":
                    self.assertEqual(result.min_buy, "5000")
                    self.assertEqual(result.max_total, "1000000")
                else:
                    self.assertIs(result, policy)

    def test_explicit_id_list_never_swallows_missing_or_unsupported_scan(self):
        self.order()
        self.assertEqual(len(self.adapters["coinone"].list_orders(uuids=["synthetic-order"], ticker="KRW-XRP")), 1)
        count = len(self.calls)
        for kwargs in ({"ticker": "KRW-XRP"}, {"uuids": ["id"] * 11}, {"identifiers": ["bad id"]}):
            with self.assertRaises(ValueError):
                self.adapters["coinone"].list_orders(**kwargs)
        self.assertEqual(len(self.calls), count)

    def test_missing_ack_is_typed_unknown_not_safe_to_resubmit(self):
        self.payload = {}
        with self.assertRaises(ExchangeRequestError) as caught:
            self.place()
        self.assertTrue(caught.exception.outcome_unknown)
        self.assertEqual(len(self.calls), 1)

    def test_cancel_timeout_remains_unknown_and_does_not_retry(self):
        self.error = requests.Timeout()
        for exchange in self.adapters:
            before = len(self.calls)
            with self.assertRaises(ExchangeRequestError) as caught:
                self.adapters[exchange].cancel_order(identifier="intent-1", ticker="KRW-XRP")
            self.assertTrue(caught.exception.outcome_unknown)
            self.assertEqual(len(self.calls), before + 1)

    def test_coinone_lowercase_book_order_markets_and_balances(self):
        self.book()
        self.payload.update(quote_currency="krw", target_currency="xrp")
        self.assertEqual(self.adapters["coinone"].get_orderbook_parse("KRW-XRP")["market"], "KRW-XRP")
        self.order(quote_currency="krw", target_currency="xrp")
        self.assertTrue(self.adapters["coinone"].get_order("synthetic-order", ticker="KRW-XRP")["terminal"])
        self.payload = {"markets": [dict(quote_currency="krw", target_currency="xrp", maintenance_status=0, trade_status=1)]}
        self.assertEqual(self.adapters["coinone"].list_trading_pairs()[0]["market"], "KRW-XRP")

    def test_wallet_states_preserve_coinone_unknown_network_and_digitalx_routes(self):
        self.payload = {"currencies": [dict(symbol="xrp", deposit_status="normal", withdraw_status="suspended")]}
        self.assertEqual(self.adapters["coinone"].list_wallet_statuses(), [dict(currency="XRP", wallet_state="deposit_only")])
        self.payload = [dict(name="krw", depositStatus="launched", withdrawalStatus="launched", networkList=None),
            dict(name="xrp", depositStatus="stopped", withdrawalStatus="stopped", networkList=[
                dict(name="XRP", depositStatus="stopped", withdrawalStatus="launched")])]
        result = self.adapters["korbit"].list_wallet_statuses()
        self.assertEqual(result, [dict(currency="KRW", wallet_state="working"),
                                 dict(currency="XRP", net_type="XRP", wallet_state="withdraw_only")])
        self.payload[1]["networkList"] = None
        with self.assertRaises(ValueError):
            self.adapters["korbit"].list_wallet_statuses()

    def test_coinone_global_open_uses_documented_active_endpoint_not_obsolete_alias(self):
        row = self.order(type="LIMIT", quote_currency="krw", target_currency="xrp", original_qty="2", remain_qty="0.5")
        row.pop("status")
        self.payload = {"active_orders": [row]}
        result = self.adapters["coinone"].list_open_orders(limit=1)
        self.assertEqual(result[0]["market"], "KRW-XRP")
        self.assertFalse(result[0]["terminal"])
        self.assertEqual(self.calls[-1]["request_parameters"], {})
        self.assertTrue(self.calls[-1]["endpoint"].endswith("/v2.1/order/active_orders"))
        self.payload = {"active_orders": []}
        self.assertEqual(self.adapters["coinone"].list_open_orders(limit=1), [])
        self.payload = {}
        with self.assertRaises(KeyError):
            self.adapters["coinone"].list_open_orders(limit=1)

    def test_digitalx_open_requires_market_and_binds_missing_response_symbol(self):
        with self.assertRaisesRegex(ValueError, "REQUIRE_TICKER"):
            self.adapters["korbit"].list_open_orders(limit=1)
        self.assertEqual(self.calls, [])
        row = self.order("korbit", status="partiallyFilled")
        row.pop("symbol")
        self.payload = [row]
        result = self.adapters["korbit"].list_open_orders(ticker="KRW-XRP", limit=1)
        self.assertEqual(result[0]["executed_volume"], "1.5")
        self.assertFalse(result[0]["terminal"])

    def test_history_probe_never_labels_coinone_fills_closed(self):
        self.payload = {"completed_orders": [dict(quote_currency="krw", target_currency="xrp", trade_id="fake-trade", order_id="fake-order")]}
        rows = self.adapters["coinone"].list_order_history_probe(ticker="KRW-XRP", limit=1)
        self.assertNotIn("terminal", rows[0])
        self.assertNotIn("state", rows[0])
        params = self.calls[-1]["request_parameters"]
        self.assertEqual(params["to_ts"] - params["from_ts"], 86400000)
        with self.assertRaises(ValueError):
            self.adapters["coinone"].list_closed_orders(ticker="KRW-XRP", limit=1)


if __name__ == "__main__":
    unittest.main()
