from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from src.core.event_constants import EXCHANGE_BINANCE_FUTURES, SOURCE_ORDERBOOK

try:
    import websocket
except ModuleNotFoundError:
    websocket = None  # type: ignore[assignment]


@dataclass(frozen=True)
class _TickerTarget:
    key: str
    symbol: str
    stream: str


class BinanceFuturesDataBank:
    """
    Maintain latest Binance Futures orderbooks.

    - Primary source: WebSocket depth stream
    - Fallback: REST depth if no data is received for a ticker in `stale_after_seconds`
    """

    WS_BASE_URL = "wss://fstream.binance.com/stream?streams="
    REST_DEPTH_URL = "https://fapi.binance.com/fapi/v1/depth"

    def __init__(
        self,
        tickers: list[str],
        *,
        count: int = 5,
        quote: str = "USDT",
        stale_after_seconds: float = 5.0,
        rest_check_interval_seconds: float = 1.0,
        rest_timeout_seconds: float = 5.0,
        ws_reconnect_delay_seconds: float = 1.0,
        event_queue: queue.Queue | None = None,
        on_update: Any | None = None,
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
                    f"invalid ticker '{raw}'. expected format like 'BTC-USDT'"
                )
            base_coin, base_quote = parts[0], parts[1]
            symbol = f"{base_coin}{base_quote}"
            key = base_coin.lower()

            targets[key] = _TickerTarget(
                key=key,
                symbol=symbol,
                stream=f"{symbol.lower()}@depth20@100ms",
            )

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

        stream_path = "/".join(t.stream for t in self._targets.values())
        ws_url = f"{self.WS_BASE_URL}{stream_path}"

        while not self._stop_event.is_set():
            ws = None
            try:
                ws = websocket.create_connection(ws_url, timeout=10)

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
        data = message.get("data") if isinstance(message.get("data"), dict) else message
        if not isinstance(data, dict):
            return

        symbol = str(data.get("s") or "").upper()
        if not symbol:
            return

        key = None
        for target in self._targets.values():
            if target.symbol == symbol:
                key = target.key
                break
        if key is None:
            return

        asks = self._normalize_levels(data.get("a"), self.count)
        bids = self._normalize_levels(data.get("b"), self.count)

        if asks or bids:
            self._update_book(key, asks, bids)

    def _rest_fallback_loop(self) -> None:
        while not self._stop_event.wait(self.rest_check_interval_seconds):
            now = time.time()

            stale_keys: list[str] = []
            with self._lock:
                for key, book in self._data.items():
                    if now - float(book.get("updated_at", 0.0)) >= self.stale_after_seconds:
                        stale_keys.append(key)

            for key in stale_keys:
                symbol = self._targets[key].symbol
                snapshot = self._fetch_rest_orderbook(symbol)
                if not isinstance(snapshot, dict):
                    continue

                asks = self._normalize_levels(snapshot.get("asks"), self.count)
                bids = self._normalize_levels(snapshot.get("bids"), self.count)

                if asks or bids:
                    self._update_book(key, asks, bids)

    def _fetch_rest_orderbook(self, symbol: str) -> dict[str, Any]:
        limit = self._rest_limit(self.count)
        query = urllib_parse.urlencode({"symbol": symbol, "limit": str(limit)})
        url = f"{self.REST_DEPTH_URL}?{query}"

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

        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _rest_limit(count: int) -> int:
        valid_limits = [5, 10, 20, 50, 100, 500, 1000]
        for limit in valid_limits:
            if count <= limit:
                return limit
        return 1000

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_levels(self, raw_levels: Any, limit: int) -> list[dict[str, float]]:
        if not isinstance(raw_levels, list):
            return []

        levels: list[dict[str, float]] = []
        for level in raw_levels:
            price: float | None = None
            qty: float | None = None

            if isinstance(level, list) and len(level) >= 2:
                price = self._to_float(level[0])
                qty = self._to_float(level[1])
            elif isinstance(level, dict):
                price = self._to_float(level.get("price"))
                qty = self._to_float(level.get("qty") or level.get("quantity"))

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
            "exchange": EXCHANGE_BINANCE_FUTURES,
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


if __name__ == "__main__":
    q: queue.Queue = queue.Queue(maxsize=1000)
    bank = BinanceFuturesDataBank(
        ["btc-usdt", "eth-usdt"],
        quote="USDT",
        count=5,
        event_queue=q,
    )
    try:
        started = time.time()
        while time.time() - started < 5:
            try:
                print(q.get(timeout=1))
            except queue.Empty:
                pass
    finally:
        bank.stop()
