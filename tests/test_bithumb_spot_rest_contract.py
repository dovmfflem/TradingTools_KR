"""Bithumb spot adapter through the real REST encoder; mock HTTP only."""
import json
import unittest
from unittest.mock import Mock
import requests
from src.exchanges.bithumb.bithumb_rest import BithumbRest
from src.exchanges.spot_trading import BithumbSpot


class BithumbSpotRestTests(unittest.TestCase):
    def setUp(self):
        self.client = BithumbRest("fixture-key", "fixture-secret", timeout_seconds=3)
        self.addCleanup(self.client._session.close)
        self.adapter = BithumbSpot(self.client)
        self.calls = []
        self.client._session.request = Mock(side_effect=self.http)

    def http(self, **request):
        self.calls.append(request)
        self.assertEqual(request["timeout"], 3)
        self.assertFalse(request["allow_redirects"])
        path = request["url"].removeprefix("https://api.bithumb.com")
        body, params = request["json"] or {}, request["params"] or {}
        if path == "/v1/orders/chance":
            self.assertEqual(params["market"], "KRW-USDT")
            data = {"bid_fee": "0.0004", "ask_fee": "0.0004", "market": {
                "state": "active", "bid": {"min_total": "5000"}, "ask": {"min_total": "5000"}}}
        elif path == "/v2/orders":
            self.assertEqual(body["market"], "KRW-USDT")
            self.assertEqual(body["client_order_id"], "intent-1")
            self.assertEqual((body["order_type"], body["price"], body["volume"]), ("limit", "1360", "5"))
            data = {"order_id": "order-1", "client_order_id": "intent-1"}
        elif path == "/v1/order":
            self.assertTrue(params.get("uuid") == "order-1" or params.get("client_order_id") == "intent-1")
            data = {"uuid": "order-1", "client_order_id": "intent-1", "market": "KRW-USDT", "side": "bid",
                    "volume": "5", "executed_volume": "0", "state": "wait"}
        elif path == "/v2/order":
            self.assertEqual(params["order_id"], "order-1")
            data = {"order_id": "order-1"}
        else:
            self.fail(path)
        response = requests.Response()
        response.status_code = 201 if request["method"] == "POST" else 200
        response._content = json.dumps(data).encode()
        return response

    def test_policy_sends_canonical_market_over_http(self):
        self.adapter.policy("KRW-USDT").validate("buy", "1360", "5")

    def test_submit_lookup_and_cancel_use_real_rest_contract(self):
        self.assertEqual(self.adapter.submit("KRW-USDT", "buy", "1360", "5", "intent-1"), "order-1")
        self.assertEqual(self.adapter.lookup("KRW-USDT", order_id="order-1").id, "order-1")
        self.assertEqual(self.adapter.lookup("KRW-USDT", client_id="intent-1").client_id, "intent-1")
        self.adapter.cancel("KRW-USDT", "order-1")
        self.assertEqual([c["method"] for c in self.calls], ["POST", "GET", "GET", "DELETE"])

    def test_v2_order_id_is_acknowledged_without_uuid(self):
        client = Mock()
        client.place_order.return_value = {"order_id": "v2-order"}
        self.assertEqual(BithumbSpot(client).submit("KRW-USDT", "sell", "1360", "5", "intent-1"), "v2-order")
