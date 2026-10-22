"""Market buys with null original volume and partially executed terminal orders."""
import unittest
import json
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import Mock, create_autospec
from src.exchanges.domestic_spot import DomesticSpot
from src.exchanges.upbit.upbit_rest import UpbitRest
from src.exchanges.bithumb.bithumb_rest import BithumbRest
from src.exchanges.coinone.coinone_rest import CoinoneRest
from src.exchanges.korbit.korbit_rest import KorbitRest
from src.exchanges.public_orderbook import normalize_orderbook


class SpotTests(unittest.TestCase):
    captures = []

    @classmethod
    def tearDownClass(cls):
        target = Path(__file__).parent / "results" / "domestic-arbitrage" / "mock-market-requests.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"mocked": True, "captureTime": datetime.now(timezone.utc).isoformat(), "requests": cls.captures}, indent=2), encoding="utf-8")

    def test_native_market_submission_contract_all_four(self):
        for ex, cls, key in (("upbit", UpbitRest, "uuid"), ("bithumb", BithumbRest, "order_id"), ("coinone", CoinoneRest, "order_id"), ("korbit", KorbitRest, "orderId")):
            with self.subTest(exchange=ex):
                c = create_autospec(cls, instance=True)
                c.place_order.return_value = {key: "mock-order"}
                adapter = DomesticSpot(ex, c)
                self.assertEqual(adapter.submit("KRW-XRP", "buy", "500", "da_123", amount="50000"), "mock-order")
                buy = c.place_order.call_args.kwargs
                self.assertEqual(buy.get("price", buy.get("amount")), "50000")
                self.assertNotIn("volume", buy); self.assertNotIn("qty", buy)
                adapter.submit("KRW-XRP", "sell", "500", "da_124")
                sell = c.place_order.call_args.kwargs
                self.assertEqual(sell.get("volume", sell.get("qty")), "500")
                self.assertNotIn("amount", sell); self.assertNotIn("price", sell)
                self.captures.append(dict(exchange=ex, category="private-trading", api="native-market-order", method="POST",
                    endpoint={"upbit":"/v1/orders","bithumb":"/v2/orders","coinone":"/v2.1/order","korbit":"/v2/orders"}[ex],
                    parameters={"sdkBuy":buy,"sdkSell":sell}, state="PASSED", response={key:"mock-order"}))

    def test_bithumb_market_uses_actual_v2_receipt_then_v1_lookup(self):
        import requests
        c = BithumbRest("mock-access", "mock-secret-32-characters-long", timeout_seconds=3)
        self.addCleanup(c.close)
        calls = []
        def http(**kwargs):
            calls.append(kwargs)
            if kwargs["method"] == "POST":
                self.assertTrue(kwargs["url"].endswith("/v2/orders"))
                self.assertEqual(kwargs["json"]["order_type"], "price")
                result = {"order_id": "mock-order", "client_order_id": "da_123"}
            else:
                self.assertEqual(kwargs["params"]["uuid"], "mock-order")
                result = dict(uuid="mock-order", market="KRW-XRP", side="bid", state="done", volume=None,
                              executed_volume="500", trades=[dict(funds="50000")], paid_fee="25")
            response = requests.Response(); response.status_code = 200
            response._content = json.dumps(result).encode(); return response
        c._session.request = Mock(side_effect=http)
        adapter = DomesticSpot("bithumb", c)
        oid = adapter.submit("KRW-XRP", "buy", "500", "da_123", amount="50000")
        self.assertTrue(adapter.lookup("KRW-XRP", order_id=oid)["terminal"])
        self.assertEqual(len(calls), 2)

    def test_upbit_and_bithumb_null_volume_partial_cancel_exact_fill(self):
        for ex in ("upbit", "bithumb"):
            c = Mock()
            c.get_order.return_value = dict(uuid="mock", market="KRW-XRP", side="bid", state="cancel", volume=None,
                executed_volume="499.99999999", paid_fee="25", trades=[dict(funds="49999.999999")])
            r = DomesticSpot(ex, c).lookup("KRW-XRP", order_id="mock")
            self.assertTrue(r["terminal"]); self.assertEqual(r["filled"], "499.99999999")
            self.assertEqual(r["turnover"], "49999.999999")

    def test_coinone_and_digitalx_terminal_short_fill_and_identity(self):
        c = Mock(); c.get_order.return_value = {"order": dict(order_id="mock", quote_currency="KRW", target_currency="XRP", side="BUY",
            status="CANCELED_UNDER_PRODUCT_UNIT", executed_qty="499.9", average_executed_price="100", fee="25")}
        self.assertTrue(DomesticSpot("coinone", c).lookup("KRW-XRP", order_id="mock")["terminal"])
        c.get_order.return_value = dict(orderId="mock", symbol="xrp_krw", side="buy", status="filled", filledQty="499.9", filledAmt="49990")
        self.assertEqual(DomesticSpot("korbit", c).lookup("KRW-XRP", order_id="mock")["filled"], "499.9")
        with self.assertRaisesRegex(ValueError, "ORDER_ID_MISMATCH"):
            DomesticSpot("korbit", c).lookup("KRW-XRP", order_id="different")

    def test_depth_opt_in_does_not_change_existing_top_contract(self):
        message = dict(code="KRW-XRP", timestamp=1700000000000, orderbook_units=[dict(bid_price=100-i, bid_size=600, ask_price=101+i, ask_size=700) for i in range(15)])
        plain = normalize_orderbook("upbit", message, "KRW-XRP", observed_at=1700000000)
        self.assertNotIn("bids", plain)
        full = normalize_orderbook("upbit", message, "KRW-XRP", observed_at=1700000000, include_depth=True)
        self.assertEqual(len(full["bids"]), 10); self.assertEqual(len(full["asks"]), 10)
        self.assertEqual(full["bids"][0], dict(price="100", quantity="600"))

    def test_unknown_open_orders_response_fails_closed(self):
        c = Mock(); c.list_active_orders.return_value = {"wrong": []}
        with self.assertRaisesRegex(ValueError, "INVALID_OPEN_ORDERS"):
            DomesticSpot("coinone", c).open_orders("KRW-XRP")


if __name__ == "__main__": unittest.main()
