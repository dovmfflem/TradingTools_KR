from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any

from src.core.event_constants import EXCHANGE_BINANCE_FUTURES, SOURCE_MYTRADE
from src.exchanges.binance.binance_futures_rest import BinanceFuturesRest

try:
    import websocket
except ModuleNotFoundError:
    websocket = None  # type: ignore[assignment]


class BinanceFuturesMyOrder:
    WS_BASE_URL = "wss://fstream.binance.com/ws"

    def __init__(
        self,
        rest_client: BinanceFuturesRest,
        *,
        ws_base_url: str = WS_BASE_URL,
        timeout_seconds: float = 10.0,
        listen_key_keepalive_seconds: float = 30.0 * 60.0,
        reconnect_delay_seconds: float = 1.0,
        event_queue: queue.Queue[dict[str, Any]] | None = None,
        on_event: Any | None = None,
    ) -> None:
        if websocket is None:
            raise RuntimeError("websocket-client is required. install: pip install websocket-client")

        self.rest_client = rest_client
        self.ws_base_url = ws_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.listen_key_keepalive_seconds = listen_key_keepalive_seconds
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.event_queue = event_queue
        self.on_event = on_event

        self._listen_key: str | None = None
        self._ws: Any | None = None
        self._listen_thread: threading.Thread | None = None
        self._keepalive_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    @classmethod
    def from_info_yaml(cls, file_path: str = "info.yaml") -> "BinanceFuturesMyOrder":
        rest = BinanceFuturesRest.from_info_yaml(file_path)
        return cls(rest)

    def connect(self) -> None:
        with self._lock:
            if self._ws is not None:
                return

            self._stop_event.clear()
            self._listen_key = self.rest_client.create_listen_key()
            ws_url = f"{self.ws_base_url}/{self._listen_key}"
            ws_module = websocket
            if ws_module is None:
                raise RuntimeError("websocket-client is required. install: pip install websocket-client")
            self._ws = ws_module.create_connection(ws_url, timeout=self.timeout_seconds)

            self._start_keepalive_loop()

    def _start_keepalive_loop(self) -> None:
        if self._keepalive_thread is not None and self._keepalive_thread.is_alive():
            return

        def _loop() -> None:
            while not self._stop_event.wait(self.listen_key_keepalive_seconds):
                listen_key = self._listen_key
                if not listen_key:
                    continue
                try:
                    _ = self.rest_client.keepalive_listen_key(listen_key)
                except Exception:
                    continue

        self._keepalive_thread = threading.Thread(target=_loop, daemon=True)
        self._keepalive_thread.start()

    def _emit_event(self, msg: dict[str, Any] | str) -> None:
        event = {
            "source": SOURCE_MYTRADE,
            "exchange": EXCHANGE_BINANCE_FUTURES,
            "event_type": msg.get("e") if isinstance(msg, dict) else "raw",
            "payload": msg,
            "received_at": time.time(),
        }
        if self.event_queue is not None:
            try:
                self.event_queue.put_nowait(event)
            except queue.Full:
                pass
        if callable(self.on_event):
            self.on_event(event)

    def recv_once(self) -> dict[str, Any] | str | None:
        ws = self._ws
        if ws is None:
            raise RuntimeError("websocket is not connected")
        try:
            raw = ws.recv()
        except Exception as error:
            if error.__class__.__name__ == "WebSocketTimeoutException":
                return None
            raise

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not isinstance(raw, str):
            return None

        lowered = raw.strip().lower()
        if lowered in {"ping", "pong"}:
            return raw

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        return parsed

    def _close_socket(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _close_listen_key(self) -> None:
        listen_key = self._listen_key
        self._listen_key = None
        if not listen_key:
            return
        try:
            _ = self.rest_client.close_listen_key(listen_key)
        except Exception:
            pass

    def close(self) -> None:
        self._stop_event.set()

        if self._listen_thread is not None and self._listen_thread.is_alive():
            self._listen_thread.join(timeout=2.0)
        if self._keepalive_thread is not None and self._keepalive_thread.is_alive():
            self._keepalive_thread.join(timeout=2.0)

        with self._lock:
            self._close_socket()
            self._close_listen_key()

    def start_listen(self, on_message: Any | None = None) -> None:
        if self._listen_thread is not None and self._listen_thread.is_alive():
            return

        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.is_set():
                try:
                    if self._ws is None:
                        self.connect()
                    msg = self.recv_once()
                    if msg is None:
                        continue
                    self._emit_event(msg)
                    if callable(on_message):
                        on_message(msg)
                except Exception:
                    with self._lock:
                        self._close_socket()
                        self._close_listen_key()
                    if self._stop_event.wait(self.reconnect_delay_seconds):
                        break

        self._listen_thread = threading.Thread(target=_loop, daemon=True)
        self._listen_thread.start()
