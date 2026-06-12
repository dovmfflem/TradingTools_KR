from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import jwt
from src.core.event_constants import EXCHANGE_UPBIT, SOURCE_MYTRADE, SOURCE_ORDERBOOK

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


def _to_upbit_market_code(value: str) -> str:
    raw = value.strip()
    normalized = raw.upper()
    raw_parts = raw.split("-", 1)
    parts = normalized.split("-", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"invalid ticker '{value}'. expected format like 'btc-krw' or 'KRW-BTC'"
        )

    first, second = parts[0], parts[1]
    quote_currencies = {"KRW", "BTC", "USDT"}
    if (
        len(raw_parts) == 2
        and raw_parts[0].isupper()
        and first in quote_currencies
    ):
        return f"{first}-{second}"
    if second in quote_currencies:
        return f"{second}-{first}"
    if first in quote_currencies:
        return f"{first}-{second}"
    return f"{second}-{first}"


@dataclass(frozen=True)
class _TickerTarget:
    key: str
    market: str


class UpbitDataBank:
    """
    Maintain latest Upbit orderbooks.

    - Primary source: WebSocket
    - Fallback: REST batch request if no data is received for a ticker in `stale_after_seconds`
    """

    WS_URL = "wss://api.upbit.com/websocket/v1"
    REST_ORDERBOOK_URL = "https://api.upbit.com/v1/orderbook"

    def __init__(
        self,
        tickers: list[str],
        *,
        count: int = 5,
        quote: str = "KRW",
        stale_after_seconds: float = 5.0,
        rest_check_interval_seconds: float = 1.0,
        rest_timeout_seconds: float = 5.0,
        ws_reconnect_delay_seconds: float = 1.0,
        event_queue: queue.Queue[Mapping[str, object]] | None = None,
        on_update: Callable[[Mapping[str, object]], None] | None = None,
        auto_start: bool = True,
    ) -> None:
        if not tickers:
            raise ValueError("tickers is required")
        if count <= 0:
            raise ValueError("count must be greater than 0")

        self.count = count
        self.quote = quote.upper()
        self.stale_after_seconds = stale_after_seconds
        self.rest_check_interval_seconds = rest_check_interval_seconds
        self.rest_timeout_seconds = rest_timeout_seconds
        self.ws_reconnect_delay_seconds = ws_reconnect_delay_seconds
        self.event_queue = event_queue
        self.on_update = on_update

        self._targets = self._build_targets(tickers, self.quote)
        self._data: dict[str, dict[str, Any]] = {
            target.key: {"asks": [], "bids": [], "updated_at": 0.0}
            for target in self._targets.values()
        }

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._ws_thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._rest_thread = threading.Thread(target=self._rest_fallback_loop, daemon=True)

        if auto_start:
            self.start()

    @staticmethod
    def _build_targets(tickers: list[str], quote: str) -> dict[str, _TickerTarget]:
        targets: dict[str, _TickerTarget] = {}

        for raw in tickers:
            normalized = raw.strip().upper()
            if not normalized:
                continue
            parts = normalized.split("-", 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError(
                    f"invalid ticker '{raw}'. expected format like 'BTC-KRW'"
                )
            base_coin, base_quote = parts[0], parts[1]
            market = f"{base_quote}-{base_coin}"
            key = base_coin.lower()

            targets[key] = _TickerTarget(key=key, market=market)

        if not targets:
            raise ValueError("no valid tickers were provided")

        return targets

    def start(self) -> None:
        if self._ws_thread.is_alive() or self._rest_thread.is_alive():
            return

        self._stop_event.clear()
        self._ws_thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._rest_thread = threading.Thread(target=self._rest_fallback_loop, daemon=True)
        self._ws_thread.start()
        self._rest_thread.start()

    def stop(self, timeout_seconds: float = 2.0) -> None:
        self._stop_event.set()
        if self._ws_thread.is_alive():
            self._ws_thread.join(timeout=timeout_seconds)
        if self._rest_thread.is_alive():
            self._rest_thread.join(timeout=timeout_seconds)

    def get_data(self, ticker: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            if ticker is None:
                return dict(self._data)

            key = ticker.lower()
            return self._data.get(key)

    def _ws_loop(self) -> None:
        if websocket is None:
            return

        subscribe_payload = [
            {"ticket": "upbit-databank"},
            {
                "type": "orderbook",
                "codes": [t.market for t in self._targets.values()],
            },
            {"format": "DEFAULT"},
        ]

        while not self._stop_event.is_set():
            ws = None
            try:
                ws = websocket.create_connection(self.WS_URL, timeout=10)
                ws.send(json.dumps(subscribe_payload))

                while not self._stop_event.is_set():
                    raw = ws.recv()
                    if not raw:
                        continue

                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")

                    try:
                        message = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if isinstance(message, dict):
                        self._handle_ws_message(message)

            except Exception:
                time.sleep(self.ws_reconnect_delay_seconds)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

    def _handle_ws_message(self, message: dict[str, Any]) -> None:
        market = str(message.get("code") or "")
        if not market or "-" not in market:
            return

        base_coin = market.split("-", 1)[1].lower()
        if base_coin not in self._targets:
            return

        orderbook_units = message.get("orderbook_units")
        asks = self._normalize_levels(orderbook_units, self.count, side="ask")
        bids = self._normalize_levels(orderbook_units, self.count, side="bid")

        if asks or bids:
            self._update_book(base_coin, asks, bids)

    def _rest_fallback_loop(self) -> None:
        while not self._stop_event.wait(self.rest_check_interval_seconds):
            now = time.time()

            stale_keys: list[str] = []
            with self._lock:
                for key, book in self._data.items():
                    if now - float(book.get("updated_at", 0.0)) >= self.stale_after_seconds:
                        stale_keys.append(key)

            if not stale_keys:
                continue

            markets = [self._targets[key].market for key in stale_keys]
            snapshots = self._fetch_rest_orderbooks(markets)

            for key in stale_keys:
                market = self._targets[key].market
                raw_book = snapshots.get(market)
                if not isinstance(raw_book, dict):
                    continue

                asks = self._normalize_levels(raw_book.get("orderbook_units"), self.count, side="ask")
                bids = self._normalize_levels(raw_book.get("orderbook_units"), self.count, side="bid")

                if asks or bids:
                    self._update_book(key, asks, bids)

    def _fetch_rest_orderbooks(self, markets: list[str]) -> dict[str, dict[str, Any]]:
        if not markets:
            return {}

        query = urllib_parse.urlencode({"markets": ",".join(markets)})
        url = f"{self.REST_ORDERBOOK_URL}?{query}"

        req = urllib_request.Request(url, method="GET")
        try:
            with urllib_request.urlopen(req, timeout=self.rest_timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except Exception:
            return {}

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}

        items: list[dict[str, Any]] = []
        if isinstance(payload, list):
            items = [x for x in payload if isinstance(x, dict)]
        elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
            items = [x for x in payload["data"] if isinstance(x, dict)]

        result: dict[str, dict[str, Any]] = {}
        for item in items:
            market = str(item.get("market") or item.get("code") or "")
            if not market:
                continue
            market = market.upper().replace("_", "-")
            result[market] = item

        return result

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_levels(
        self,
        raw_levels: Any,
        limit: int,
        *,
        side: str,
    ) -> list[dict[str, float]]:
        if not isinstance(raw_levels, list):
            return []

        levels: list[dict[str, float]] = []

        for level in raw_levels:
            if not isinstance(level, dict):
                continue

            if side == "ask":
                price = self._to_float(level.get("ask_price"))
                qty = self._to_float(level.get("ask_size"))
            else:
                price = self._to_float(level.get("bid_price"))
                qty = self._to_float(level.get("bid_size"))

            if price is None or qty is None:
                continue

            levels.append({"price": price, "qty": qty})
            if len(levels) >= limit:
                break

        return levels

    def _update_book(
        self,
        key: str,
        asks: list[dict[str, float]],
        bids: list[dict[str, float]],
    ) -> None:
        updated_at = time.time()
        with self._lock:
            current = self._data[key]
            if asks:
                current["asks"] = asks
            if bids:
                current["bids"] = bids
            current["updated_at"] = updated_at

        event = {
            "source": SOURCE_ORDERBOOK,
            "exchange": EXCHANGE_UPBIT,
            "ticker": key,
            "asks": asks,
            "bids": bids,
            "updated_at": updated_at,
        }
        if self.event_queue is not None:
            try:
                self.event_queue.put_nowait(event)
            except queue.Full:
                pass
        if callable(self.on_update):
            self.on_update(event)


class UpbitPublicWebSocket:
    WS_URL = "wss://api.upbit.com/websocket/v1"

    def __init__(
        self,
        *,
        url: str = WS_URL,
        timeout_seconds: float = 10.0,
        ping_interval_seconds: float = 30.0,
        event_queue: queue.Queue[Mapping[str, object]] | None = None,
        on_event: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        if websocket is None:
            raise RuntimeError(
                "websocket-client is required. install: pip install websocket-client"
            )
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

    def connect(self) -> None:
        if self._ws is not None:
            return
        if websocket is None:
            raise RuntimeError(
                "websocket-client is required. install: pip install websocket-client"
            )

        self._stop_event.clear()
        self._ws = websocket.create_connection(self.url, timeout=self.timeout_seconds)
        self._start_ping_loop()

    def _start_ping_loop(self) -> None:
        if self._ping_thread is not None and self._ping_thread.is_alive():
            return

        def _loop() -> None:
            while not self._stop_event.wait(self.ping_interval_seconds):
                if self._ws is None:
                    break
                try:
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

        body: list[dict[str, Any]] = [{"ticket": ticket or f"upbit-public-{uuid.uuid4()}"}]
        body.extend(request_types)
        if format_type:
            body.append({"format": format_type})

        with self._send_lock:
            self._ws.send(json.dumps(body))

    def subscribe_orderbook(
        self,
        tickers: list[str],
        *,
        level: int | None = None,
        format_type: str = "DEFAULT",
    ) -> None:
        req: dict[str, Any] = {
            "type": "orderbook",
            "codes": [_to_upbit_market_code(ticker) for ticker in tickers],
        }
        if level is not None:
            req["level"] = level
        self.send_request([req], format_type=format_type)

    def subscribe_ticker(
        self,
        tickers: list[str],
        *,
        is_only_snapshot: bool | None = None,
        is_only_realtime: bool | None = None,
        format_type: str = "DEFAULT",
    ) -> None:
        req: dict[str, Any] = {
            "type": "ticker",
            "codes": [_to_upbit_market_code(ticker) for ticker in tickers],
        }
        if is_only_snapshot is not None:
            req["is_only_snapshot"] = is_only_snapshot
        if is_only_realtime is not None:
            req["is_only_realtime"] = is_only_realtime
        self.send_request([req], format_type=format_type)

    def subscribe_trade(
        self,
        tickers: list[str],
        *,
        is_only_snapshot: bool | None = None,
        is_only_realtime: bool | None = None,
        format_type: str = "DEFAULT",
    ) -> None:
        req: dict[str, Any] = {
            "type": "trade",
            "codes": [_to_upbit_market_code(ticker) for ticker in tickers],
        }
        if is_only_snapshot is not None:
            req["is_only_snapshot"] = is_only_snapshot
        if is_only_realtime is not None:
            req["is_only_realtime"] = is_only_realtime
        self.send_request([req], format_type=format_type)

    def subscribe_candle(
        self,
        tickers: list[str],
        *,
        interval: str = "1m",
        format_type: str = "DEFAULT",
    ) -> None:
        req = {
            "type": f"candle.{interval}",
            "codes": [_to_upbit_market_code(ticker) for ticker in tickers],
        }
        self.send_request([req], format_type=format_type)

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
        event_type = msg.get("type") if isinstance(msg, dict) else "raw"
        event = {
            "source": str(event_type or "public"),
            "exchange": EXCHANGE_UPBIT,
            "event_type": event_type,
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


class UpbitMyOrder:
    PRIVATE_WS_URL = "wss://api.upbit.com/websocket/v1/private"

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        *,
        url: str = PRIVATE_WS_URL,
        timeout_seconds: float = 10.0,
        ping_interval_seconds: float = 30.0,
        event_queue: queue.Queue[Mapping[str, object]] | None = None,
        on_event: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        if websocket is None:
            raise RuntimeError(
                "websocket-client is required. install: pip install websocket-client"
            )
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

        self._ws: Any | None = None
        self._listen_thread: threading.Thread | None = None
        self._ping_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._send_lock = threading.Lock()

    @classmethod
    def from_info_yaml(cls, file_path: str = "info.yaml") -> "UpbitMyOrder":
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"{file_path} not found")

        config = _parse_simple_yaml_mapping(path.read_text(encoding="utf-8"))
        api_key = config.get("upbit_api_key")
        secret_key = config.get("upbit_secret_key")
        if not api_key or not secret_key:
            raise ValueError("info.yaml must include 'upbit_api_key' and 'upbit_secret_key'")

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
        if websocket is None:
            raise RuntimeError(
                "websocket-client is required. install: pip install websocket-client"
            )

        self._stop_event.clear()
        self._ws = websocket.create_connection(
            self.url,
            timeout=self.timeout_seconds,
            header=[f"Authorization: {self._build_authorization_header()}"],
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

        body: list[dict[str, Any]] = [{"ticket": ticket or f"upbit-myorder-{uuid.uuid4()}"}]
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
    ) -> None:
        codes: list[str] = []
        if tickers:
            codes = [_to_upbit_market_code(ticker) for ticker in tickers]

        req: dict[str, Any] = {"type": "myOrder"}
        if tickers is not None:
            req["codes"] = codes

        self.send_request([req], format_type=format_type)

    def subscribe_my_asset(self, *, format_type: str = "DEFAULT") -> None:
        self.send_request([{"type": "myAsset"}], format_type=format_type)

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
            "exchange": EXCHANGE_UPBIT,
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
    q: queue.Queue[Mapping[str, object]] = queue.Queue(maxsize=1000)
    bank = UpbitDataBank(["btc-krw", "eth-krw"], count=5, event_queue=q)

    mode = "orderbook"
    try:
        import sys

        if len(sys.argv) > 1:
            mode = sys.argv[1].strip().lower()
    except Exception:
        mode = "orderbook"

    trade: UpbitMyOrder | None = None
    try:
        if mode in {"myorder", "mytrade"}:
            trade = UpbitMyOrder.from_info_yaml("info.yaml")
            trade.event_queue = q
            trade.connect()
            trade.subscribe_my_order(tickers=None)
            trade.start_listen()

        started = time.time()
        while time.time() - started < 10:
            try:
                print(q.get(timeout=1))
            except queue.Empty:
                pass
    finally:
        if trade is not None:
            trade.close()
        bank.stop()
