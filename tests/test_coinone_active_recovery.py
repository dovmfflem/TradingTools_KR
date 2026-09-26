"""Read-only Coinone order-list recovery using synthetic responses."""
import unittest
from unittest.mock import Mock
from src.exchanges.coinone.coinone_rest import CoinoneRest
from src.exchanges.spot_trading import CoinoneSpot


class CoinoneActiveRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.adapter = CoinoneSpot(self.client)
        self.row = {"order_id": "order-1", "user_order_id": "intent-1", "quote_currency": "KRW",
            "target_currency": "USDT", "side": "BUY", "original_qty": "5", "executed_qty": "1", "remain_qty": "4"}

    def read(self, rows, order_id="order-1"):
        self.client.list_active_orders.return_value = {"active_orders": rows}
        return self.adapter.resolve_active_order("KRW-USDT", order_id, "intent-1")

    def test_exact_order_or_client_id_can_restore_an_open_order(self):
        for order_id in ("order-1", None):
            result, covered = self.read([self.row], order_id)
            self.assertTrue(covered)
            self.assertEqual((result.id, result.filled, result.status), ("order-1", "1", "OPEN"))

    def test_absence_requires_valid_response_and_a_comparable_identifier(self):
        self.assertEqual(self.read([]), (None, True))
        row = {**self.row, "order_id": "other"}
        row.pop("user_order_id")
        self.assertEqual(self.read([row], None), (None, False))
        for rows in (None, [None], [{}]):
            with self.assertRaises(ValueError):
                self.read(rows)

    def test_mismatched_and_duplicate_identities_are_not_absence(self):
        for rows in ([{**self.row, "user_order_id": "other"}], [{**self.row, "target_currency": "BTC"}],
                     [self.row, self.row], [{**self.row, "remain_qty": "0"}]):
            with self.assertRaises(ValueError):
                self.read(rows)

    def test_rest_preserves_raw_fields_for_recovery_and_existing_normalized_contract(self):
        client = CoinoneRest("fixture", "fixture", timeout_seconds=3)
        self.addCleanup(client._session.close)
        client._request = Mock(return_value={"active_orders": [self.row]})
        self.assertEqual(client.list_active_orders(ticker="USDT-KRW")["active_orders"][0], self.row)
        client._request.assert_called_once_with("/v2.1/order/active_orders", {
            "quote_currency": "KRW", "target_currency": "USDT", "order_type": ["LIMIT", "STOP_LIMIT"]})
        self.assertEqual(client.get_open_orders(ticker="USDT-KRW")[0]["remaining_volume"], "4")
