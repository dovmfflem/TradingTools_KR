from __future__ import annotations

import base64
import hashlib
import hmac
import json
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from src.core.event_constants import EXCHANGE_COINONE, SOURCE_MYTRADE, SOURCE_ORDERBOOK
from src.exchanges.coinone.coinone_rest import _parse_simple_yaml_mapping
from src.exchanges.coinone.coinone_rest import _to_pair

try:
    import websocket
except ModuleNotFoundError:
    websocket = None  # type: ignore[assignment]


class CoinoneDataBank:
    WS_URL = "wss://stream.coinone.co.kr"

    def __init__(
        self,
        tickers: list[str],
        *,
        count: int = 5,
        ws_reconnect_delay_seconds: float = 1.0,
        event_queue: queue.Queue[dict[str, Any]] | None = None,
        on_update: Any | None = None,
        auto_start: bool = True,
    ) -> None:
        if not tickers:
            raise ValueError("tickers is required")
        self.count = count
        self.ws_reconnect_delay_seconds = ws_reconnect_delay_seconds
        self.event_queue = event_queue
        self.on_update = on_update

        self._pairs: dict[str, tuple[str, str]] = {}
        self._data: dict[str, dict[str, Any]] = {}
        for ticker in tickers:
            quote, target = _to_pair(ticker)
            key = target.lower()
            self._pairs[key] = (quote, target)
            self._data[key] = {
                "asks": [],
                "bids": [],
                "updated_at": 0.0,
                "asks_updated_at": 0.0,
                "bids_updated_at": 0.0,
            }

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._ws_thread = threading.Thread(target=self._ws_loop, daemon=True)

        if auto_start:
            self.start()

    def start(self) -> None:
        if self._ws_thread.is_alive():
            return
        self._stop_event.clear()
        self._ws_thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._ws_thread.start()

    def stop(self, timeout_seconds: float = 2.0) -> None:
        self._stop_event.set()
        if self._ws_thread.is_alive():
            self._ws_thread.join(timeout=timeout_seconds)

    def get_data(self, ticker: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            if ticker is None:
                return dict(self._data)
            return self._data.get(ticker.lower())

    def _ws_loop(self) -> None:
        if websocket is None:
            return

        requests_payload = [
            {
                "request_type": "SUBSCRIBE",
                "channel": "ORDERBOOK",
                "topic": {"quote_currency": quote, "target_currency": target},
            }
            for quote, target in self._pairs.values()
        ]

        while not self._stop_event.is_set():
            ws = None
            try:
                ws = websocket.create_connection(self.WS_URL, timeout=10)
                for request in requests_payload:
                    ws.send(json.dumps(request))

                while not self._stop_event.is_set():
                    raw = ws.recv()
                    if not raw:
                        continue
                    message = json.loads(raw)
                    self._handle_message(message)
            except Exception:
                time.sleep(self.ws_reconnect_delay_seconds)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_levels(self, levels: Any, *, side: str) -> list[dict[str, float]]:
        if not isinstance(levels, list):
            return []
        result: list[dict[str, float]] = []
        for level in levels:
            if not isinstance(level, dict):
                continue
            price = self._to_float(level.get("price") or level.get("p"))
            qty = self._to_float(level.get("qty") or level.get("q"))
            if price is None or qty is None:
                continue
            result.append({"price": price, "qty": qty})
        if side == "ask":
            result.sort(key=lambda item: item["price"])
        else:
            result.sort(key=lambda item: item["price"], reverse=True)
        result = result[: self.count]
        return result

    def _handle_message(self, message: dict[str, Any]) -> None:
        response_type = str(message.get("response_type") or message.get("r") or "").upper()
        channel = str(message.get("channel") or message.get("c") or "").upper()
        if response_type != "DATA" or channel != "ORDERBOOK":
            return

        data = message.get("data") or message.get("d")
        if not isinstance(data, dict):
            return

        target = str(data.get("target_currency") or data.get("tc") or "").lower()
        if target not in self._pairs:
            return

        asks = self._normalize_levels(data.get("asks") or data.get("a"), side="ask")
        bids = self._normalize_levels(data.get("bids") or data.get("b"), side="bid")
        if not asks and not bids:
            return

        updated_at = time.time()
        with self._lock:
            current = self._data[target]
            if asks:
                current["asks"] = asks
                current["asks_updated_at"] = updated_at
            if bids:
                current["bids"] = bids
                current["bids_updated_at"] = updated_at
            current["updated_at"] = updated_at

        event = {
            "source": SOURCE_ORDERBOOK,
            "exchange": EXCHANGE_COINONE,
            "ticker": target,
            "asks": asks,
            "bids": bids,
            "updated_at": updated_at,
            "asks_updated_at": updated_at if asks else None,
            "bids_updated_at": updated_at if bids else None,
        }
        if self.event_queue is not None:
            try:
                self.event_queue.put_nowait(event)
            except queue.Full:
                pass
        if callable(self.on_update):
            self.on_update(event)


class CoinoneMyOrder:
    PRIVATE_WS_URL = "wss://stream.coinone.co.kr/v1/private"

    def __init__(
        self,
        access_token: str,
        secret_key: str,
        *,
        url: str = PRIVATE_WS_URL,
        timeout_seconds: float = 10.0,
        ping_interval_seconds: float = 30.0,
        event_queue: queue.Queue[dict[str, Any]] | None = None,
        on_event: Any | None = None,
    ) -> None:
        if websocket is None:
            raise RuntimeError("websocket-client is required. install: pip install websocket-client")
        self.access_token = access_token
        self.secret_key = secret_key
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.ping_interval_seconds = ping_interval_seconds
        self.event_queue = event_queue
        self.on_event = on_event

        self._ws: Any | None = None
        self._listen_thread: threading.Thread | None = None
        self._ping_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._send_lock = threading.Lock()

    @classmethod
    def from_info_yaml(cls, file_path: str = "info.yaml") -> "CoinoneMyOrder":
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"{file_path} not found")
        config = _parse_simple_yaml_mapping(path.read_text(encoding="utf-8"))
        access_token = config.get("coinone_access_token")
        secret_key = config.get("coinone_secret_key")
        if not access_token or not secret_key:
            raise ValueError(
                "info.yaml must include 'coinone_access_token' and 'coinone_secret_key'"
            )
        return cls(access_token=str(access_token), secret_key=str(secret_key))

    def _auth_headers(self) -> list[str]:
        payload = {
            "access_token": self.access_token,
            "nonce": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
        }
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        body_b64 = base64.b64encode(body.encode("utf-8")).decode("utf-8")
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            body_b64.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()
        return [
            f"X-COINONE-PAYLOAD: {body_b64}",
            f"X-COINONE-SIGNATURE: {signature}",
        ]

    def connect(self) -> None:
        if self._ws is not None:
            return
        ws_module = websocket
        if ws_module is None:
            raise RuntimeError("websocket-client is required. install: pip install websocket-client")

        self._stop_event.clear()
        self._ws = ws_module.create_connection(
            self.url,
            timeout=self.timeout_seconds,
            header=self._auth_headers(),
        )
        self._start_ping_loop()

    def _start_ping_loop(self) -> None:
        if self._ping_thread is not None and self._ping_thread.is_alive():
            return

        def _loop() -> None:
            while not self._stop_event.wait(self.ping_interval_seconds):
                if self._ws is None:
                    break
                try:
                    with self._send_lock:
                        self._ws.send(json.dumps({"request_type": "PING"}))
                except Exception:
                    break

        self._ping_thread = threading.Thread(target=_loop, daemon=True)
        self._ping_thread.start()

    def subscribe_my_order(self, tickers: list[str] | None = None, *, format_type: str = "DEFAULT") -> None:
        if self._ws is None:
            raise RuntimeError("websocket is not connected")

        topics: list[dict[str, str]] = []
        if tickers:
            for ticker in tickers:
                quote, target = _to_pair(ticker)
                topics.append({"quote_currency": quote, "target_currency": target})

        request_body: dict[str, Any] = {
            "request_type": "SUBSCRIBE",
            "channel": "MYORDER",
            "topic": topics,
        }
        if format_type:
            request_body["format"] = format_type

        with self._send_lock:
            self._ws.send(json.dumps(request_body))

    def recv_once(self) -> dict[str, Any] | str | None:
        if self._ws is None:
            raise RuntimeError("websocket is not connected")
        try:
            raw = self._ws.recv()
        except Exception as error:
            if error.__class__.__name__ == "WebSocketTimeoutException":
                return None
            raise

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except Exception:
            return raw

    def _emit_event(self, msg: dict[str, Any] | str) -> None:
        event = {
            "source": SOURCE_MYTRADE,
            "exchange": EXCHANGE_COINONE,
            "event_type": msg.get("channel") if isinstance(msg, dict) else "raw",
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

    def start_listen(self, on_message: Any | None = None) -> None:
        if self._listen_thread is not None and self._listen_thread.is_alive():
            return

        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.is_set() and self._ws is not None:
                try:
                    msg = self.recv_once()
                    if msg is None:
                        continue
                    self._emit_event(msg)
                    if callable(on_message):
                        on_message(msg)
                except Exception:
                    break

        self._listen_thread = threading.Thread(target=_loop, daemon=True)
        self._listen_thread.start()

    def close(self) -> None:
        self._stop_event.set()
        if self._listen_thread is not None and self._listen_thread.is_alive():
            self._listen_thread.join(timeout=2.0)
        if self._ping_thread is not None and self._ping_thread.is_alive():
            self._ping_thread.join(timeout=2.0)
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
