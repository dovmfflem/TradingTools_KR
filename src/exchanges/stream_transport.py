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
    "binance_futures": "wss://fstream.binance.com/stream?streams=",
}
PRIVATE_URLS = {
    "upbit": "wss://api.upbit.com/websocket/v1/private",
    "bithumb": "wss://ws-api.bithumb.com/websocket/v2/private",
    "coinone": "wss://stream.coinone.co.kr/v1/private",
}


def _connect(url, *, headers=None, connect=None):
    import websocket
    kwargs = {"timeout": 10}
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
    return _connect(url, connect=connect)


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

    Each element of the returned subscriptions list is one WebSocket message.
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
