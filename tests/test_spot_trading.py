"""Offline spot adapter contracts; no credentials or network used."""
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from src.exchanges.spot_trading import (ADAPTERS, SpotAdapter, UpbitSpot, BithumbSpot,
    CoinoneSpot, KorbitSpot, Policy, Order, register_spot_adapter, spot_adapter)


class SpotTradingTests(unittest.TestCase):
    def test_manifest_matches_registered_adapters(self):
        manifest = json.loads((Path(__file__).parents[1] / "spot-adapters.json").read_text(encoding="utf8"))
        self.assertEqual(set(ADAPTERS), {entry["id"] for entry in manifest})

    def test_upbit_bithumb_wire_contracts_and_exact_quantity(self):
        for cls, key in [(UpbitSpot, "identifier"), (BithumbSpot, "client_order_id")]:
            with self.subTest(exchange=cls.exchange):
                client = Mock()
                client.place_order.return_value = {"uuid": "order-1"}
                client.get_order.return_value = {"uuid": "order-1", "market": "KRW-BTC", "side": "bid",
                    "volume": "0.12345678", "executed_volume": "0.12345678", "state": "done", key: "intent-1"}
                adapter = cls(client)
                self.assertEqual(adapter.submit("KRW-BTC", "buy", "10000", "0.12345678", "intent-1"), "order-1")
                client.place_order.assert_called_once_with(ticker="KRW-BTC", side="bid", order_type="limit",
                    price="10000", volume="0.12345678", **{key: "intent-1"})
                order = adapter.lookup("KRW-BTC", client_id="intent-1")
                client.get_order.assert_called_once_with(None, **{key: "intent-1"})
                self.assertEqual((order.status, order.filled), ("FILLED", "0.12345678"))
                adapter.cancel("KRW-BTC", "order-1")
                client.cancel_order.assert_called_once_with("order-1")

    def test_coinone_wire_contract_and_canceled_partial_fill(self):
        client = Mock()
        client.place_order.return_value = {"order_id": "order-1"}
        client.get_order.return_value = {"order": {"order_id": "order-1", "quote_currency": "KRW",
            "target_currency": "BTC", "side": "SELL", "original_qty": "1", "executed_qty": "0.4",
            "status": "CANCELED", "average_executed_price": "10100", "fee": "0.04"}}
        adapter = CoinoneSpot(client)
        self.assertEqual(adapter.submit("KRW-BTC", "sell", "10100", "1", "intent-1"), "order-1")
        client.place_order.assert_called_once_with(ticker="BTC-KRW", side="sell", order_type="limit",
            price="10100", volume="1", user_order_id="intent-1")
        order = adapter.lookup("KRW-BTC", client_id="intent-1")
        client.get_order.assert_called_once_with(ticker="BTC-KRW", user_order_id="intent-1")
        self.assertEqual((order.status, order.filled, order.turnover), ("CANCELED", "0.4", "4040.0"))
        adapter.cancel("KRW-BTC", "order-1")
        client.cancel_order.assert_called_once_with("order-1", ticker="BTC-KRW")

    def test_coinone_dynamic_rules(self):
        client = Mock()
        client.get_market.return_value = {"markets": [{"maintenance_status": 0, "trade_status": 1,
            "min_order_amount": "5000", "max_order_amount": "100000000", "qty_unit": "0.0001",
            "min_qty": "0.0001", "max_qty": "10000"}]}
        client.get_range_units.return_value = {"range_price_units": [{"range_min": "0", "price_unit": "1", "next_range_min": "1000000"}]}
        client.get_trade_fee.return_value = {"fee_rates": [{"maker": "0.0002", "taker": "0.0002"}]}
        policy = CoinoneSpot(client).policy("KRW-BTC")
        policy.validate("buy", "10000", "1")
        with self.assertRaisesRegex(ValueError, "STEP"):
            policy.validate("buy", "10000", "1.00001")

    def test_korbit_wire_contract(self):
        client = Mock()
        client.place_order.return_value = {"orderId": 123}
        client.get_order.return_value = {"orderId": 123, "symbol": "btc_krw", "side": "buy",
            "qty": "1", "filledQty": "0.4", "status": "partiallyFilledCanceled", "clientOrderId": "intent-1"}
        adapter = KorbitSpot(client)
        self.assertEqual(adapter.submit("KRW-BTC", "buy", "10000", "1", "intent-1"), "123")
        client.place_order.assert_called_once_with(symbol="btc_krw", side="buy", order_type="limit",
            price="10000", qty="1", client_order_id="intent-1")
        self.assertEqual(adapter.lookup("KRW-BTC", client_id="intent-1").status, "CANCELED")
        client.get_order.assert_called_once_with("btc_krw", client_order_id="intent-1")
        adapter.cancel("KRW-BTC", "123")
        client.cancel_order.assert_called_once_with(symbol="btc_krw", order_id="123")

    def test_invalid_terminal_and_identity_events_require_reconciliation(self):
        with self.assertRaisesRegex(ValueError, "INCOMPLETE"):
            Order("1", "KRW-BTC", "buy", "1", "0.4", "FILLED")
        adapter = UpbitSpot(Mock())
        self.assertIsNone(adapter.event({"event": "order", "exact": {}}, "1", "buy"))
        with self.assertRaisesRegex(ValueError, "QUANTITY_MISMATCH"):
            adapter.event({"event": "order", "exact": {"cumulativeFilled": "1", "originalVolume": "2"}}, "1", "buy")
        for value in ("NaN", "Infinity", "-1", "0"):
            with self.assertRaises(ValueError):
                Policy((("0", "1"),), "5000", "5000", "0").validate("buy", value, "1")

    def test_extension_requires_no_strategy_exchange_branch(self):
        class ExampleSpot(SpotAdapter):
            exchange = "example"
        try:
            register_spot_adapter("example", ExampleSpot)
            self.assertIsInstance(spot_adapter("example", Mock()), ExampleSpot)
            with self.assertRaises(ValueError):
                register_spot_adapter("example", ExampleSpot)
        finally:
            ADAPTERS.pop("example", None)

    def test_explicit_available_asset_amount_takes_priority_over_total(self):
        from src.exchanges.private_events import normalize_private_asset_event
        result = normalize_private_asset_event("coinone", {"response_type": "DATA", "channel": "MYASSET",
            "data": {"assets": [{"currency": "KRW", "balance": "10000", "available": "1000", "limit": "9000"}]}})
        self.assertEqual(result["assets"][0]["available"], "1000")


if __name__ == "__main__":
    unittest.main()
