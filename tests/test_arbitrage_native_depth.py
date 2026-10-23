"""Coinone/DigitalX snapshot streams: fake sockets and clock, never sleep."""
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from websocket import WebSocketTimeoutException
from src.exchanges.api_error import ExchangeRequestError
from src.exchanges.arbitrage_depth import open_spot_depth_stream
from src.exchanges.public_orderbook import URLS


class NativeDepthTests(unittest.TestCase):
    evidence = []

    @classmethod
    def tearDownClass(cls):
        root = Path(__file__).parent / "results" / "arbitrage-native-spot"
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        (root / f"synthetic-depth-{stamp}.json").write_text(json.dumps(cls.evidence, indent=2), encoding="utf-8")

    def setUp(self):
        self.socket = Mock(spec=["send", "recv", "settimeout", "close"])
        self.connect = self.enterContext(patch("websocket.create_connection", return_value=self.socket))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.clock = self.enterContext(patch("src.exchanges.arbitrage_depth.time.monotonic", return_value=100))
        self.enterContext(patch("src.exchanges.arbitrage_depth.time.time", return_value=1800000000.125))

    def open(self, exchange):
        stream = open_spot_depth_stream(exchange, ["XRP", "KRW-BTC"], timeout_seconds=0.25)
        self.addCleanup(stream.close)
        return stream

    def frame(self, exchange):
        data = dict(asks=[dict(price="3002", qty="4"), dict(price="3001", qty="2")],
                    bids=[dict(price="2999", qty="3"), dict(price="3000", qty="1")], timestamp=1725930007672)
        if exchange == "coinone":
            data.update(quote_currency="krw", target_currency="xrp")
            return dict(response_type="DATA", channel="ORDERBOOK", data=data)
        return dict(type="orderbook", symbol="xrp_krw", timestamp=1725930007999, data=data)

    def receive(self, stream, frame):
        self.socket.recv.return_value = json.dumps(frame).encode()
        result = stream.recv()
        self.evidence.append(dict(exchange=stream.exchange, api_category="public", api_name=self._testMethodName,
            http_method="WebSocket", endpoint=URLS[stream.exchange],
            request_parameters=[json.loads(c.args[0]) for c in self.socket.send.call_args_list],
            captured_at=datetime.now(timezone.utc).isoformat(), state="HEALTHY", mode="synthetic",
            response_or_error=frame, normalized=result, connect_timeout_seconds=10, io_timeout_seconds=0.25))
        return result

    def test_native_subscriptions_single_connection_and_normalized_snapshot(self):
        for exchange in ("coinone", "korbit"):
            with self.subTest(exchange=exchange):
                self.connect.reset_mock()
                self.socket.reset_mock()
                stream = self.open(exchange)
                self.connect.assert_called_once_with(URLS[exchange], timeout=10)
                self.socket.settimeout.assert_called_once_with(0.25)
                sent = [json.loads(c.args[0]) for c in self.socket.send.call_args_list]
                if exchange == "coinone":
                    self.assertEqual(len(sent), 2)
                    self.assertEqual(sent[0]["topic"], dict(quote_currency="KRW", target_currency="XRP"))
                else:
                    self.assertEqual(sent[0][0]["symbols"], ["xrp_krw", "btc_krw"])
                result = self.receive(stream, self.frame(exchange))
                self.assertEqual(result["ticker"], "XRP")
                self.assertEqual(result["market"], "KRW-XRP")
                self.assertEqual(result["exchange"], exchange)
                self.assertEqual(result["asks"][0], dict(price=3001., qty=2.))
                self.assertEqual(result["bids"][0], dict(price=3000., qty=1.))
                self.assertEqual(result["sourceTimestampMs"], 1725930007672)
                self.assertEqual(result["receivedAtMs"], 1800000000125)
                stream.close()
                stream.close()
                self.socket.close.assert_called_once_with(timeout=0.25)
                with self.assertRaises(ConnectionError):
                    stream.recv()

    def test_digitalx_alias_and_missing_data_timestamp_never_uses_send_time(self):
        stream = self.open("digitalx")
        frame = self.frame("korbit")
        frame["data"].pop("timestamp")
        self.assertEqual(self.receive(stream, frame)["sourceTimestampMs"], 0)
        self.assertEqual(stream.exchange, "korbit")

    def test_coinone_simple_protocol(self):
        stream = self.open("coinone")
        result = self.receive(stream, dict(r="DATA", c="ORDERBOOK", d=dict(qc="krw", tc="xrp",
            t=1725930007672, a=[dict(p="3001", q="2")], b=[dict(p="3000", q="1")])))
        self.assertEqual(result["bids"][0]["qty"], 1)

    def test_timeouts_controls_closed_and_invalid_books_no_reconnect(self):
        stream = self.open("korbit")
        self.socket.recv.side_effect = WebSocketTimeoutException()
        self.assertIsNone(stream.recv())
        self.socket.recv.assert_called_once_with()
        self.socket.recv.side_effect = None
        self.assertIsNone(self.receive(stream, {"type": "ticker", "symbol": "xrp_krw"}))
        self.assertIsNone(self.receive(stream, {**self.frame("korbit"), "symbol": "eth_krw"}))
        for payload, error in (("", ConnectionError), ("invalid", ValueError),
                               (json.dumps({"error": {"message": "INVALID_ARGUMENT"}}), ExchangeRequestError)):
            self.socket.recv.return_value = payload
            with self.assertRaises(error):
                stream.recv()
        frame = self.frame("korbit")
        frame["data"]["asks"][0]["price"] = "1"
        with self.assertRaisesRegex(ValueError, "CROSSED"):
            self.receive(stream, frame)
        self.connect.assert_called_once()

    def test_coinone_ping_pong_deadline_without_sleep_and_no_digitalx_json_ping(self):
        stream = self.open("coinone")
        self.clock.return_value = 120
        self.receive(stream, {"response_type": "PONG"})
        self.assertEqual(json.loads(self.socket.send.call_args.args[0]), {"request_type": "PING"})
        self.clock.return_value = 140
        self.socket.recv.side_effect = WebSocketTimeoutException()
        self.assertIsNone(stream.recv())
        self.clock.return_value = 155
        with self.assertRaisesRegex(TimeoutError, "PONG_TIMED_OUT"):
            stream.recv()
        stream.close()
        self.socket.reset_mock()
        other = self.open("korbit")
        self.clock.return_value = 1000
        self.assertIsNone(other.recv())
        self.socket.send.assert_called_once()

    def test_subscription_failure_preserves_first_error_and_closes(self):
        original = TimeoutError("synthetic send timeout")
        self.socket.send.side_effect = original
        self.socket.close.side_effect = RuntimeError("secondary close failure")
        with self.assertRaises(TimeoutError) as caught:
            self.open("coinone")
        self.assertIs(caught.exception, original)
        self.socket.close.assert_called_once_with(timeout=0.25)
        self.connect.assert_called_once()

    def test_bounds_validate_before_connect(self):
        for exchange in ("coinone", "korbit"):
            for symbols in ([], "XRP", [f"KRW-T{i}" for i in range(601)], ["BTC-USDT"]):
                with self.assertRaises(ValueError):
                    open_spot_depth_stream(exchange, symbols)
            for timeout in (None, 0, -1, float("inf"), True):
                with self.assertRaises(ValueError):
                    open_spot_depth_stream(exchange, ["XRP"], timeout_seconds=timeout)
        self.connect.assert_not_called()

    def test_reference_markets_over_100_and_local_600_bound(self):
        for exchange in ("coinone", "korbit"):
            for size in (104, 600):
                self.socket.reset_mock()
                stream = open_spot_depth_stream(exchange, [f"T{i}" for i in range(size)])
                self.addCleanup(stream.close)
                self.assertEqual(self.socket.send.call_count, size if exchange == "coinone" else 1)
                if exchange == "korbit":
                    self.assertEqual(len(json.loads(self.socket.send.call_args.args[0])[0]["symbols"]), size)
                stream.close()


if __name__ == "__main__":
    unittest.main()
