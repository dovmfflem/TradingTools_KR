"""Offline protocol fixtures for four-venue OrderID reconciliation."""
import unittest
from unittest.mock import Mock

from src.exchanges.spot_trading import ADAPTERS, Order
from src.exchanges.api_error import ExchangeRequestError
from src.exchanges.bithumb.bithumb_rest import BithumbRest, BithumbRestError


class RecoveryContractTests(unittest.TestCase):
    def setup_venue(self, exchange):
        client = Mock()
        for name in ("list_orders_by_ids", "list_closed_orders", "list_orders", "get_open_orders", "list_all_orders", "get_my_trades"):
            getattr(client, name).return_value = []
        client.list_active_orders.return_value = {"active_orders": []}
        client.list_completed_orders.return_value = {"completed_orders": []}
        adapter = ADAPTERS[exchange](client)
        code = {"coinone": "104", "korbit": "ORDER_NOT_FOUND"}.get(exchange, "order_not_found")
        adapter.lookup = Mock(side_effect=ExchangeRequestError(exchange, code, status_code=200 if exchange == "coinone" else 404))
        probe = {"submittedAt": 1_790_000_000, "wallTime": 1_790_000_030}
        return adapter, client, probe

    def advance(self, adapter, probe):
        return adapter.recover_order("KRW-BTC", "123", "client-123", "buy", "1", probe)

    def drain(self, adapter, probe):
        for _ in range(16):
            before = len(adapter.client.mock_calls) + adapter.lookup.call_count
            result, complete = self.advance(adapter, probe)
            self.assertEqual(len(adapter.client.mock_calls) + adapter.lookup.call_count - before, 1)
            if complete:
                return result
        self.fail("recovery did not terminate within fixture budget")

    def wire(self, exchange, state="OPEN", filled="0"):
        if exchange in {"upbit", "bithumb"}:
            return {"uuid": "123", "market": "KRW-BTC", "side": "bid", "volume": "1",
                    "executed_volume": filled, "state": {"OPEN": "wait", "FILLED": "done", "CANCELED": "cancel"}[state], "paid_fee": "0"}
        if exchange == "korbit":
            return {"orderId": 123, "symbol": "btc_krw", "side": "buy", "qty": "1", "filledQty": filled,
                    "status": {"OPEN": "open", "FILLED": "filled", "CANCELED": "canceled"}[state]}
        return {"order_id": "123", "quote_currency": "KRW", "target_currency": "BTC", "side": "BUY",
                "original_qty": "1", "executed_qty": "0", "remain_qty": "1"}

    def test_complete_absence_requires_final_detail_and_order_id_only(self):
        for exchange in ADAPTERS:
            with self.subTest(exchange=exchange):
                adapter, client, probe = self.setup_venue(exchange)
                self.assertIsNone(self.drain(adapter, probe))
                adapter.lookup.assert_called_once_with("KRW-BTC", order_id="123")
                self.assertNotIn("client-123", repr(client.mock_calls))
                if exchange == "bithumb":
                    self.assertEqual([c.kwargs["state"] for c in client.list_orders.call_args_list], ["wait", "done", "cancel"])

    def test_no_order_id_is_rejected_without_network_call(self):
        for exchange in ADAPTERS:
            adapter, client, probe = self.setup_venue(exchange)
            with self.assertRaisesRegex(ValueError, "ORDER_ID_REQUIRED"):
                adapter.recover_order("KRW-BTC", None, "client", "buy", "1", probe)
            self.assertEqual(client.mock_calls, [])

    def test_active_order_is_normalized_for_each_venue(self):
        methods = {"upbit": "list_orders_by_ids", "bithumb": "list_orders", "coinone": "list_active_orders", "korbit": "get_open_orders"}
        for exchange, method in methods.items():
            with self.subTest(exchange=exchange):
                adapter, client, probe = self.setup_venue(exchange)
                row = self.wire(exchange)
                if exchange == "korbit":
                    row.pop("symbol")  # documented openOrders schema
                getattr(client, method).return_value = {"active_orders": [row]} if exchange == "coinone" else [row]
                result, complete = self.advance(adapter, probe)
                self.assertEqual((result.id, result.status, complete), ("123", "OPEN", True))
                adapter.lookup.assert_not_called()

    def test_closed_fills_and_cancels_from_order_history(self):
        for exchange in ("upbit", "bithumb", "korbit"):
            for status in ("FILLED", "CANCELED"):
                with self.subTest(exchange=exchange, status=status):
                    adapter, client, probe = self.setup_venue(exchange)
                    self.advance(adapter, probe)
                    method = {"upbit": "list_closed_orders", "bithumb": "list_orders", "korbit": "list_all_orders"}[exchange]
                    getattr(client, method).return_value = [self.wire(exchange, status, "1" if status == "FILLED" else "0")]
                    result, complete = self.advance(adapter, probe)
                    self.assertEqual((result.status, result.id, complete), (status, "123", True))

    def test_coinone_and_digitalx_fill_history_recovers_full_fill(self):
        for exchange in ("coinone", "korbit"):
            with self.subTest(exchange=exchange):
                adapter, client, probe = self.setup_venue(exchange)
                if exchange == "coinone":
                    client.list_completed_orders.return_value = {"completed_orders": [{"trade_id": "t1", "order_id": "123",
                        "quote_currency": "KRW", "target_currency": "BTC", "is_ask": False, "price": "10000", "qty": "1", "timestamp": 1_790_000_010_000}]}
                else:
                    client.get_my_trades.return_value = [{"tradeId": 1, "orderId": 123, "symbol": "btc_krw", "side": "buy",
                        "price": "10000", "qty": "1", "tradedAt": 1_790_000_010_000}]
                result = self.drain(adapter, probe)
                self.assertEqual((result.status, result.filled, result.filled_at), ("FILLED", "1", 1_790_000_010))
                adapter.lookup.assert_not_called()

    def test_history_timeout_or_permission_error_is_never_absence(self):
        for exchange in ADAPTERS:
            for error in (TimeoutError(), ExchangeRequestError(exchange, "FORBIDDEN", status_code=403)):
                adapter, client, probe = self.setup_venue(exchange)
                probe["stage"] = "final"
                adapter.lookup.side_effect = error
                with self.assertRaises(type(error)):
                    self.advance(adapter, probe)

    def test_malformed_lists_are_not_empty_lists(self):
        methods = {"upbit": "list_orders_by_ids", "bithumb": "list_orders", "coinone": "list_active_orders", "korbit": "get_open_orders"}
        for exchange, method in methods.items():
            adapter, client, probe = self.setup_venue(exchange)
            getattr(client, method).return_value = {"active_orders": [None]} if exchange == "coinone" else [None]
            with self.assertRaises(ValueError):
                self.advance(adapter, probe)

    def test_bithumb_rest_preserves_invalid_list_failure(self):
        client = BithumbRest("fixture", "fixture")
        self.addCleanup(client._session.close)
        for body in ({}, {"error": "invalid"}, [None], {"data": [None]}):
            client._request = Mock(return_value=body)
            with self.assertRaises(BithumbRestError):
                client.list_orders(uuids=["123"], state="cancel")

    def test_digitalx_history_outside_36_hours_is_not_absence(self):
        adapter, client, probe = self.setup_venue("korbit")
        probe["submittedAt"] -= 37 * 3600
        self.advance(adapter, probe)
        with self.assertRaisesRegex(ValueError, "COVERAGE_INCOMPLETE"):
            self.advance(adapter, probe)
        client.list_all_orders.assert_not_called()

    def test_upbit_full_page_splits_creation_window(self):
        adapter, client, probe = self.setup_venue("upbit")
        self.advance(adapter, probe)
        client.list_closed_orders.return_value = [dict(self.wire("upbit", "CANCELED"), uuid=f"other-{i}") for i in range(1000)]
        result, complete = self.advance(adapter, probe)
        self.assertIsNone(result)
        self.assertFalse(complete)
        self.assertEqual(len(probe["windows"]), 2)

    def test_digitalx_full_history_page_splits_without_false_absence(self):
        adapter, client, probe = self.setup_venue("korbit")
        self.advance(adapter, probe)
        client.list_all_orders.return_value = [{"orderId": i + 1000} for i in range(1000)]
        result, complete = self.advance(adapter, probe)
        self.assertEqual((result, complete), (None, False))
        self.assertEqual(len(probe["windows"]), 2)
        probe["windows"] = [(1000, 1001)]
        with self.assertRaisesRegex(ValueError, "TRUNCATED"):
            self.advance(adapter, probe)

    def test_final_detail_found_wins_over_empty_history(self):
        for exchange in ADAPTERS:
            adapter, client, probe = self.setup_venue(exchange)
            adapter.lookup.side_effect = None
            adapter.lookup.return_value = Order("123", "KRW-BTC", "buy", "1", "1", "FILLED")
            self.assertEqual(self.drain(adapter, probe).status, "FILLED")
