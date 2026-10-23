"""Synthetic REST contract tests; no credentials or live exchange calls.

Response shapes follow the official v2 create/cancel and v1 lookup/list docs.
HTTP evidence excludes headers/JWTs and is saved automatically after the run.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import jwt
import requests
from requests.adapters import HTTPAdapter

from src.exchanges.api_error import ExchangeRequestError
from src.exchanges.arbitrage_spot import ArbitrageSpotAdapter, SPOT_CAPABILITIES, wrap_spot
from src.exchanges.bithumb.bithumb_rest import BithumbRest
from src.exchanges.upbit.upbit_rest import UpbitRest


class ArbitrageSpotTests(unittest.TestCase):
    evidence = []

    @classmethod
    def tearDownClass(cls):
        root = Path(__file__).parent / "results" / "arbitrage-spot"
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        (root / f"http-{stamp}.json").write_text(json.dumps({
            "transport": "synthetic HTTP only", "requests": cls.evidence,
        }, indent=2), encoding="utf-8")

    def setUp(self):
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.raw = BithumbRest("synthetic-key", "synthetic-secret-32-characters-long", timeout_seconds=3)
        self.upbit = UpbitRest("synthetic-key", "synthetic-secret-32-characters-long", timeout_seconds=4)
        self.addCleanup(self.raw._session.close)
        self.addCleanup(self.upbit._session.close)
        self.client = wrap_spot("bithumb", self.raw)
        self.payload = {"order_id": "synthetic-order", "market": "KRW-XRP", "side": "bid",
                        "order_type": "limit", "time_in_force": "fok",
                        "created_at": "2026-10-22T00:00:00+09:00", "stp_type": "cancel_taker"}
        self.status = 200
        self.transport_error = None
        self.invalid_json = False
        self.raw._session.request = Mock(side_effect=lambda **kw: self.http("bithumb", **kw))
        self.upbit._session.request = Mock(side_effect=lambda **kw: self.http("upbit", **kw))

    def http(self, exchange, **request):
        path = request["url"].split(".com", 1)[-1]
        self.evidence.append({
            "exchange": exchange, "api_category": "public" if path in {"/v1/orderbook", "/v1/market/all"} else "private",
            "api_name": self._testMethodName, "http_method": request["method"], "endpoint": path,
            "request_parameters": {"params": request.get("params"), "json": request.get("json")},
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "state": "FAILED" if self.transport_error or self.invalid_json or self.status >= 300 else "HEALTHY",
            "response_or_error": type(self.transport_error).__name__ if self.transport_error else
                ("INVALID_JSON" if self.invalid_json else self.payload),
            "status_code": None if self.transport_error else self.status,
            "timeout_seconds": request["timeout"], "allow_redirects": request["allow_redirects"],
        })
        self.assertFalse(request["allow_redirects"])
        if self.transport_error:
            raise self.transport_error
        response = requests.Response()
        response.status_code = self.status
        response.headers.update({"Retry-After": "2", "Remaining-Req": "group=order; sec=0"})
        response._content = b"not-json" if self.invalid_json else json.dumps(self.payload).encode()
        return response

    def place(self, **overrides):
        values = dict(ticker="KRW-XRP", side="bid", order_type="limit", price="3000.00",
                      volume="2.000", identifier="Intent_A-1", time_in_force="fok")
        values.update(overrides)
        return self.client.place_order(**values)

    def test_factory_preserves_upbit_identity_and_rejects_other_exchanges(self):
        self.assertIs(wrap_spot(" UPBIT ", self.upbit), self.upbit)
        self.assertIsInstance(wrap_spot(" BITHUMB ", self.raw), ArbitrageSpotAdapter)
        with self.assertRaises(ValueError):
            wrap_spot("unsupported", self.raw)
        self.raw._session.request.assert_not_called()
        self.upbit._session.request.assert_not_called()

    def test_explicit_capabilities_and_no_private_forwarding(self):
        for exchange in ("upbit", "bithumb"):
            caps = SPOT_CAPABILITIES[exchange]
            self.assertTrue(caps.fok and caps.ioc and caps.post_only and caps.identifiers)
            self.assertTrue(caps.wallets and caps.batch_orderbooks)
        self.assertEqual(self.client.capabilities.time_in_force_quotes, ("KRW",))
        self.assertEqual(self.client.capabilities.identifier_max_length, 36)
        with self.assertRaises(TypeError):
            SPOT_CAPABILITIES["other"] = self.client.capabilities
        for name in ("create_coin_withdraw", "create_krw_withdraw", "create_twap_order",
                     "get_pocket", "place_orders", "cancel_all_orders", "_request", "_public_request"):
            self.assertFalse(hasattr(self.client, name), name)

    def test_timeout_origin_metadata_and_close_delegate(self):
        self.assertEqual(self.client.api_url, "https://api.bithumb.com")
        self.assertNotEqual(self.client.api_url, wrap_spot("upbit", self.upbit).api_url)
        self.client.timeout_seconds = 1.25
        self.place()
        self.assertEqual(self.raw.timeout_seconds, 1.25)
        self.assertEqual(self.raw._session.request.call_args.kwargs["timeout"], 1.25)
        self.assertEqual(self.client.last_response_metadata["retryAfterSeconds"], 2)
        with patch.object(self.raw._session, "close") as close:
            self.client.close()
            close.assert_called_once_with()
        explicit = Mock(spec=["close"])
        wrap_spot("bithumb", explicit).close()
        explicit.close.assert_called_once_with()

    def test_v2_ack_aliases_preserve_raw_and_do_not_invent_fill_state(self):
        self.payload["client_order_id"] = "Intent_A-1"
        result = self.place()
        self.assertEqual(result["uuid"], "synthetic-order")
        self.assertEqual(result["identifier"], "Intent_A-1")
        self.assertEqual(result["ord_type"], "limit")
        self.assertTrue(self.payload.items() <= result.items())
        self.assertNotIn("uuid", self.payload)
        for key in ("state", "executed_volume", "price", "volume"):
            self.assertNotIn(key, result)

    def test_all_limit_tifs_are_encoded_and_signed_without_identifier_renaming_loss(self):
        for tif in (None, "ioc", "fok", "post_only"):
            with self.subTest(tif=tif):
                self.place(time_in_force=tif)
                request = self.raw._session.request.call_args.kwargs
                self.assertEqual((request["method"], request["url"]), ("POST", "https://api.bithumb.com/v2/orders"))
                body = request["json"]
                self.assertEqual(body["market"], "KRW-XRP")
                self.assertEqual(body["client_order_id"], "Intent_A-1")
                self.assertEqual((body["price"], body["volume"]), ("3000.00", "2.000"))
                self.assertNotIn("identifier", body)
                self.assertNotIn("ord_type", body)
                self.assertEqual(body.get("time_in_force"), tif)
                if tif is None:
                    self.assertNotIn("time_in_force", body)
                token = request["headers"]["Authorization"].split()[1]
                claims = jwt.decode(token, self.raw.secret_key, algorithms=["HS256"])
                expected = hashlib.sha512(self.raw._build_query_string(body).encode()).hexdigest()
                self.assertEqual(claims["query_hash"], expected)

    def test_best_orders_and_market_orders(self):
        for side, kind, price, volume, tif in (
            ("bid", "best", "6000", None, "ioc"), ("bid", "best", "6000", None, "fok"),
            ("ask", "best", None, "2", "ioc"), ("ask", "best", None, "2", "fok"),
            ("bid", "price", "6000", None, None), ("ask", "market", None, "2", None),
        ):
            with self.subTest(side=side, kind=kind, tif=tif):
                self.place(side=side, order_type=kind, price=price, volume=volume, time_in_force=tif)
                body = self.raw._session.request.call_args.kwargs["json"]
                self.assertEqual(body["order_type"], kind)
                self.assertEqual(body.get("time_in_force"), tif)
                self.assertEqual(body.get("price"), price)
                self.assertEqual(body.get("volume"), volume)

    def test_invalid_tif_combinations_never_reach_transport(self):
        invalid = [dict(time_in_force=tif) for tif in ("", "FOK", "gtc", 1, [], {})]
        invalid += [dict(order_type=kind, time_in_force=tif, side=side)
                    for kind, side in (("price", "bid"), ("market", "ask"))
                    for tif in ("ioc", "fok", "post_only")]
        invalid += [dict(order_type="best", time_in_force=tif) for tif in (None, "post_only")]
        invalid += [dict(order_type="best", side="bid", price=None),
                    dict(order_type="best", side="ask", volume=None),
                    dict(ticker="BTC-XRP"), dict(ticker="USDT-XRP"),
                    dict(side="buy"), dict(order_type="unknown"), dict(price=None), dict(volume=None)]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    self.place(**overrides)
        self.raw._session.request.assert_not_called()

    def test_client_identifier_boundaries_and_invalid_characters(self):
        for identifier in (None, "A", "A0_-" * 9):
            self.place(identifier=identifier)
        self.raw._session.request.reset_mock()
        for identifier in ("", "A" * 37, "with space", "dot.id", "slash/id", "a\n", "\u00e9", 5, []):
            for operation in (lambda: self.place(identifier=identifier),
                              lambda: self.client.get_order(identifier=identifier),
                              lambda: self.client.cancel_order(identifier=identifier),
                              lambda: self.client.list_orders(identifiers=[identifier])):
                with self.subTest(identifier=identifier):
                    with self.assertRaises(ValueError):
                        operation()
        self.raw._session.request.assert_not_called()

    def test_lookup_and_cancel_by_uuid_or_identifier(self):
        for operation, method, path, wire_id in ((self.client.get_order, "GET", "/v1/order", "uuid"),
                                                (self.client.cancel_order, "DELETE", "/v2/order", "order_id")):
            for kwargs, expected in (({"uuid": "order-1"}, {wire_id: "order-1"}),
                                     ({"uuid_value": "order-1"}, {wire_id: "order-1"}),
                                     ({"identifier": "Intent_A-1"}, {"client_order_id": "Intent_A-1"}),
                                     ({"uuid": "order-1", "identifier": "Intent_A-1"},
                                      {wire_id: "order-1", "client_order_id": "Intent_A-1"})):
                operation(**kwargs)
                call = self.raw._session.request.call_args.kwargs
                self.assertEqual((call["method"], call["url"], call["params"]),
                                 (method, "https://api.bithumb.com" + path, expected))
            with self.assertRaises(ValueError):
                operation()
            with self.assertRaises(ValueError):
                operation("first", uuid="second")

    def test_lookup_preserves_v1_terminal_states_and_existing_aliases(self):
        for state, executed in (("wait", "0"), ("done", "1"), ("cancel", "0")):
            self.payload = {"uuid": "v1-order", "client_order_id": "Intent_A-1", "ord_type": "limit",
                            "market": "KRW-XRP", "volume": "2", "executed_volume": executed,
                            "state": state, "trades": [{"funds": "3000"}], "cancel_type": "tif_cancel"}
            result = self.client.get_order(identifier="Intent_A-1")
            for key, value in self.payload.items():
                self.assertEqual(result[key], value)
            self.assertEqual(result["identifier"], "Intent_A-1")
        self.payload.update(order_id="other", identifier="existing", order_type="best")
        result = self.client.get_order("v1-order")
        self.assertEqual((result["uuid"], result["identifier"], result["ord_type"]),
                         ("v1-order", "existing", "limit"))

    def test_list_orders_supports_ticker_only_recovery_and_identifier_alias(self):
        self.payload = [{"uuid": "v1-order", "client_order_id": "Intent_A-1", "state": "done"}]
        result = self.client.list_orders(ticker="KRW-XRP")
        self.assertEqual(result[0]["identifier"], "Intent_A-1")
        self.assertEqual(self.raw._session.request.call_args.kwargs["params"], {"market": "KRW-XRP"})
        self.client.list_orders(ticker="BTC-CHR", identifiers=["Intent_A-1"], states=["done", "cancel"],
                                uuids=["v1-order"], limit=10, page=1, order_by="desc")
        params = self.raw._session.request.call_args.kwargs["params"]
        self.assertEqual(params["market"], "BTC-CHR")
        self.assertEqual(params["client_order_ids[]"], ["Intent_A-1"])
        self.assertEqual(params["states[]"], ["done", "cancel"])
        self.payload = {"data": [{"order_id": "v2-order"}]}
        self.assertEqual(self.client.list_orders()[0]["uuid"], "v2-order")
        with self.assertRaises(ValueError):
            self.client.list_orders(identifiers=["one"], client_order_ids=["two"])

    def test_public_books_preserve_krw_and_cross_quote_orientation(self):
        cases = {"KRW-BTC": "KRW-BTC", "btc-krw": "KRW-BTC", "BTC-KRW": "KRW-BTC",
                 "BTC-CHR": "BTC-CHR", "chr-btc": "BTC-CHR", "USDT-IOTX": "USDT-IOTX",
                 "iotx-usdt": "USDT-IOTX", "BTC-USDT": "BTC-USDT", "btc-usdt": "USDT-BTC",
                 "USDT-BTC": "USDT-BTC", "usdt-btc": "BTC-USDT"}
        for ticker, market in cases.items():
            with self.subTest(ticker=ticker):
                self.payload = [{"market": market, "orderbook_units": [
                    {"ask_price": 3001, "bid_price": 3000, "ask_size": 2, "bid_size": 3},
                    {"ask_price": 3002, "bid_price": 2999, "ask_size": 4, "bid_size": 5}]}]
                book = self.client.get_orderbook_parse(ticker, 1)
                self.assertEqual(book["exchange"], "bithumb")
                self.assertEqual(book["ticker"], market.split("-")[1].lower())
                self.assertEqual(book["bids"], [{"price": 3000, "qty": 3}])
                self.assertEqual(len(book["asks"]), 1)
                self.assertEqual(self.raw._session.request.call_args.kwargs["params"], {"markets": market})
                self.assertEqual(self.client.get_orderbooks([ticker]), self.payload)
                self.assertEqual(self.raw._session.request.call_args.kwargs["params"], {"markets": market})
        self.client.get_orderbooks(["KRW-BTC", "BTC-CHR", "USDT-IOTX"])
        self.assertEqual(self.raw._session.request.call_args.kwargs["params"]["markets"], "KRW-BTC,BTC-CHR,USDT-IOTX")

    def test_open_and_closed_reads_use_native_endpoints_and_filters(self):
        self.payload = [{"uuid": "order-1", "client_order_id": "Intent_A-1"}]
        for operation, kwargs, expected in (
            (self.client.list_closed_orders, {"ticker": "KRW-BTC", "limit": 1},
             {"market": "KRW-BTC", "states[]": ["done", "cancel"], "limit": 1}),
            (self.client.list_open_orders, {"ticker": "ETH-krw", "limit": 1},
             {"market": "KRW-ETH", "state": "wait", "limit": 1}),
            (self.client.list_open_orders, {"limit": 1}, {"state": "wait", "limit": 1}),
            (self.client.list_closed_orders, {"state": "done"}, {"state": "done"}),
            (self.client.list_open_orders, {"states": ["wait", "watch"]}, {"states[]": ["wait", "watch"]}),
        ):
            result = operation(**kwargs)
            self.assertEqual(result[0]["identifier"], "Intent_A-1")
            call = self.raw._session.request.call_args.kwargs
            self.assertEqual(call["url"], "https://api.bithumb.com/v1/orders")
            self.assertEqual(call["params"], expected)
        upbit = ArbitrageSpotAdapter("upbit", self.upbit)
        upbit.list_open_orders(ticker="ETH-krw", limit=1)
        self.assertTrue(self.upbit._session.request.call_args.kwargs["url"].endswith("/v1/orders/open"))
        upbit.list_closed_orders(ticker="KRW-BTC", limit=1)
        self.assertTrue(self.upbit._session.request.call_args.kwargs["url"].endswith("/v1/orders/closed"))

    def test_invalid_books_do_not_make_requests(self):
        for tickers in ([], ["KRW-XRP"] * 101, ["KRW-XRP-extra"], ["XRP"], ["foo-bar"]):
            with self.assertRaises(ValueError):
                self.client.get_orderbooks(tickers)
        with self.assertRaises(ValueError):
            self.client.get_orderbook_parse("KRW-XRP", 0)
        self.raw._session.request.assert_not_called()

    def test_accounts_markets_and_wallet_shapes_pass_through(self):
        for operation, path, payload in (
            (self.client.get_accounts, "/v1/accounts", [{"currency": "KRW", "balance": "123", "locked": "4"}]),
            (self.client.list_trading_pairs, "/v1/market/all", [{"market": "KRW-XRP", "english_name": "XRP"}]),
            (self.client.list_wallet_statuses, "/v1/status/wallet", [{"currency": "XRP", "wallet_state": "working"}]),
            (self.client.get_wallet_status, "/v1/status/wallet", [{"currency": "XRP", "wallet_state": "withdraw_only"}]),
        ):
            self.payload = payload
            self.assertEqual(operation(), payload)
            self.assertTrue(self.raw._session.request.call_args.kwargs["url"].endswith(path))

    def test_errors_keep_real_exchange_metadata_and_never_retry(self):
        for status in (401, 404, 429, 500, 503, 302):
            for operation, mutation in ((lambda: self.place(), True),
                                        (lambda: self.client.cancel_order("order-1"), True),
                                        (lambda: self.client.get_order(identifier="Intent_A-1"), False)):
                self.status, self.payload = status, {"error": {"name": "order_not_found"}}
                self.raw._session.request.reset_mock()
                with self.assertRaises(ExchangeRequestError) as caught:
                    operation()
                error = caught.exception
                self.assertEqual(error.exchange, "bithumb")
                self.assertEqual(error.status_code, status)
                self.assertEqual(error.outcome_unknown, mutation and (status >= 500 or status == 302))
                self.assertEqual(self.client.is_order_missing(error), status == 404)
                if status == 429:
                    self.assertEqual((error.code, error.retry_after_seconds), ("RATE_LIMITED", 2))
                self.raw._session.request.assert_called_once()

    def test_transport_timeouts_and_invalid_json_are_not_retried(self):
        for bad_json in (False, True):
            self.invalid_json = bad_json
            self.transport_error = None if bad_json else requests.Timeout("synthetic timeout")
            for operation, mutation in ((lambda: self.place(), True),
                                        (lambda: self.client.cancel_order("order-1"), True),
                                        (lambda: self.client.get_order("order-1"), False)):
                self.raw._session.request.reset_mock()
                with self.assertRaises(ExchangeRequestError) as caught:
                    operation()
                self.assertEqual(caught.exception.exchange, "bithumb")
                self.assertEqual(caught.exception.outcome_unknown, mutation)
                self.assertEqual(caught.exception.code, "INVALID_RESPONSE" if bad_json else "TRANSPORT_FAILED")
                self.raw._session.request.assert_called_once()

    def test_retrying_mutation_transport_is_rejected_before_send(self):
        self.raw._session.mount("https://", HTTPAdapter(max_retries=1))
        for operation in (lambda: self.place(), lambda: self.client.cancel_order("order-1")):
            with self.assertRaisesRegex(ValueError, "retries disabled"):
                operation()
        self.raw._session.request.assert_not_called()

    def test_adapter_does_not_wrap_existing_exchange_errors(self):
        error = ExchangeRequestError("bithumb", "RATE_LIMITED", status_code=429, retry_after_seconds=3)
        with patch.object(self.raw, "place_order", side_effect=error):
            with self.assertRaises(ExchangeRequestError) as caught:
                self.place()
        self.assertIs(caught.exception, error)

    def test_explicit_upbit_wrapper_retains_existing_wire_contract(self):
        adapter = ArbitrageSpotAdapter("upbit", self.upbit)
        self.payload = {"uuid": "upbit-order", "identifier": "Intent_A-1", "ord_type": "limit", "state": "wait"}
        result = adapter.place_order(ticker="KRW-XRP", side="bid", order_type="limit", price="3000",
                                     volume="2", identifier="Intent_A-1", time_in_force="fok")
        self.assertEqual(result, self.payload)
        call = self.upbit._session.request.call_args.kwargs
        self.assertEqual(call["url"], "https://api.upbit.com/v1/orders")
        self.assertEqual(call["json"]["identifier"], "Intent_A-1")
        self.assertEqual(call["json"]["ord_type"], "limit")
        self.assertNotIn("client_order_id", call["json"])
        adapter.get_order(identifier="Intent_A-1")
        self.assertEqual(self.upbit._session.request.call_args.kwargs["params"], {"identifier": "Intent_A-1"})
        adapter.cancel_order("upbit-order")
        self.assertEqual(self.upbit._session.request.call_args.kwargs["params"], {"uuid": "upbit-order"})
        self.payload = [{"uuid": "upbit-order"}]
        self.assertEqual(adapter.list_orders(ticker="KRW-XRP", identifiers=["Intent_A-1"]), self.payload)
        self.assertTrue(self.upbit._session.request.call_args.kwargs["url"].endswith("/v1/orders/uuids"))
        with self.assertRaises(ValueError):
            adapter.list_orders(ticker="KRW-XRP")


if __name__ == "__main__":
    unittest.main()
