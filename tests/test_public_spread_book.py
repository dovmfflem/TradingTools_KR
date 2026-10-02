"""Offline wire contracts for the shared Python public book feed."""
import json
import threading
import unittest
from unittest.mock import Mock
from src.exchanges.public_orderbook import subscription, normalize_orderbook, run_orderbook_stream, URLS


def frame(exchange):
    if exchange in {"upbit", "bithumb"}:
        return {"code": "KRW-USDT", "timestamp": 1800000000000, "orderbook_units": [
            {"bid_price": "1399.01", "ask_price": "1400.02", "bid_size": "12345.67890123", "ask_size": "23456.78901234"}]}
    book = {"timestamp": 1800000000000, "bids": [{"price": "1399.01", "qty": "12345.67890123"}],
            "asks": [{"price": "1400.02", "qty": "23456.78901234"}]}
    if exchange == "coinone":
        return {"response_type": "DATA", "channel": "ORDERBOOK", "data": {**book, "quote_currency": "KRW", "target_currency": "USDT"}}
    return {"type": "orderbook", "symbol": "usdt_krw", "data": book}


class PublicBookTests(unittest.TestCase):
    def test_all_venues_keep_exact_prices_and_depth_and_reject_foreign_market(self):
        for exchange in URLS:
            with self.subTest(exchange=exchange):
                quote = normalize_orderbook(exchange, frame(exchange), "KRW-USDT", observed_at=1800000000)
                self.assertEqual(quote["bid"], "1399.01")
                self.assertEqual(quote["ask_size"], "23456.78901234")
                self.assertEqual(quote["timestamp"], 1800000000)
                self.assertIsNone(normalize_orderbook(exchange, frame(exchange), "KRW-USDC", observed_at=1800000000))

    def test_four_wire_subscriptions_and_socket_deadlines(self):
        for exchange in URLS:
            with self.subTest(exchange=exchange):
                stop, events = threading.Event(), []
                ws = Mock()
                def receive(**kwargs):
                    stop.set()
                    return 1, json.dumps(frame(exchange))
                ws.recv_data.side_effect = receive
                connect = Mock(return_value=ws)
                run_orderbook_stream({"exchange": exchange, "ticker": "USDT"}, emit=events.append, stop=stop, connect=connect)
                self.assertEqual(connect.call_count, 1)
                self.assertEqual(connect.call_args.kwargs["timeout"], 10)
                self.assertEqual(connect.call_args.args[0], URLS[exchange])
                ws.settimeout.assert_called_once_with(1)
                ws.close.assert_called_once_with(timeout=1)
                sent = json.loads(ws.send.call_args.args[0])
                if exchange == "coinone": self.assertEqual(sent["topic"]["target_currency"], "USDT")
                elif exchange == "korbit": self.assertEqual(sent[0]["symbols"], ["usdt_krw"])
                else: self.assertEqual(sent[1]["codes"], ["KRW-USDT"])
                self.assertEqual(events[-1]["event"], "quote")

    def test_no_book_times_out_without_reconnect(self):
        ws, connect, events = Mock(), Mock(), []
        connect.return_value = ws
        clock = Mock(side_effect=[0, 0, 31])
        run_orderbook_stream({"exchange": "upbit", "ticker": "USDT"}, emit=events.append, stop=threading.Event(), connect=connect, clock=clock)
        self.assertEqual(events[-1]["status"], "TIMED_OUT")
        self.assertEqual(connect.call_count, 1)
        ws.recv_data.assert_not_called()

    def test_invalid_or_crossed_books_fail_instead_of_fabricating_liquidity(self):
        data = frame("upbit")
        for value in ("NaN", "-1", None):
            data["orderbook_units"][0]["ask_size"] = value
            with self.assertRaises(ValueError):
                normalize_orderbook("upbit", data, "KRW-USDT", observed_at=1800000000)
        data = frame("upbit"); data["orderbook_units"][0]["ask_price"] = "1300"
        with self.assertRaisesRegex(ValueError, "CROSSED"):
            normalize_orderbook("upbit", data, "KRW-USDT", observed_at=1800000000)
