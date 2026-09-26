"""Offline completed-fill recovery; all IDs and responses are synthetic."""
import unittest
from unittest.mock import Mock

from src.exchanges.coinone.coinone_rest import CoinoneRest
from src.exchanges.spot_trading import CoinoneSpot


def trade(key="fill-1", qty="5", **changes):
    return {"trade_id": key, "order_id": "order-1", "quote_currency": "KRW",
            "target_currency": "USDT", "is_ask": False, "qty": qty,
            "price": "1350", "timestamp": 1790340034000, **changes}


class CoinoneCompletedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.adapter = CoinoneSpot(self.client)
        self.probe = {"from": 1790300000000, "to": 1790350000000}

    def read(self, rows):
        self.client.list_completed_orders.return_value = {"completed_orders": rows}
        return self.adapter.resolve_completed_order("KRW-USDT", "order-1", "intent-1", "buy", "5", self.probe)

    def test_full_fill_requires_exact_order_and_returns_execution_time(self):
        result, more = self.read([trade(order_id="another"), trade("b", "3"), trade("a", "2")])
        self.assertEqual((result.id, result.client_id, result.filled, result.status), ("order-1", "intent-1", "5", "FILLED"))
        self.assertEqual(result.filled_at, 1790340034)
        self.assertEqual(result.turnover, "6750")
        self.assertFalse(more)

    def test_partial_empty_and_wrong_order_are_not_terminal(self):
        for rows in ([], [trade(qty="2")], [trade(order_id="other")]):
            self.probe = {"from": 1790300000000, "to": 1790350000000}
            self.assertEqual(self.read(rows), (None, False))

    def test_pages_deduplicate_fills_and_forward_cursor(self):
        rows = [trade("a", "2")] + [trade(str(i), order_id="other") for i in range(99)]
        self.assertEqual(self.read(rows), (None, True))
        result, _ = self.read([trade("a", "2"), trade("b", "3")])
        self.assertEqual(result.filled, "5")
        self.assertEqual(self.client.list_completed_orders.call_args.kwargs["to_trade_id"], "98")

    def test_inconsistent_records_fail_without_committing_page(self):
        for rows in ([trade(qty="6")], [trade(is_ask=True)], [trade(target_currency="BTC")],
                     [trade(timestamp=1)], [trade("a", "2"), trade("a", "3")]):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    self.read(rows)
                self.assertNotIn("trades", self.probe)

    def test_missing_exchange_id_never_scans_by_price(self):
        with self.assertRaises(ValueError):
            self.adapter.resolve_completed_order("KRW-USDT", None, "intent-1", "buy", "5", self.probe)
        self.client.list_completed_orders.assert_not_called()

    def test_repeated_cursor_is_rejected(self):
        rows = [trade(str(i), order_id="other") for i in range(100)]
        self.read(rows)
        with self.assertRaisesRegex(ValueError, "CURSOR"):
            self.read(rows)

    def test_rest_forwards_completed_history_cursor(self):
        client = CoinoneRest("fixture", "fixture", timeout_seconds=3)
        self.addCleanup(client._session.close)
        client._request = Mock(return_value={})
        client.list_completed_orders(ticker="USDT-KRW", size=100, from_ts=1, to_ts=2, to_trade_id="cursor")
        client._request.assert_called_once_with("/v2.1/order/completed_orders",
            {"size": 100, "from_ts": 1, "to_ts": 2, "to_trade_id": "cursor", "quote_currency": "KRW", "target_currency": "USDT"})
