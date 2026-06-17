from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request

os.environ.setdefault("CRYPTOGRAPHY_OPENSSL_NO_LEGACY", "1")

import jwt
from src.core.event_constants import EXCHANGE_BITHUMB, SOURCE_MYTRADE, SOURCE_ORDERBOOK

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


class BithumbPublicWebSocket:
    PUBLIC_WS_URL = "wss://ws-api.bithumb.com/websocket/v1"

    def __init__(
        self,
        *,
        url: str = PUBLIC_WS_URL,
        timeout_seconds: float = 10.0,
        event_queue: queue.Queue[dict[str, Any]] | None = None,
        on_event: Any | None = None,
    ) -> None:
        if websocket is None:
            raise RuntimeError("websocket-client is required. install: pip install websocket-client")

        self.url = url
        self.timeout_seconds = timeout_seconds
        self.event_queue = event_queue
        self.on_event = on_event
        self._ws: Any | None = None
        self._listen_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._send_lock = threading.Lock()

    @staticmethod
    def _codes(tickers: list[str] | tuple[str, ...] | str) -> list[str]:
        values = [tickers] if isinstance(tickers, str) else list(tickers)
        if not values:
            raise ValueError("tickers is required")
        return [_to_bithumb_market_code(ticker) for ticker in values]

    def connect(self) -> None:
        if self._ws is not None:
            return
        ws_module = websocket
        if ws_module is None:
            raise RuntimeError("websocket-client is required. install: pip install websocket-client")

        self._stop_event.clear()
        self._ws = ws_module.create_connection(self.url, timeout=self.timeout_seconds)

    def close(self) -> None:
        self._stop_event.set()
        if self._listen_thread is not None and self._listen_thread.is_alive():
            self._listen_thread.join(timeout=2.0)
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

        body: list[dict[str, Any]] = [{"ticket": ticket or f"public-{uuid.uuid4()}"}]
        body.extend(request_types)
        if format_type:
            body.append({"format": format_type})

        with self._send_lock:
            self._ws.send(json.dumps(body))

    def subscribe_orderbook(
        self,
        tickers: list[str] | tuple[str, ...] | str,
        *,
        format_type: str = "DEFAULT",
        only_snapshot: bool = False,
        only_realtime: bool = False,
    ) -> None:
        self._subscribe_market_stream(
            "orderbook",
            tickers,
            format_type=format_type,
            only_snapshot=only_snapshot,
            only_realtime=only_realtime,
        )

    def subscribe_ticker(
        self,
        tickers: list[str] | tuple[str, ...] | str,
        *,
        format_type: str = "DEFAULT",
        only_snapshot: bool = False,
        only_realtime: bool = False,
    ) -> None:
        self._subscribe_market_stream(
            "ticker",
            tickers,
            format_type=format_type,
            only_snapshot=only_snapshot,
            only_realtime=only_realtime,
        )

    def subscribe_trade(
        self,
        tickers: list[str] | tuple[str, ...] | str,
        *,
        format_type: str = "DEFAULT",
        only_snapshot: bool = False,
        only_realtime: bool = False,
    ) -> None:
        self._subscribe_market_stream(
            "trade",
            tickers,
            format_type=format_type,
            only_snapshot=only_snapshot,
            only_realtime=only_realtime,
        )

    def subscribe_candle(
        self,
        tickers: list[str] | tuple[str, ...] | str,
        *,
        interval: str = "1m",
        format_type: str = "DEFAULT",
        only_snapshot: bool = False,
        only_realtime: bool = False,
    ) -> None:
        self._subscribe_market_stream(
            f"candle.{interval}",
            tickers,
            format_type=format_type,
            only_snapshot=only_snapshot,
            only_realtime=only_realtime,
        )

    def _subscribe_market_stream(
        self,
        stream_type: str,
        tickers: list[str] | tuple[str, ...] | str,
        *,
        format_type: str,
        only_snapshot: bool,
        only_realtime: bool,
    ) -> None:
        req: dict[str, Any] = {
            "type": stream_type,
            "codes": self._codes(tickers),
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
            "source": "bithumb_public_websocket",
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


@dataclass(frozen=True)
class _TickerTarget:
    key: str
    rest_market: str
    ws_symbol: str


class BithumbDataBank:
    WS_URL = "wss://pubwss.bithumb.com/pub/ws"
    REST_ORDERBOOK_URL = "https://api.bithumb.com/v1/orderbook"

    def __init__(
        self,
        tickers: list[str],
        *,
        count: int = 5,
        stale_after_seconds: float = 5.0,
        rest_check_interval_seconds: float = 1.0,
        rest_timeout_seconds: float = 5.0,
        ws_reconnect_delay_seconds: float = 1.0,
        event_queue: queue.Queue[dict[str, Any]] | None = None,
        on_update: Any | None = None,
        auto_start: bool = True,
    ) -> None:
        if not tickers:
            raise ValueError("tickers is required")
        if count <= 0:
            raise ValueError("count must be greater than 0")

        self.count = count
        self.stale_after_seconds = stale_after_seconds
        self.rest_check_interval_seconds = rest_check_interval_seconds
        self.rest_timeout_seconds = rest_timeout_seconds
        self.ws_reconnect_delay_seconds = ws_reconnect_delay_seconds
        self.event_queue = event_queue
        self.on_update = on_update

        self._targets = self._build_targets(tickers)
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
    def _build_targets(tickers: list[str]) -> dict[str, _TickerTarget]:
        targets: dict[str, _TickerTarget] = {}

        for raw in tickers:
            normalized = raw.strip().upper()
            if not normalized:
                continue
            parts = normalized.split("-", 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError(f"invalid ticker '{raw}'. expected format like 'BTC-KRW'")

            base_coin, base_quote = parts[0], parts[1]
            rest_market = f"{base_quote}-{base_coin}"
            ws_symbol = f"{base_coin}_{base_quote}"
            key = base_coin.lower()
            targets[key] = _TickerTarget(key=key, rest_market=rest_market, ws_symbol=ws_symbol)

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
            return self._data.get(ticker.lower())

    def _ws_loop(self) -> None:
        if websocket is None:
            return

        subscribe_payload = {
            "type": "orderbooksnapshot",
            "symbols": [t.ws_symbol for t in self._targets.values()],
        }

        while not self._stop_event.is_set():
            ws = None
            try:
                ws = websocket.create_connection(self.WS_URL, timeout=10)
                ws.send(json.dumps(subscribe_payload))

                while not self._stop_event.is_set():
                    raw = ws.recv()
                    if not raw:
                        continue
                    try:
                        message = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
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
        content = message.get("content")
        if not isinstance(content, dict):
            return

        symbol = str(content.get("symbol") or "")
        if not symbol or "_" not in symbol:
            return

        base_coin = symbol.split("_", 1)[0].lower()
        if base_coin not in self._targets:
            return

        asks = self._normalize_levels(content.get("asks"), self.count)
        bids = self._normalize_levels(content.get("bids"), self.count)
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

            markets = [self._targets[key].rest_market for key in stale_keys]
            snapshots = self._fetch_rest_orderbooks(markets)

            for key in stale_keys:
                market = self._targets[key].rest_market
                raw_book = snapshots.get(market)
                if not isinstance(raw_book, dict):
                    continue

                asks_raw = raw_book.get("orderbook_units") or raw_book.get("asks")
                bids_raw = raw_book.get("orderbook_units") or raw_book.get("bids")

                asks = self._normalize_levels(asks_raw, self.count, side="ask")
                bids = self._normalize_levels(bids_raw, self.count, side="bid")
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
            market = str(item.get("market") or item.get("symbol") or "")
            if not market:
                continue
            result[market.upper().replace("_", "-")] = item

        return result

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_levels(self, raw_levels: Any, limit: int, *, side: str | None = None) -> list[dict[str, float]]:
        if not isinstance(raw_levels, list):
            return []

        levels: list[dict[str, float]] = []
        for level in raw_levels:
            price: float | None = None
            qty: float | None = None

            if isinstance(level, dict):
                if side == "ask":
                    price = self._to_float(level.get("ask_price") if "ask_price" in level else level.get("price"))
                    qty = self._to_float(level.get("ask_size") if "ask_size" in level else level.get("quantity") or level.get("qty"))
                elif side == "bid":
                    price = self._to_float(level.get("bid_price") if "bid_price" in level else level.get("price"))
                    qty = self._to_float(level.get("bid_size") if "bid_size" in level else level.get("quantity") or level.get("qty"))
                else:
                    price = self._to_float(level.get("price"))
                    qty = self._to_float(level.get("quantity") or level.get("qty"))
            elif isinstance(level, list) and len(level) >= 2:
                price = self._to_float(level[0])
                qty = self._to_float(level[1])

            if price is None or qty is None:
                continue

            levels.append({"price": price, "qty": qty})
            if len(levels) >= limit:
                break

        return levels

    def _update_book(self, key: str, asks: list[dict[str, float]], bids: list[dict[str, float]]) -> None:
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
            "exchange": EXCHANGE_BITHUMB,
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

        self._ws: Any | None = None
        self._listen_thread: threading.Thread | None = None
        self._ping_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._send_lock = threading.Lock()

    @classmethod
    def from_info_yaml(cls, file_path: str = "info.yaml") -> "BithumbMyOrder":
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
        ws_module = websocket
        if ws_module is None:
            raise RuntimeError("websocket-client is required. install: pip install websocket-client")

        self._stop_event.clear()
        self._ws = ws_module.create_connection(
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

    def send_request(self, request_types: list[dict[str, Any]], *, ticket: str | None = None, format_type: str = "DEFAULT") -> None:
        if self._ws is None:
            raise RuntimeError("websocket is not connected")

        body: list[dict[str, Any]] = [{"ticket": ticket or f"myorder-{uuid.uuid4()}"}]
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

    def subscribe_my_asset(
        self,
        *,
        format_type: str = "DEFAULT",
        only_snapshot: bool = False,
        only_realtime: bool = False,
    ) -> None:
        req: dict[str, Any] = {"type": "myAsset"}
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
    q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1000)
    bank = BithumbDataBank(["btc-krw"], event_queue=q)
    trade = BithumbMyOrder.from_info_yaml("info.yaml")
    trade.event_queue = q
    trade.connect()
    trade.subscribe_my_order(tickers=None)
    trade.start_listen()

    started = time.time()
    try:
        while time.time() - started < 10:
            try:
                print(q.get(timeout=1))
            except queue.Empty:
                pass
    finally:
        trade.close()
        bank.stop()
