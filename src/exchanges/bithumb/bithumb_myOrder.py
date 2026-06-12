from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import jwt
from src.core.event_constants import EXCHANGE_BITHUMB, SOURCE_MYTRADE

try:
    import websocket
except ModuleNotFoundError:
    websocket = None  # type: ignore[assignment]


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    result: list[str] = []

    for ch in line:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            break
        result.append(ch)

    return "".join(result).rstrip()


def _parse_simple_yaml_mapping(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue

        line = _strip_comment(raw_line)
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if ":" not in content:
            continue

        key, value = content.split(":", 1)
        key = key.strip()
        value = value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError("invalid YAML indentation")

        current = stack[-1][1]
        if value == "":
            child: dict[str, Any] = {}
            current[key] = child
            stack.append((indent, child))
        else:
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            current[key] = value

    return root


def _to_bithumb_market_code(ticker: str) -> str:
    normalized = ticker.strip().upper()
    parts = normalized.split("-", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"invalid ticker '{ticker}'. expected format like 'btc-krw'")

    base_coin, quote = parts[0], parts[1]
    return f"{quote}-{base_coin}"


class BithumbMyOrder:
    PRIVATE_WS_URL = "wss://ws-api.bithumb.com/websocket/v1/private"

    def __init__(
        self,
        api_key: str,
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

        if not api_key:
            raise ValueError("api_key is required")
        if not secret_key:
            raise ValueError("secret_key is required")

        self.api_key = api_key
        self.secret_key = secret_key
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.ping_interval_seconds = ping_interval_seconds
        self.event_queue = event_queue
        self.on_event = on_event
        self._ws: websocket.WebSocket | None = None
        self._listen_thread: threading.Thread | None = None
        self._ping_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._send_lock = threading.Lock()

    @classmethod
    def from_info_yaml(cls, file_path: str = "info.yaml") -> "BithumbMyTrade":
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"{file_path} not found")

        config = _parse_simple_yaml_mapping(path.read_text(encoding="utf-8"))
        api_key = config.get("bithumb_api_key")
        secret_key = config.get("bithumb_secret_key")

        if not api_key or not secret_key:
            raise ValueError("info.yaml must include 'bithumb_api_key' and 'bithumb_secret_key'")

        return cls(api_key=str(api_key), secret_key=str(secret_key))

    def _build_authorization_header(self) -> str:
        payload = {
            "access_key": self.api_key,
            "nonce": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
        }
        token = jwt.encode(payload, self.secret_key, algorithm="HS256")
        return f"Bearer {token}"

    def connect(self) -> None:
        if self._ws is not None:
            return

        self._stop_event.clear()
        self._ws = websocket.create_connection(
            self.url,
            timeout=self.timeout_seconds,
            header=[f"authorization: {self._build_authorization_header()}"],
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
                    # RFC6455 ping frame from client to keep the connection alive.
                    self._ws.ping("keepalive")
                except Exception:
                    break

        self._ping_thread = threading.Thread(target=_loop, daemon=True)
        self._ping_thread.start()

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

    def send_request(
        self,
        request_types: list[dict[str, Any]],
        *,
        ticket: str | None = None,
        format_type: str = "DEFAULT",
    ) -> None:
        if self._ws is None:
            raise RuntimeError("websocket is not connected")

        body: list[dict[str, Any]] = [{"ticket": ticket or f"mytrade-{uuid.uuid4()}"}]
        body.extend(request_types)
        if format_type:
            body.append({"format": format_type})

        with self._send_lock:
            self._ws.send(json.dumps(body))

    def subscribe_my_order(
        self,
        tickers: list[str] | None = None,
        *,
        format_type: str = "DEFAULT",
        only_snapshot: bool = False,
        only_realtime: bool = False,
    ) -> None:
        req: dict[str, Any] = {
            "type": "myOrder",
            "codes": [] if not tickers else [_to_bithumb_market_code(t) for t in tickers],
        }
        if only_snapshot:
            req["is_only_snapshot"] = True
        if only_realtime:
            req["is_only_realtime"] = True

        self.send_request([req], format_type=format_type)

    def recv_once(self) -> dict[str, Any] | str | None:
        if self._ws is None:
            raise RuntimeError("websocket is not connected")

        try:
            raw = self._ws.recv()
        except websocket.WebSocketTimeoutException:
            # No application message within socket timeout window.
            # Keep the connection alive and let caller continue looping.
            return None

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")

        # App-level heartbeat handling (text ping/pong).
        if isinstance(raw, str):
            lowered = raw.strip().lower()
            if lowered == "ping":
                with self._send_lock:
                    self._ws.send("pong")
                return raw
            if lowered == "pong":
                return raw

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def _emit_event(self, msg: dict[str, Any] | str) -> None:
        event = {
            "source": SOURCE_MYTRADE,
            "exchange": EXCHANGE_BITHUMB,
            "event_type": msg.get("type") if isinstance(msg, dict) else "raw",
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


if __name__ == "__main__":
    client = BithumbMyTrade.from_info_yaml("info.yaml")
    client.connect()

    # example: all markets(myOrder)
    client.subscribe_my_order(tickers=None)

    print("[BithumbMyTrade] listening... (10 seconds)")
    started = time.time()
    try:
        while time.time() - started < 10:
            print(client.recv_once())
    finally:
        client.close()
