from __future__ import annotations

import json
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import requests

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[3]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from src.core.event_constants import EXCHANGE_LIGHTER, SOURCE_ORDERBOOK

try:
    import websocket
except ModuleNotFoundError:
    websocket = None  # type: ignore[assignment]


@dataclass(frozen=True)
class _MarketTarget:
    key: str
    market_id: int


class LighterDataBank:
    WS_URL = "wss://mainnet.zklighter.elliot.ai/stream"
    WS_READONLY_URL = "wss://mainnet.zklighter.elliot.ai/stream?readonly=true"

    def __init__(
        self,
        market_ids: list[int | str],
        *,
        count: int = 5,
        ws_read_timeout_seconds: float = 10.0,
        ws_no_data_reconnect_seconds: float = 10.0,
        ws_reconnect_delay_seconds: float = 1.0,
        event_queue: queue.Queue[object] | None = None,
        on_update: Any | None = None,
        readonly: bool = True,
        auto_start: bool = True,
    ) -> None:
        if not market_ids:
            raise ValueError("market_ids is required")
        if count <= 0:
            raise ValueError("count must be greater than 0")

        self.count = count
        self.ws_read_timeout_seconds = ws_read_timeout_seconds
        self.ws_no_data_reconnect_seconds = ws_no_data_reconnect_seconds
        self.ws_reconnect_delay_seconds = ws_reconnect_delay_seconds
        self.event_queue = event_queue
        self.on_update = on_update
        self.readonly = readonly

        self._targets = self._build_targets(market_ids)
        self._data: dict[str, dict[str, Any]] = {
            target.key: {"asks": [], "bids": [], "updated_at": 0.0, "offset": 0}
            for target in self._targets.values()
        }

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._ws_thread = threading.Thread(target=self._ws_loop, daemon=True)

        if auto_start:
            self.start()

    @staticmethod
    def _build_targets(market_ids: list[int | str]) -> dict[str, _MarketTarget]:
        targets: dict[str, _MarketTarget] = {}
        for raw_market_id in market_ids:
            market_text = str(raw_market_id).strip()
            if not market_text:
                continue
            try:
                market_id = int(market_text)
            except ValueError as exc:
                raise ValueError(f"invalid market_id '{raw_market_id}'") from exc

            key = str(market_id)
            targets[key] = _MarketTarget(key=key, market_id=market_id)

        if not targets:
            raise ValueError("no valid market ids were provided")
        return targets

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

    def get_data(self, market_id: int | str | None = None) -> dict[str, Any] | None:
        with self._lock:
            if market_id is None:
                return dict(self._data)
            return self._data.get(str(market_id).strip())

    def _ws_loop(self) -> None:
        if websocket is None:
            return

        ws_url = self.WS_READONLY_URL if self.readonly else self.WS_URL
        while not self._stop_event.is_set():
            ws = None
            try:
                ws = websocket.create_connection(ws_url, timeout=self.ws_read_timeout_seconds)
                last_data_at = time.time()
                while not self._stop_event.is_set():
                    try:
                        raw = ws.recv()
                    except Exception as exc:
                        if exc.__class__.__name__ == "WebSocketTimeoutException":
                            now = time.time()
                            if now - last_data_at >= self.ws_no_data_reconnect_seconds:
                                break
                            try:
                                ws.ping("keepalive")
                            except Exception:
                                break
                            continue
                        raise
                    if not raw:
                        continue

                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")

                    try:
                        message = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if not isinstance(message, dict):
                        continue
                    if message.get("type") == "connected":
                        self._subscribe_all(ws)
                        continue
                    if message.get("type") == "ping":
                        ws.send(json.dumps({"type": "pong"}))
                        continue
                    if message.get("type") == "pong":
                        continue

                    self._handle_ws_message(message)
                    if message.get("type") in {"subscribed/order_book", "update/order_book"}:
                        last_data_at = time.time()

            except Exception:
                time.sleep(self.ws_reconnect_delay_seconds)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

    def _subscribe_all(self, ws: Any) -> None:
        for target in self._targets.values():
            payload = {
                "type": "subscribe",
                "channel": f"order_book/{target.market_id}",
            }
            ws.send(json.dumps(payload))

    def _handle_ws_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type") or "")
        if message_type not in {"subscribed/order_book", "update/order_book"}:
            return

        market_key = self._extract_market_key(message)
        if market_key is None or market_key not in self._targets:
            return

        order_book = message.get("order_book")
        if not isinstance(order_book, dict):
            return

        asks = self._normalize_levels(order_book.get("asks"), self.count)
        bids = self._normalize_levels(order_book.get("bids"), self.count)
        offset_value = self._to_int(message.get("offset") or order_book.get("offset"))

        with self._lock:
            current = self._data[market_key]
            if message_type == "subscribed/order_book":
                current["asks"] = asks
                current["bids"] = bids
            else:
                current["asks"] = self._merge_levels(current.get("asks"), asks, ascending=True, limit=self.count)
                current["bids"] = self._merge_levels(current.get("bids"), bids, ascending=False, limit=self.count)

            current["updated_at"] = time.time()
            if offset_value is not None:
                current["offset"] = offset_value
            emit_asks = list(current.get("asks") or [])
            emit_bids = list(current.get("bids") or [])
            emit_offset = int(current.get("offset") or 0)
            emit_updated_at = float(current.get("updated_at") or 0.0)

        event = {
            "source": SOURCE_ORDERBOOK,
            "exchange": EXCHANGE_LIGHTER,
            "ticker": market_key,
            "asks": emit_asks,
            "bids": emit_bids,
            "offset": emit_offset,
            "updated_at": emit_updated_at,
        }
        if self.event_queue is not None:
            try:
                self.event_queue.put_nowait(event)
            except queue.Full:
                pass
        if callable(self.on_update):
            self.on_update(event)

    @staticmethod
    def _extract_market_key(message: dict[str, Any]) -> str | None:
        channel = str(message.get("channel") or "")
        if ":" in channel:
            right = channel.split(":", 1)[1].strip()
            return right if right else None
        if "/" in channel:
            right = channel.rsplit("/", 1)[1].strip()
            return right if right else None
        return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _normalize_levels(self, raw_levels: Any, limit: int) -> list[dict[str, float]]:
        if not isinstance(raw_levels, list):
            return []
        levels: list[dict[str, float]] = []
        for item in raw_levels:
            if not isinstance(item, dict):
                continue
            price = self._to_float(item.get("price"))
            size = self._to_float(item.get("size"))
            if price is None or size is None:
                continue
            levels.append({"price": price, "qty": size})
            if len(levels) >= limit:
                break
        return levels

    @staticmethod
    def _merge_levels(
        existing_levels: Any,
        delta_levels: list[dict[str, float]],
        *,
        ascending: bool,
        limit: int,
    ) -> list[dict[str, float]]:
        merged_map: dict[float, float] = {}

        if isinstance(existing_levels, list):
            for item in existing_levels:
                if not isinstance(item, dict):
                    continue
                price = item.get("price")
                qty = item.get("qty")
                if isinstance(price, (int, float)) and isinstance(qty, (int, float)) and qty > 0:
                    merged_map[float(price)] = float(qty)

        for item in delta_levels:
            price = item.get("price")
            qty = item.get("qty")
            if not isinstance(price, (int, float)) or not isinstance(qty, (int, float)):
                continue
            if qty <= 0:
                merged_map.pop(float(price), None)
            else:
                merged_map[float(price)] = float(qty)

        sorted_prices = sorted(merged_map.keys(), reverse=not ascending)
        result: list[dict[str, float]] = []
        for price in sorted_prices[:limit]:
            result.append({"price": price, "qty": merged_map[price]})
        return result
    
def market_id():
    BASE = "https://mainnet.zklighter.elliot.ai/api/v1"
    r = requests.get(f"{BASE}/orderBooks", timeout=10)
    r.raise_for_status()
    data = r.json()

    order_books = data.get("order_books", [])
    print("count:", len(order_books))

    for m in order_books:
        symbol = m.get("symbol")
        market_id = m.get("market_id") or m.get("marketId")  # 혹시 camelCase로 올 수도 있어서
        print(f"Symbol: {symbol}, Market ID: {market_id}")
        


if __name__ == "__main__":
    q: queue.Queue[object] = queue.Queue(maxsize=1000)
    bank = LighterDataBank([7], count=5, event_queue=q, readonly=True)
    try:
        started = time.time()
        while time.time() - started < 10:
            try:
                print(q.get(timeout=1))
            except queue.Empty:
                pass
    finally:
        bank.stop()
