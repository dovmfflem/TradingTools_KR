"""Python public book stream contract, matching node/public-streams.mjs.

One connection per invocation. Connect 10s, receive 1s, first book / stale feed
30s, ping 20s with pong deadline 15s. No REST fallback or hidden reconnect.
"""
import json
import re
import time
import uuid
from decimal import Decimal
from .stream_transport import _connect
from .spot_trading import decimal, exact

URLS = {
    "upbit": "wss://api.upbit.com/websocket/v1",
    "bithumb": "wss://ws-api.bithumb.com/websocket/v1",
    "coinone": "wss://stream.coinone.co.kr",
    "korbit": "wss://ws-api.digitalx.miraeasset.com/v2/public",
}


def subscription(exchange, market):
    if exchange not in URLS or not re.fullmatch(r"KRW-[A-Z0-9]{2,15}", market):
        raise ValueError("INVALID_BOOK_MARKET")
    quote, asset = market.split("-")
    if exchange == "coinone":
        return {"request_type": "SUBSCRIBE", "channel": "ORDERBOOK", "topic": {
            "quote_currency": quote, "target_currency": asset}, "format": "DEFAULT"}
    if exchange == "korbit":
        return [{"requestId": 1, "method": "subscribe", "type": "orderbook", "symbols": [f"{asset.lower()}_krw"]}]
    return [{"ticket": str(uuid.uuid4())}, {"type": "orderbook", "codes": [market]}, {"format": "DEFAULT"}]


def normalize_orderbook(exchange, message, market, *, observed_at):
    if isinstance(message, list):
        message = message[0] if message else {}
    if not isinstance(message, dict):
        return None
    if exchange in {"upbit", "bithumb"}:
        if message.get("code", message.get("cd")) != market:
            return None
        units = message.get("orderbook_units", message.get("obu"))
        if not isinstance(units, list):
            return None
        bids = [(r.get("bid_price", r.get("bp")), r.get("bid_size", r.get("bs"))) for r in units]
        asks = [(r.get("ask_price", r.get("ap")), r.get("ask_size", r.get("as"))) for r in units]
        stamp = message.get("timestamp", message.get("tms"))
    else:
        data = message.get("data", message.get("d")) or {}
        if exchange == "coinone":
            if message.get("response_type", message.get("r")) != "DATA" or message.get("channel", message.get("c")) != "ORDERBOOK":
                return None
            code = f'{data.get("quote_currency", data.get("qc", ""))}-{data.get("target_currency", data.get("tc", ""))}'
        elif exchange == "korbit":
            if message.get("type") != "orderbook":
                return None
            code = "-".join(reversed(str(message.get("symbol", "")).upper().split("_")))
        else:
            raise ValueError("INVALID_BOOK_EXCHANGE")
        if code != market:
            return None
        bids = [(r.get("price", r.get("p")), r.get("qty", r.get("q"))) for r in data.get("bids", data.get("b", []))]
        asks = [(r.get("price", r.get("p")), r.get("qty", r.get("q"))) for r in data.get("asks", data.get("a", []))]
        stamp = data.get("timestamp", data.get("t", message.get("timestamp")))
    def best(levels, reverse):
        parsed = [(decimal(p), decimal(q)) for p, q in levels]
        if any(p <= 0 or q < 0 for p, q in parsed):
            raise ValueError("INVALID_BOOK_LEVEL")
        active = sorted((r for r in parsed if r[1] > 0), reverse=reverse)
        if not active:
            raise ValueError("EMPTY_ORDERBOOK")
        return active[0]
    bid, bid_size = best(bids, True)
    ask, ask_size = best(asks, False)
    if ask < bid:
        raise ValueError("CROSSED_ORDERBOOK")
    stamp = decimal(stamp) if stamp is not None else decimal(observed_at)
    if stamp > 100_000_000_000_000:
        stamp /= 1_000_000
    elif stamp > 100_000_000_000:
        stamp /= 1000
    return {"bid": exact(bid), "ask": exact(ask), "bid_size": exact(bid_size),
            "ask_size": exact(ask_size), "timestamp": float(stamp), "received_at": observed_at}


def run_orderbook_stream(payload, *, emit, stop, record=None, connect=None, clock=time.monotonic, wall=time.time):
    import websocket
    exchange, market = payload["exchange"], "KRW-" + payload["ticker"]
    request = subscription(exchange, market)
    ws = None
    try:
        ws = _connect(URLS[exchange], connect=connect, suppress_origin=exchange == "upbit")
        ws.settimeout(1)
        ws.send(json.dumps(request))
        last_book, next_ping, pong_deadline = clock(), clock() + 20, None
        ready = False
        while not stop.is_set():
            now = clock()
            if now - last_book >= 30 or (pong_deadline is not None and now >= pong_deadline):
                raise TimeoutError("PUBLIC_BOOK_TIMED_OUT")
            if now >= next_ping:
                if exchange == "coinone":
                    ws.send(json.dumps({"request_type": "PING"}))
                else:
                    ws.ping("book")
                next_ping, pong_deadline = now + 20, now + 15
            try:
                opcode, raw = ws.recv_data(control_frame=True)
            except websocket.WebSocketTimeoutException:
                continue
            if opcode == websocket.ABNF.OPCODE_PONG:
                pong_deadline = None
                continue
            if opcode == websocket.ABNF.OPCODE_CLOSE:
                raise ConnectionError("PUBLIC_BOOK_CLOSED")
            if opcode not in {websocket.ABNF.OPCODE_TEXT, websocket.ABNF.OPCODE_BINARY}:
                continue
            if len(raw) > 262144:
                raise ValueError("BOOK_MESSAGE_TOO_LARGE")
            message = json.loads(raw, parse_float=Decimal)
            if isinstance(message, dict):
                if message.get("error") or message.get("response_type") == "ERROR" or message.get("status") in {"fail", "error"}:
                    raise ValueError("BOOK_SUBSCRIPTION_REJECTED")
                if message.get("response_type", message.get("r")) == "PONG":
                    pong_deadline = None
            quote = normalize_orderbook(exchange, message, market, observed_at=wall())
            if quote:
                last_book = clock()
                if not ready:
                    emit({"event": "status", "status": "HEALTHY"})
                    ready = True
                emit({"event": "quote", "quote": quote})
    except Exception as error:
        emit({"event": "status", "status": "TIMED_OUT" if isinstance(error, TimeoutError) else "FAILED", "cause": type(error).__name__})
    finally:
        if ws is not None:
            ws.close(timeout=1)
