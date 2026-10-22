"""Raw exchange streams for applications that own normalization and reconnects.

Connections have a 10s deadline. There is no implicit reconnect or REST fallback.
Callers own bounded receive/heartbeat policy and must close the returned socket.
"""
import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from urllib.parse import urlencode

PUBLIC_URLS = {
    "upbit": "wss://api.upbit.com/websocket/v1",
    "bithumb": "wss://ws-api.bithumb.com/websocket/v1",
    "binance_futures": "wss://fstream.binance.com/stream?streams=",
}
PRIVATE_URLS = {
    "upbit": "wss://api.upbit.com/websocket/v1/private",
    "bithumb": "wss://ws-api.bithumb.com/websocket/v2/private",
    "coinone": "wss://stream.coinone.co.kr/v1/private",
}


def _connect(url, *, headers=None, connect=None, suppress_origin=False):
    import websocket
    kwargs = {"timeout": 10}
    if suppress_origin:
        kwargs["suppress_origin"] = True
    if headers is not None:
        kwargs["header"] = headers
    return (connect or websocket.create_connection)(url, **kwargs)


def open_public_stream(exchange, *, streams=None, connect=None):
    url = PUBLIC_URLS[exchange]
    if exchange == "binance_futures":
        import re
        if not streams or any(not re.fullmatch(r"[a-z0-9]+@depth5", s) for s in streams):
            raise ValueError("invalid Binance depth streams")
        url += "/".join(streams)
    # Server-side Upbit public streams must not synthesize a browser Origin.
    return _connect(url, connect=connect, suppress_origin=exchange == "upbit")


def spot_depth_quote(exchange, message, depth=5):
    """Decode a DEFAULT/SIMPLE KRW book without inventing source freshness.

    Returns {symbol, asks, bids, sourceTimestampMs}; levels use price/size.
    Control/unrelated frames return None; rejected or invalid books raise.
    The caller owns receive timestamps, socket lifecycle and reconnect policy.
    """
    import math
    from .api_error import ExchangeRequestError, safe_code
    from .public_orderbook import normalize_orderbook
    from .spot_trading import decimal

    if exchange not in {"upbit", "bithumb"}:
        raise ValueError("unsupported spot depth exchange")
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 30:
        raise ValueError("depth must be an integer in 1..30")
    if not isinstance(message, dict):
        return None
    if message.get("error"):
        raise ExchangeRequestError(exchange, safe_code(message))
    if message.get("type", message.get("ty")) != "orderbook":
        return None
    market = message.get("code", message.get("cd"))
    if not isinstance(market, str) or not re.fullmatch(r"KRW-[A-Z0-9]{1,20}", market):
        return None
    units = message.get("orderbook_units", message.get("obu"))
    if not isinstance(units, list) or any(not isinstance(unit, dict) for unit in units):
        raise ValueError("INVALID_BOOK_LEVELS")
    normalize_orderbook(exchange, message, market, observed_at=0)

    def levels(side, short, reverse):
        rows = [{"price": float(unit.get(f"{side}_price", unit.get(f"{short}p"))),
                 "size": float(unit.get(f"{side}_size", unit.get(f"{short}s")))} for unit in units]
        if any(not math.isfinite(row["price"]) or not math.isfinite(row["size"]) or row["price"] <= 0 for row in rows):
            raise ValueError("INVALID_BOOK_LEVEL")
        active = sorted((row for row in rows if row["size"] > 0), key=lambda row: row["price"], reverse=reverse)
        if not active:
            raise ValueError("EMPTY_ORDERBOOK")
        return active[:depth]

    value = message.get("timestamp", message.get("tms"))
    stamp = decimal(value) if value is not None else decimal(0)
    # Match the shared normalizer's units, retaining exact milliseconds instead
    # of round-tripping an epoch through float seconds.
    if stamp > 100_000_000_000_000:
        stamp /= 1000
    elif stamp <= 100_000_000_000:
        stamp *= 1000
    return {"symbol": market[4:], "asks": levels("ask", "a", False), "bids": levels("bid", "b", True),
            "sourceTimestampMs": max(0, int(stamp))}


def _markets(markets):
    if isinstance(markets, str):
        markets = [markets]
    if not isinstance(markets, (list, tuple)) or not markets:
        raise ValueError("at least one market is required")
    result = list(dict.fromkeys(markets))
    if any(not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9]{2,12}-[A-Z0-9]{2,15}", value)
           for value in result):
        raise ValueError("invalid market")
    return result


def private_account_connection_config(exchange, access, secret, markets, *, include_assets=True,
                                      include_trades=True, account_seq=1):
    """Build one account stream's wire messages for all requested spot markets.

    Each element is one subscription payload; encode_private_subscription applies
    its exchange-specific wire envelope before sending the WebSocket message.
    The caller owns connection sharing, acknowledgement, and bounded reconnects.
    """
    markets = _markets(markets)
    if exchange == "korbit":
        from .korbit.korbit_rest import private_connection_config as korbit_config
        return korbit_config(access, secret, markets, include_assets=include_assets,
                             include_trades=include_trades, account_seq=account_seq)
    if exchange not in PRIVATE_URLS:
        raise ValueError("unsupported private exchange")
    import jwt
    payload = {"nonce": str(uuid.uuid4()), "timestamp": int(time.time() * 1000)}
    if exchange == "coinone":
        payload["access_token"] = access
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha512).hexdigest()
        headers = [f"X-COINONE-PAYLOAD: {encoded}", f"X-COINONE-SIGNATURE: {signature}"]
        topics = [{"quote_currency": quote, "target_currency": ticker}
                  for quote, ticker in (market.split("-", 1) for market in markets)]
        subscriptions = [{"request_type": "SUBSCRIBE", "channel": "MYORDER", "topic": topics}]
        if include_assets:
            subscriptions.append({"request_type": "SUBSCRIBE", "channel": "MYASSET"})
    else:
        payload["access_key"] = access
        headers = [f"Authorization: Bearer {jwt.encode(payload, secret, algorithm='HS256')}"]
        channels = [{"type": "myOrder", "codes": markets}]
        if include_assets:
            channels.append({"type": "myAsset"})
        subscriptions = [[{"ticket": str(uuid.uuid4())}, *channels, {"format": "DEFAULT"}]]
    return PRIVATE_URLS[exchange], headers, subscriptions


def private_connection_config(exchange, access, secret, market):
    """Compatibility entry point for existing single-market page workers."""
    url, headers, subscriptions = private_account_connection_config(
        exchange, access, secret, market, include_assets=exchange == "korbit", include_trades=False)
    return url, headers, subscriptions if exchange == "korbit" else subscriptions[0]


def open_private_stream(url, headers, *, connect=None):
    # URLs are produced by private_connection_config; allow no alternate origins.
    base = url.split("?", 1)[0]
    if base not in {*PRIVATE_URLS.values(), "wss://ws-api.korbit.co.kr/v2/private", "wss://ws-api.digitalx.miraeasset.com/v2/private"}:
        raise ValueError("unsupported private stream URL")
    return _connect(url, headers=headers, connect=connect)


def encode_private_subscription(exchange, subscription):
    """Digital X v2 requires an array envelope even for a single subscription."""
    if exchange == "korbit":
        subscription = [subscription]
    return json.dumps(subscription)


def private_ping_message(exchange):
    if exchange != "coinone":
        raise ValueError("exchange uses WebSocket control ping")
    return {"request_type": "PING"}


def binance_spot_subscription(access, secret, timestamp):
    params = {"apiKey": access, "timestamp": timestamp}
    params["signature"] = hmac.new(secret.encode(), urlencode(sorted(params.items())).encode(), hashlib.sha256).hexdigest()
    return {"id": "gridlab-subscribe", "method": "userDataStream.subscribe.signature", "params": params}


def open_binance_private_stream(client, *, futures, connect=None):
    if futures:
        listen_key = client.create_listen_key()
        url = f"wss://fstream.binance.com/private/ws/{listen_key}"
    else:
        client._sync_server_time_offset()
        listen_key = None
        url = "wss://ws-api.binance.com:443/ws-api/v3"
    return _connect(url, connect=connect), listen_key
