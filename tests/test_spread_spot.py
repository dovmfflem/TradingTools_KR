"""Offline spread contracts; runner persists test evidence without credentials."""
import unittest
from unittest.mock import Mock
from src.exchanges.spread_spot import SpreadSpot


class SpreadSpotTests(unittest.TestCase):
    def test_best_level_depth_is_exact_for_every_exchange(self):
        for exchange in ("upbit", "bithumb", "coinone", "korbit"):
            with self.subTest(exchange=exchange):
                client = Mock()
                client.get_orderbook.return_value = {
                    "orderbook_units": [
                        {"bid_price": "1398", "ask_price": "1401", "bid_size": "999999", "ask_size": "999999"},
                        {"bid_price": "1399", "ask_price": "1400", "bid_size": "10000.1", "ask_size": "20000.2"}],
                    "bids": [{"price": "1398", "qty": "999999"}, {"price": "1399", "qty": "10000.1"}],
                    "asks": [{"price": "1401", "qty": "999999"}, {"price": "1400", "qty": "20000.2"}]}
                quote = SpreadSpot(exchange, client).quote("KRW-USDT")
                self.assertEqual((quote["bid_size"], quote["ask_size"]), ("10000.1", "20000.2"))

    def test_negative_depth_rejected_not_treated_as_available(self):
        client = Mock()
        client.get_orderbook.return_value = {"bids": [{"price": "1", "qty": "-1"}], "asks": [{"price": "2", "qty": "100"}]}
        with self.assertRaisesRegex(ValueError, "INVALID_SPOT_DEPTH"):
            SpreadSpot("coinone", client).quote("KRW-USDT")

    def test_four_exchange_books_balances_and_pair_conventions(self):
        for exchange, pair in (("upbit", "KRW-USDT"), ("bithumb", "USDT-KRW"),
                               ("coinone", "USDT-KRW"), ("korbit", "usdt_krw")):
            with self.subTest(exchange=exchange):
                client = Mock()
                client.get_orderbook.return_value = {
                    "timestamp": 1800000000000, "orderbook_units": [{"bid_price": "1399.1", "ask_price": "1400.2"}],
                    "bids": [{"price": "1399.1"}], "asks": [{"price": "1400.2"}]}
                client.get_accounts.return_value = [{"currency": "USDT", "balance": "10", "locked": "2"}]
                client.get_all_balances.return_value = {"balances": [{"currency": "USDT", "available": "10", "limit": "2"}]}
                client.get_balances.return_value = [{"currency": "usdt", "available": "10", "balance": "12"}]
                adapter = SpreadSpot(exchange, client)
                self.assertEqual(adapter.quote("KRW-USDT"), {"bid": "1399.1", "ask": "1400.2", "timestamp": 1800000000, "bid_size": None, "ask_size": None})
                client.get_orderbook.assert_called_once_with(pair)
                self.assertEqual(adapter.holdings("USDT"), {"KRW": {"available": "0", "total": "0"},
                                                          "USDT": {"available": "10", "total": "12"}})

    def test_book_errors_propagate_and_missing_timestamp_uses_observation(self):
        client = Mock()
        adapter = SpreadSpot("coinone", client, clock=lambda: 42)
        client.get_orderbook.return_value = {"bids": [{"price": "1"}], "asks": [{"price": "2"}]}
        self.assertEqual(adapter.quote("KRW-USDT")["timestamp"], 42)
        client.get_orderbook.return_value["asks"][0]["price"] = "0.9"
        with self.assertRaisesRegex(ValueError, "INVALID_SPOT_QUOTE"):
            adapter.quote("KRW-USDT")
        client.get_orderbook.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            adapter.quote("KRW-USDT")
        self.assertEqual(client.get_orderbook.call_count, 3)  # No hidden retries.

    def test_cumulative_terminal_events_for_all_four_venues(self):
        for exchange in ("upbit", "bithumb", "coinone", "korbit"):
            with self.subTest(exchange=exchange):
                adapter = SpreadSpot(exchange, Mock())
                message = {"event": "order", "id": "fixture", "market": "KRW-USDT", "state": "trade_done" if exchange == "coinone" else "done",
                           "terminal": True, "order": {"side": "buy", "orderType": "limit"},
                           "exact": {"exchangeOrderId": "fixture", "originalVolume": "10000", "cumulativeFilled": "10000", "remainingVolume": "0"}}
                order = adapter.event(message, 10000, "buy")
                self.assertEqual((order.id, order.filled, order.status), ("fixture", "10000", "FILLED"))
                self.assertIsNone(adapter.event({"event": "order"}, 10000, "buy"))

    def test_submit_and_cancel_reuse_existing_adapter(self):
        for exchange in ("upbit", "bithumb", "coinone", "korbit"):
            with self.subTest(exchange=exchange):
                client = Mock()
                client.place_order.return_value = {"uuid": "fixture", "order_id": "fixture", "orderId": "fixture"}
                adapter = SpreadSpot(exchange, client)
                self.assertEqual(adapter.submit("KRW-USDT", "buy", "1400", "10000", "fixture-intent"), "fixture")
                adapter.cancel("KRW-USDT", "fixture")
                self.assertEqual(client.place_order.call_count, 1)
                self.assertEqual(client.cancel_order.call_count, 1)
