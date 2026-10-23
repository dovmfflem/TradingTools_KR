"""Single-connection spot depth readers; the application owns receive loops.

Connect deadline: existing open_public_stream's 10 seconds. Receive/send/close
deadline: timeout_seconds. No listener thread, sleep, retry or REST fallback.
"""
import math
import json
import re
import time

from .bithumb.bithumb_websocket import BithumbPublicWebSocket
from .stream_transport import open_public_stream, spot_depth_quote
from .stream_transport import _connect
from .public_orderbook import URLS, subscription, normalize_orderbook
from .spot_trading import decimal
from .api_error import ExchangeRequestError, safe_code

__all__ = ["open_spot_depth_stream"]


class _NativeDepthStream:
    """Single-socket snapshots; no worker threads, reconnects or REST fallback.

    Coinone JSON ping at 20s with 15s PONG deadline, driven only by recv().
    DigitalX uses native websocket control frames, not application JSON pings.
    Caller must keep invoking recv and enforce its own first-book/stale deadline.
    """
    def __init__(self, exchange, socket, markets, timeout_seconds):
        self.exchange, self._ws = exchange, socket
        self._markets = frozenset(markets)
        self.timeout_seconds = timeout_seconds
        self._ping_at = time.monotonic() + 20
        self._pong_deadline = None

    def recv(self):
        from websocket import WebSocketTimeoutException
        if self._ws is None:
            raise ConnectionError("SPOT_PUBLIC_STREAM_CLOSED")
        now = time.monotonic()
        if self.exchange == "coinone":
            if self._pong_deadline is not None and now >= self._pong_deadline:
                raise TimeoutError("COINONE_PONG_TIMED_OUT")
            if self._pong_deadline is None and now >= self._ping_at:
                self._ws.send(json.dumps({"request_type": "PING"}))
                self._pong_deadline = now + 15
        try:
            raw = self._ws.recv()
        except WebSocketTimeoutException:
            return None
        received_at = time.time()
        if raw in ("", b""):
            raise ConnectionError("SPOT_PUBLIC_STREAM_CLOSED")
        try:
            message = json.loads(raw)
        except (ValueError, TypeError, UnicodeError):
            raise ValueError("INVALID_BOOK_MESSAGE") from None
        if not isinstance(message, dict):
            return None
        if message.get("error") or message.get("success") is False or message.get("response_type", message.get("r")) == "ERROR":
            raise ExchangeRequestError(self.exchange, safe_code(message))
        if self.exchange == "coinone":
            if message.get("response_type", message.get("r")) == "PONG":
                self._pong_deadline = None
                self._ping_at = time.monotonic() + 20
                return None
            if message.get("response_type", message.get("r")) != "DATA" or message.get("channel", message.get("c")) != "ORDERBOOK":
                return None
            data = message.get("data", message.get("d"))
            if not isinstance(data, dict):
                raise ValueError("INVALID_BOOK_MESSAGE")
            market = f'{data.get("quote_currency", data.get("qc", ""))}-{data.get("target_currency", data.get("tc", ""))}'.upper()
            # The public feed may use lowercase currencies. Canonicalize only
            # identity before reusing the shared strict book normalizer.
            data = {**data, "quote_currency": market.split("-", 1)[0],
                    "target_currency": market.split("-", 1)[1]}
            message = {**message, "data": data}
            stamp = data.get("timestamp", data.get("t"))
        else:
            if message.get("type") != "orderbook":
                return None
            market = "-".join(reversed(str(message.get("symbol", "")).upper().split("_")))
            data = message.get("data")
            if not isinstance(data, dict):
                raise ValueError("INVALID_BOOK_MESSAGE")
            # Outer DigitalX timestamp is server send time, not book freshness.
            stamp = data.get("timestamp")
        if market not in self._markets:
            return None
        normalized = {}
        for side, short in (("asks", "a"), ("bids", "b")):
            rows = data.get(side, data.get(short))
            if not isinstance(rows, list):
                raise ValueError("INVALID_BOOK_LEVELS")
            levels = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("INVALID_BOOK_LEVEL")
                price, qty = decimal(row.get("price", row.get("p"))), decimal(row.get("qty", row.get("q")))
                if price <= 0 or qty < 0:
                    raise ValueError("INVALID_BOOK_LEVEL")
                if qty > 0:
                    levels.append({"price": float(price), "qty": float(qty)})
            normalized[side] = sorted(levels, key=lambda r: r["price"], reverse=side == "bids")[:15]
        normalize_orderbook(self.exchange, message, market, observed_at=0)
        stamp = decimal(stamp) if stamp is not None else decimal(0)
        if stamp < 0:
            raise ValueError("INVALID_BOOK_TIMESTAMP")
        return {"ticker": market[4:], "market": market, "exchange": self.exchange,
                "source": "orderbook", **normalized, "sourceTimestampMs": int(stamp),
                "receivedAtMs": int(received_at * 1000), "updated_at": received_at}

    def close(self):
        socket, self._ws = self._ws, None
        if socket is not None:
            socket.close(timeout=self.timeout_seconds)


class _SpotDepthStream:
    def __init__(self, protocol, markets, timeout_seconds):
        self._protocol = protocol
        self._markets = frozenset(markets)
        self.timeout_seconds = timeout_seconds

    def recv(self):
        if self._protocol._ws is None:
            raise ConnectionError("BITHUMB_PUBLIC_STREAM_CLOSED")
        message = self._protocol.recv_once()
        received_at = time.time()
        if message is None:
            return None
        if isinstance(message, str):
            if message.strip().lower() in {"ping", "pong"}:
                return None
            if not message:
                raise ConnectionError("BITHUMB_PUBLIC_STREAM_CLOSED")
            raise ValueError("INVALID_BOOK_MESSAGE")
        if not isinstance(message, dict):
            return None
        quote = spot_depth_quote("bithumb", message, depth=15)
        if quote is None:
            return None
        market = message.get("code", message.get("cd"))
        if not isinstance(market, str) or market not in self._markets:
            return None
        def levels(side):
            return [{"price": row["price"], "qty": row["size"]} for row in quote[side]]
        return {"ticker": market.split("-", 1)[1], "market": market, "exchange": "bithumb",
                "source": "orderbook", "asks": levels("asks"), "bids": levels("bids"),
                "sourceTimestampMs": quote["sourceTimestampMs"],
                "receivedAtMs": int(received_at * 1000), "updated_at": received_at}

    def close(self):
        socket = self._protocol._ws
        self._protocol._ws = None
        if socket is not None:
            socket.close(timeout=self.timeout_seconds)


def open_spot_depth_stream(exchange, symbols, *, timeout_seconds=1.0):
    """Open Bithumb/Coinone/DigitalX KRW depth (digitalx aliases korbit).

    All venues accept 1..600 symbols (local bound, not a claimed venue limit).
    Coinone sends one subscription per market; DigitalX sends one batch.
    Connection deadline 10s; each send/receive/close uses timeout_seconds.

    recv() returns one normalized dict or None for control/unrelated frames and
    receive timeout. Closed/broken connections and invalid books raise; callers
    close and decide whether to reconnect. Missing source timestamps stay zero.
    """
    if not isinstance(exchange, str):
        raise ValueError("unsupported spot depth exchange")
    exchange = exchange.strip().lower()
    exchange = "korbit" if exchange == "digitalx" else exchange
    if exchange not in {"bithumb", "coinone", "korbit"}:
        raise ValueError("unsupported spot depth exchange")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and greater than zero")
    if not isinstance(symbols, (list, tuple)) or not 1 <= len(symbols) <= 600:
        raise ValueError("symbols must contain 1..600 base symbols or KRW market codes")
    markets = []
    for symbol in symbols:
        if not isinstance(symbol, str):
            raise ValueError("invalid spot depth symbol")
        value = symbol.strip().upper()
        market = value if value.startswith("KRW-") else f"KRW-{value}"
        if not re.fullmatch(r"KRW-[A-Z0-9]{1,20}", market):
            raise ValueError("invalid spot depth symbol")
        markets.append(market)
    markets = list(dict.fromkeys(markets))
    if exchange != "bithumb":
        requests = [subscription(exchange, market) for market in markets]
        if exchange == "korbit":
            requests = [[{**requests[0][0], "symbols": [r[0]["symbols"][0] for r in requests]}]]
        socket = _connect(URLS[exchange])
        stream = _NativeDepthStream(exchange, socket, markets, timeout_seconds)
        try:
            socket.settimeout(timeout_seconds)
            for request in requests:
                socket.send(json.dumps(request))
        except Exception:
            try:
                stream.close()
            except Exception:
                pass
            raise
        return stream
    protocol = BithumbPublicWebSocket(timeout_seconds=timeout_seconds)
    protocol._ws = open_public_stream("bithumb")
    stream = _SpotDepthStream(protocol, markets, timeout_seconds)
    try:
        protocol._ws.settimeout(timeout_seconds)
        protocol.subscribe_orderbook([f"{market[4:]}-krw" for market in markets], format_type="DEFAULT")
    except Exception:
        try:
            stream.close()
        except Exception:
            pass
        raise
    return stream
