"""Single-connection Bithumb depth reader; the application owns its receive loop.

Connect deadline: existing open_public_stream's 10 seconds. Receive/send/close
deadline: timeout_seconds. No listener thread, sleep, retry or REST fallback.
"""
import math
import re
import time

from .bithumb.bithumb_websocket import BithumbPublicWebSocket
from .stream_transport import open_public_stream, spot_depth_quote

__all__ = ["open_spot_depth_stream"]


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
    """Open Bithumb KRW depth for 1..600 base symbols or canonical KRW codes.

    recv() returns one normalized dict or None for control/unrelated frames and
    receive timeout. Closed/broken connections and invalid books raise; callers
    close and decide whether to reconnect. Missing source timestamps stay zero.
    """
    if not isinstance(exchange, str) or exchange.strip().lower() != "bithumb":
        raise ValueError("unsupported spot depth exchange; currently bithumb only")
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
