"""No-network tests of the current Bithumb public depth protocol and lifecycle."""
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from websocket import WebSocketTimeoutException

from src.exchanges.api_error import ExchangeRequestError
from src.exchanges.arbitrage_spot import open_spot_depth_stream
from src.exchanges.bithumb.bithumb_websocket import BithumbPublicWebSocket
from src.exchanges.stream_transport import open_public_stream, spot_depth_quote


class ArbitrageDepthTests(unittest.TestCase):
    evidence = []

    @classmethod
    def tearDownClass(cls):
        root = Path(__file__).parent / "results" / "arbitrage-spot"
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        (root / f"depth-{stamp}.json").write_text(json.dumps(cls.evidence, indent=2), encoding="utf-8")

    def setUp(self):
        self.socket = Mock(spec=["send", "recv", "settimeout", "close"])
        self.connect = self.enterContext(patch("websocket.create_connection", return_value=self.socket))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("src.exchanges.arbitrage_depth.time.time", return_value=1800000000.125))
        self.frame = {"type": "orderbook", "code": "KRW-XRP", "timestamp": 1725930007672124,
                      "orderbook_units": [
                          {"bid_price": 2999, "ask_price": 3002, "bid_size": 4, "ask_size": 3},
                          {"bid_price": 3000, "ask_price": 3001, "bid_size": 2, "ask_size": 1}]}

    def open(self, **kwargs):
        stream = open_spot_depth_stream("bithumb", ["XRP", "BTC"], **kwargs)
        self.addCleanup(stream.close)
        return stream

    def receive(self, stream, frame):
        self.socket.recv.return_value = json.dumps(frame).encode()
        result = stream.recv()
        self.evidence.append({"exchange": "bithumb", "api_category": "public", "api_name": self._testMethodName,
                              "http_method": "WebSocket", "endpoint": BithumbPublicWebSocket.PUBLIC_WS_URL,
                              "request_parameters": json.loads(self.socket.send.call_args.args[0]),
                              "captured_at": datetime.now(timezone.utc).isoformat(), "state": "HEALTHY",
                              "transport": "synthetic, not sent", "response": frame, "normalized": result})
        return result

    def test_current_public_endpoint_has_existing_ten_second_connect_deadline(self):
        result = open_public_stream("bithumb", connect=self.connect)
        self.assertIs(result, self.socket)
        self.connect.assert_called_once_with(BithumbPublicWebSocket.PUBLIC_WS_URL, timeout=10)

    def test_subscribe_once_and_normalize_default_full_depth(self):
        stream = self.open(timeout_seconds=0.25)
        self.connect.assert_called_once_with(BithumbPublicWebSocket.PUBLIC_WS_URL, timeout=10)
        self.socket.settimeout.assert_called_once_with(0.25)
        frame = json.loads(self.socket.send.call_args.args[0])
        self.assertEqual(frame[1], {"type": "orderbook", "codes": ["KRW-XRP", "KRW-BTC"]})
        self.assertEqual(frame[2], {"format": "DEFAULT"})
        result = self.receive(stream, self.frame)
        self.assertEqual(result["ticker"], "XRP")
        self.assertEqual(result["asks"], [{"price": 3001, "qty": 1}, {"price": 3002, "qty": 3}])
        self.assertEqual(result["bids"], [{"price": 3000, "qty": 2}, {"price": 2999, "qty": 4}])
        self.assertEqual(result["sourceTimestampMs"], 1725930007672)
        self.assertEqual(result["receivedAtMs"], 1800000000125)
        self.socket.recv.assert_called_once_with()
        self.socket.send.assert_called_once()
        stream.close()
        stream.close()
        self.socket.close.assert_called_once_with(timeout=0.25)
        with self.assertRaises(ConnectionError):
            stream.recv()

    def test_simple_payload_and_missing_source_timestamp(self):
        stream = self.open()
        frame = {"ty": "orderbook", "cd": "KRW-XRP", "obu": [{"bp": 3000, "ap": 3001, "bs": 2, "as": 3}]}
        self.assertEqual(self.receive(stream, frame)["sourceTimestampMs"], 0)
        frame["tms"] = 1725930007672
        self.assertEqual(self.receive(stream, frame)["sourceTimestampMs"], 1725930007672)
        frame["tms"] = 0
        self.assertEqual(self.receive(stream, frame)["sourceTimestampMs"], 0)

    def test_runtime_decoder_contract_depth_and_exact_timestamp(self):
        result = spot_depth_quote("bithumb", self.frame, depth=1)
        self.assertEqual(result, {"symbol": "XRP", "asks": [{"price": 3001, "size": 1}],
                                  "bids": [{"price": 3000, "size": 2}], "sourceTimestampMs": 1725930007672})
        for stamp in (1725930007672999, 1725930007672, None, 0):
            frame = {"ty": "orderbook", "cd": "KRW-XRP", "tms": stamp,
                     "obu": [{"bp": 3000, "ap": 3001, "bs": 2, "as": 3}]}
            for exchange in ("bithumb", "upbit"):
                self.assertEqual(spot_depth_quote(exchange, frame)["sourceTimestampMs"],
                                 1725930007672 if stamp else 0)
        for depth in (0, 31, True, 1.5):
            with self.assertRaises(ValueError):
                spot_depth_quote("bithumb", self.frame, depth=depth)
        self.assertIsNone(spot_depth_quote("bithumb", {"status": "UP"}))
        self.connect.assert_not_called()

    def test_runtime_native_protocol_uses_current_endpoint_and_base_quote_subscription(self):
        protocol = BithumbPublicWebSocket(timeout_seconds=1)
        protocol.connect()
        self.addCleanup(protocol.close)
        protocol.subscribe_orderbook(["xrp-krw"], format_type="DEFAULT")
        self.connect.assert_called_once_with(BithumbPublicWebSocket.PUBLIC_WS_URL, timeout=1)
        self.assertEqual(json.loads(self.socket.send.call_args.args[0])[1]["codes"], ["KRW-XRP"])
        self.socket.recv.return_value = json.dumps(self.frame)
        self.assertEqual(spot_depth_quote("bithumb", protocol.recv_once())["symbol"], "XRP")

    def test_control_unrelated_and_timeout_frames_do_not_loop(self):
        stream = self.open()
        for frame in ({"status": "UP"}, {"type": "ticker", "code": "KRW-XRP"},
                      {**self.frame, "code": "KRW-ETH"}):
            self.assertIsNone(self.receive(stream, frame))
        self.socket.recv.reset_mock()
        self.socket.recv.side_effect = WebSocketTimeoutException("synthetic receive deadline")
        self.assertIsNone(stream.recv())
        self.socket.recv.assert_called_once_with()
        self.connect.assert_called_once()
        self.socket.recv.side_effect = None
        self.socket.recv.return_value = "PING"
        self.assertIsNone(stream.recv())
        self.socket.send.assert_called_with("pong")

    def test_closed_invalid_and_rejected_frames_surface_without_reconnect(self):
        stream = self.open()
        for frame, error_type in (("", ConnectionError), ("not json", ValueError),
                                  (json.dumps({"error": {"name": "INVALID_ARGUMENT"}}), ExchangeRequestError),
                                  (json.dumps({**self.frame, "orderbook_units": [None]}), ValueError),
                                  (json.dumps({**self.frame, "orderbook_units": []}), ValueError),
                                  (json.dumps({**self.frame, "orderbook_units": [
                                      {"bid_price": 5, "ask_price": 4, "bid_size": 1, "ask_size": 1}]}), ValueError)):
            self.socket.recv.return_value = frame
            with self.assertRaises(error_type) as caught:
                stream.recv()
            if error_type is ExchangeRequestError:
                self.assertEqual(caught.exception.exchange, "bithumb")
        self.connect.assert_called_once()

    def test_connect_and_subscription_failures_do_not_retry_or_leak_socket(self):
        first = TimeoutError("synthetic connect timeout")
        self.connect.side_effect = first
        with self.assertRaises(TimeoutError) as caught:
            self.open()
        self.assertIs(caught.exception, first)
        self.connect.assert_called_once()
        self.socket.send.assert_not_called()
        self.connect.side_effect = None
        self.connect.reset_mock()
        self.socket.send.side_effect = first
        self.socket.close.side_effect = RuntimeError("secondary close failure")
        with self.assertRaises(TimeoutError) as caught:
            self.open()
        self.assertIs(caught.exception, first)
        self.connect.assert_called_once()
        self.socket.send.assert_called_once()
        self.socket.close.assert_called_once_with(timeout=1.0)

    def test_market_bounds_and_timeout_validation_before_connect(self):
        for symbols in ([], ["BTC"] * 601, "BTC", [None], ["BTC-USDT"], ["KRW-XRP.5"]):
            with self.assertRaises(ValueError):
                open_spot_depth_stream("bithumb", symbols)
        for timeout in (0, -1, float("inf"), float("nan"), None, True):
            with self.assertRaises(ValueError):
                open_spot_depth_stream("bithumb", ["BTC"], timeout_seconds=timeout)
        with self.assertRaises(ValueError):
            open_spot_depth_stream("upbit", ["BTC"])
        self.connect.assert_not_called()
        stream = open_spot_depth_stream("bithumb", [f"KRW-TEST{index}" for index in range(600)])
        self.addCleanup(stream.close)
        self.assertEqual(len(json.loads(self.socket.send.call_args.args[0])[1]["codes"]), 600)


if __name__ == "__main__":
    unittest.main()
