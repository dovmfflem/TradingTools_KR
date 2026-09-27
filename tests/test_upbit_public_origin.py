"""Offline handshake regression tests; no exchange requests or credentials."""
import unittest
from unittest.mock import Mock, patch

from websocket._handshake import _get_handshake_headers

from src.exchanges.stream_transport import open_public_stream
from src.exchanges.upbit.upbit_databank import UpbitDataBank, UpbitPublicWebSocket


class UpbitPublicOriginTests(unittest.TestCase):
    def assert_no_origin(self, kwargs):
        self.assertIs(kwargs.get("suppress_origin"), True)
        headers, _ = _get_handshake_headers(
            "/websocket/v1", "wss://api.upbit.com/websocket/v1", "api.upbit.com", 443, kwargs
        )
        self.assertFalse(any(header.lower().startswith("origin:") for header in headers))

    def test_raw_public_stream_suppresses_generated_origin(self):
        connect = Mock()
        open_public_stream("upbit", connect=connect)
        self.assert_no_origin(connect.call_args.kwargs)
        self.assertEqual(connect.call_args.kwargs["timeout"], 10)

    def test_public_client_suppresses_origin_and_connects_once(self):
        client = UpbitPublicWebSocket(timeout_seconds=7)
        with patch("websocket.create_connection") as connect, patch.object(client, "_start_ping_loop"):
            client.connect()
            client.connect()
            connect.assert_called_once()
            self.assert_no_origin(connect.call_args.kwargs)
            self.assertEqual(connect.call_args.kwargs["timeout"], 7)
            client.close()

    def test_databank_orderbook_suppresses_origin(self):
        client = UpbitDataBank(["BTC-KRW"], auto_start=False)
        socket = Mock()

        def connect(*args, **kwargs):
            client._stop_event.set()
            self.assert_no_origin(kwargs)
            self.assertEqual(kwargs["timeout"], 10)
            return socket

        with patch("websocket.create_connection", side_effect=connect) as connection:
            client._ws_loop()
            connection.assert_called_once()
            socket.close.assert_called_once()

    def test_binance_transport_options_are_unchanged(self):
        connect = Mock()
        open_public_stream("binance_futures", streams=["btcusdt@depth5"], connect=connect)
        self.assertEqual(connect.call_args.kwargs, {"timeout": 10})

    def test_timeout_is_not_retried_by_transport(self):
        connect = Mock(side_effect=TimeoutError("connect deadline"))
        with self.assertRaises(TimeoutError):
            open_public_stream("upbit", connect=connect)
        connect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
