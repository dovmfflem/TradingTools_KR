from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from src.core.event_constants import EXCHANGE_BITHUMB, SOURCE_ORDERBOOK

try:
    import websocket
except ModuleNotFoundError:
    websocket = None  # type: ignore[assignment]


@dataclass(frozen=True)
class _TickerTarget:
    key: str
    rest_market: str
    ws_symbol: str


class BithumbDataBank:
    """
    Maintain latest Bithumb orderbooks.

    - Primary source: WebSocket
    - Fallback: REST batch request if no data is received for a ticker in `stale_after_seconds`
    """

    WS_URL = "wss://pubwss.bithumb.com/pub/ws"
    REST_ORDERBOOK_URL = "https://api.bithumb.com/v1/orderbook"

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
        event_queue: queue.Queue[dict[str, Any]] | None = None,
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
                    f"invalid ticker '{raw}'. expected format like 'BTC-KRW'"
                )
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

            key = ticker.lower()
            return self._data.get(key)

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

        if not asks and not bids:
            return

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
        side: str | None = None,
    ) -> list[dict[str, float]]:
        if not isinstance(raw_levels, list):
            return []

        levels: list[dict[str, float]] = []

        for level in raw_levels:
            price: float | None = None
            qty: float | None = None

            if isinstance(level, dict):
                if side == "ask":
                    price = self._to_float(
                        level.get("ask_price")
                        if "ask_price" in level
                        else level.get("price")
                    )
                    qty = self._to_float(
                        level.get("ask_size")
                        if "ask_size" in level
                        else level.get("quantity") or level.get("qty")
                    )
                elif side == "bid":
                    price = self._to_float(
                        level.get("bid_price")
                        if "bid_price" in level
                        else level.get("price")
                    )
                    qty = self._to_float(
                        level.get("bid_size")
                        if "bid_size" in level
                        else level.get("quantity") or level.get("qty")
                    )
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


if __name__ == "__main__":
    bank = BithumbDataBank(["btc-krw", "eth-krw"], count=5)
    try:
        time.sleep(3)
        snapshot = bank.get_data()
        print(snapshot)
        if isinstance(snapshot, dict):
            btc_book = snapshot.get("btc")
            if isinstance(btc_book, dict):
                asks = btc_book.get("asks")
                if isinstance(asks, list) and asks and isinstance(asks[0], dict):
                    print(asks[0].get("price"), asks[0].get("qty"))
    finally:
        bank.stop()
