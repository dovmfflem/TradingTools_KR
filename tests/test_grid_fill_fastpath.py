"""Offline documented private messages; never contacts an exchange."""
import unittest
from src.exchanges.private_events import normalize_private_order_event
from src.exchanges.spot_trading import CoinoneSpot, BithumbSpot


def coinone_event(**changes):
    row = dict(quote_currency="KRW", target_currency="BTC", order_id="fixture",
               side="BID", type="LIMIT", status="trade_done", order_qty=None,
               remain_qty="0", executed_qty="0.2", trade_id="last-fill")
    row.update(changes)
    return normalize_private_order_event("coinone", {
        "response_type": "DATA", "channel": "MYORDER", "data": row}, "KRW-BTC")


class GridFillFastpathTests(unittest.TestCase):
    def test_coinone_null_original_quantity_uses_known_order_not_last_trade_size(self):
        result = CoinoneSpot(None).event(coinone_event(), "1", "buy")
        self.assertIsNotNone(result)
        self.assertEqual((result.status, result.filled), ("FILLED", "1"))

    def test_coinone_partial_and_repeated_trades_are_absolute_not_accumulated(self):
        adapter = CoinoneSpot(None)
        event = coinone_event(status="trade", remain_qty="0.700000000000000001")
        first = adapter.event(event, "1", "buy")
        self.assertIsNotNone(first)
        self.assertEqual((first.status, first.filled), ("OPEN", "0.299999999999999999"))
        self.assertEqual(adapter.event(event, "1", "buy"), first)
        self.assertEqual(adapter.event(coinone_event(status="trade"), "1", "buy").status, "FILLED")

    def test_coinone_wait_ack_avoids_redundant_rest(self):
        result = CoinoneSpot(None).event(coinone_event(status="wait", order_qty="1", remain_qty=None), "1", "buy")
        self.assertIsNotNone(result)
        self.assertEqual((result.status, result.filled), ("OPEN", "0"))

    def test_coinone_ambiguous_cancel_and_invalid_messages_need_rest(self):
        adapter = CoinoneSpot(None)
        for changes in [dict(status="cancel"), dict(status="cancel_post_only"),
                        dict(remain_qty=None), dict(remain_qty="NaN"), dict(remain_qty="-1"),
                        dict(remain_qty="2"), dict(remain_qty="0.5"),
                        dict(type="MARKET"), dict(prevented_qty="0.2"), dict(prevented_qty="NaN")]:
            with self.subTest(changes=changes):
                self.assertIsNone(adapter.event(coinone_event(**changes), "1", "buy"))
        for changes in [dict(side="ASK"), dict(order_qty="2")]:
            with self.assertRaises(ValueError):
                adapter.event(coinone_event(**changes), "1", "buy")
        self.assertTrue(coinone_event(prevented_qty="NaN")["reconcileRequired"])

    def test_bithumb_full_trade_finishes_before_sparse_done(self):
        def event(**changes):
            row = dict(type="myOrder", code="KRW-BTC", order_id="fixture", side="buy",
                       order_type="limit", state="trade", order_quantity="1",
                       executed_quantity="1", remaining_quantity="0")
            row.update(changes)
            return normalize_private_order_event("bithumb", row, "KRW-BTC")
        adapter = BithumbSpot(None)
        result = adapter.event(event(), "1", "buy")
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "FILLED")
        self.assertEqual(adapter.event(event(executed_quantity="0.3", remaining_quantity="0.7"), "1", "buy").status, "OPEN")
        self.assertIsNone(adapter.event(event(state="done", executed_quantity=None, remaining_quantity=None,
                                              cancel_type="tif_cancel"), "1", "buy"))
        self.assertEqual(adapter.event(event(state="wait", executed_quantity=None, remaining_quantity=None), "1", "buy").filled, "0")
