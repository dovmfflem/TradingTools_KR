"""KIS domestic commodity futures quotes and account notices, day / KRX night.

Single connection per instance. Call connect/receive/close from an owner loop;
reconnection is explicit, throttled, and restores subscriptions. No trading calls.
Order notices contain per-event quantities, NOT cumulative/terminal snapshots.
"""
import base64
import json
import re
import time
from threading import Lock

import websocket
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from ..api_error import ExchangeRequestError
from .futures import KisFuturesMixin, amount


class KisFuturesWebSocket:
    URL = "ws://ops.koreainvestment.com:21000/tryitout"
    MAX_FRAME = 262144

    def __init__(self, rest, symbols, *, hts_id, night=False, connector=None, clock=time.monotonic,
                 product_kind="commodity", notices=True):
        if notices and (not isinstance(hts_id, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", hts_id)):
            raise ValueError("HTS_ID_REQUIRED")
        if product_kind not in {"commodity", "index", "stock"}:
            raise ValueError("INVALID_FUTURES_KIND")
        if not symbols or isinstance(symbols, str) or len(symbols) > 39:
            raise ValueError("INVALID_SUBSCRIPTIONS")
        self.symbols = tuple(dict.fromkeys(KisFuturesMixin._future_symbol(s) for s in symbols))
        self.rest, self.hts_id, self.night = rest, hts_id, night
        self.quote_tr = "H0MFASP0" if night else {"commodity": "H0CFASP0", "index": "H0IFASP0", "stock": "H0ZFASP0"}[product_kind]
        self.depth = 10 if product_kind == "stock" and not night else 5
        self.notice_tr = "H0MFCNI0" if night else "H0IFCNI0"
        self.subscriptions = {(self.quote_tr, s) for s in self.symbols}
        if notices:
            self.subscriptions.add((self.notice_tr, hts_id))
        self.connector = connector or websocket.create_connection
        self.clock, self.lock = clock, Lock()
        self.socket, self.keys, self.acked = None, {}, set()
        self.retry_at, self.failures, self.last_received, self.connected_at = 0, 0, 0, 0
        self.state = "STOPPED"

    @property
    def ready(self):
        return self.socket is not None and self.acked == self.subscriptions

    def connect(self):
        if not self.lock.acquire(blocking=False):
            raise ExchangeRequestError("kis", "WS_CONNECT_BUSY")
        try:
            if self.socket is not None:
                return
            if self.clock() < self.retry_at:
                raise ExchangeRequestError("kis", "WS_RECONNECT_WAIT", retry_after_seconds=self.retry_at - self.clock())
            self.state = "CONNECTING"
            self.keys, self.acked = {}, set()
            try:
                approval = self.rest.get_ws_approval()
                self.socket = self.connector(self.URL, timeout=5, enable_multithread=True)
                self.socket.settimeout(5)
                self.connected_at = self.last_received = self.clock()
                deadline = self.clock() + 20
                for tr_id, key in sorted(self.subscriptions):
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        raise ExchangeRequestError("kis", "WS_SUBSCRIBE_TIMED_OUT")
                    self.socket.settimeout(min(5, remaining))
                    self.socket.send(json.dumps({"header": {"approval_key": approval, "custtype": "P",
                        "tr_type": "1", "content-type": "utf-8"}, "body": {"input": {"tr_id": tr_id, "tr_key": key}}}))
            except ExchangeRequestError:
                self._failed()
                raise
            except Exception:
                self._failed()
                raise ExchangeRequestError("kis", "WS_CONNECT_FAILED") from None
        finally:
            self.lock.release()

    def _failed(self):
        self.close()
        self.failures += 1
        self.retry_at = self.clock() + (10, 20, 40, 80, 120)[min(self.failures - 1, 4)]
        self.state = "RECONNECT_WAIT"

    def close(self):
        sock, self.socket = self.socket, None
        self.keys, self.acked = {}, set()
        self.state = "STOPPED"
        if sock is not None:
            try:
                sock.close(timeout=1)
            except Exception:
                pass

    def receive(self):
        """One bounded receive (5s). Empty list means no application data.

        ACK deadline 20s, silent-connection deadline 90s. On failure caller must
        reconcile REST state before trading; no implicit retry loop is started.
        """
        if self.socket is None:
            raise ExchangeRequestError("kis", "WS_NOT_CONNECTED")
        try:
            if not self.ready and self.clock() - self.connected_at >= 20:
                raise ExchangeRequestError("kis", "WS_SUBSCRIBE_TIMED_OUT")
            try:
                raw = self.socket.recv()
            except websocket.WebSocketTimeoutException:
                if self.clock() - self.last_received >= 90:
                    raise ExchangeRequestError("kis", "WS_HEARTBEAT_TIMED_OUT") from None
                return []
            if not raw:
                raise ExchangeRequestError("kis", "WS_CLOSED")
            result = self.parse(raw)
            self.last_received = self.clock()
            if self.ready:
                self.state = "HEALTHY"
                # A briefly acknowledged connection must not reset the backoff.
                if self.clock() - self.connected_at >= 60:
                    self.failures = 0
            return result
        except websocket.WebSocketConnectionClosedException:
            self._failed()
            raise ExchangeRequestError("kis", "WS_CLOSED") from None
        except ExchangeRequestError:
            self._failed()
            raise
        except Exception:
            self._failed()
            raise ExchangeRequestError("kis", "WS_INVALID_FRAME") from None

    def parse(self, raw):
        if not isinstance(raw, str) or len(raw) > self.MAX_FRAME:
            raise ExchangeRequestError("kis", "WS_INVALID_FRAME")
        if raw.startswith("{"):
            message = json.loads(raw)
            header = message["header"]
            tr_id = header["tr_id"]
            if tr_id == "PINGPONG":
                self.socket.pong(raw.encode("utf-8"))
                return []
            identity = (tr_id, header.get("tr_key"))
            if identity not in self.subscriptions:
                raise ExchangeRequestError("kis", "WS_UNEXPECTED_SUBSCRIPTION")
            body = message.get("body", {})
            if str(body.get("rt_cd")) != "0":
                # Preserve the documented machine code, never msg1/raw frames
                # (which can contain the HTS ID or subscription credentials).
                code = body.get("msg_cd")
                if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,40}", code):
                    code = "WS_SUBSCRIPTION_REJECTED"
                raise ExchangeRequestError("kis", code)
            if tr_id == self.notice_tr:
                output = body.get("output", {})
                key, iv = str(output.get("key", "")).encode(), str(output.get("iv", "")).encode()
                if len(key) != 32 or len(iv) != 16:
                    raise ExchangeRequestError("kis", "WS_INVALID_CIPHER")
                self.keys[tr_id] = (key, iv)
            self.acked.add(identity)
            return []
        kind, tr_id, count, payload = raw.split("|", 3)
        if tr_id not in {self.quote_tr, self.notice_tr} or kind not in {"0", "1"}:
            raise ExchangeRequestError("kis", "WS_UNEXPECTED_CHANNEL")
        count = int(count)
        if not 1 <= count <= 100:
            raise ExchangeRequestError("kis", "WS_INVALID_COUNT")
        if tr_id == self.notice_tr:
            if kind != "1" or tr_id not in self.keys:
                raise ExchangeRequestError("kis", "WS_CIPHER_REQUIRED")
            key, iv = self.keys[tr_id]
            decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
            plain = decryptor.update(base64.b64decode(payload, validate=True)) + decryptor.finalize()
            unpad = PKCS7(128).unpadder()
            payload = (unpad.update(plain) + unpad.finalize()).decode("utf-8")
        elif kind != "0":
            raise ExchangeRequestError("kis", "WS_INVALID_QUOTE_FRAME")
        fields = payload.split("^")
        width = (19 if self.night else 22) if tr_id == self.notice_tr else 6 * self.depth + 8
        if len(fields) != width * count:
            if tr_id == self.notice_tr:
                # An authenticated, decrypted notice with an unfamiliar schema
                # is a reconciliation hint, never an inferred fill/cancellation.
                # Keep the healthy socket instead of reconnecting on every order.
                return [{"event": "order_reconcile", "reason": "WS_INVALID_FIELD_COUNT",
                         "channel": tr_id, "fieldCount": len(fields), "expectedFieldCount": width * count,
                         "recordCount": count, "requiresReconciliation": True}]
            error = ExchangeRequestError("kis", "WS_INVALID_FIELD_COUNT")
            error.diagnostic = {"channel": tr_id, "fieldCount": len(fields),
                                "expectedFieldCount": width * count, "recordCount": count}
            raise error
        return [event for i in range(0, len(fields), width)
                if (event := self._record(tr_id, fields[i:i + width])) is not None]

    def _record(self, tr_id, fields):
        if tr_id == self.quote_tr:
            if fields[0] not in self.symbols:
                raise ExchangeRequestError("kis", "WS_SYMBOL_MISMATCH")
            depth = self.depth
            bid, ask = amount(fields[2 + depth]), amount(fields[2])
            if bid < 0 or ask < 0:
                raise ExchangeRequestError("kis", "WS_INVALID_QUOTE")
            def levels(price_offset, quantity_offset):
                result = []
                for i in range(depth):
                    price, qty = amount(fields[price_offset + i]), amount(fields[quantity_offset + i])
                    if price < 0 or qty < 0 or qty != int(qty):
                        raise ExchangeRequestError("kis", "WS_INVALID_DEPTH")
                    if price > 0:
                        result.append({"price": str(price), "quantity": str(qty)})
                return result
            return {"event": "quote", "symbol": fields[0], "time": fields[1],
                    "bid": str(bid), "ask": str(ask), "tradable": 0 < bid <= ask,
                    "asks": levels(2, 2 + 4 * depth), "bids": levels(2 + depth, 2 + 5 * depth)}
        if fields[1] != self.rest.cano + self.rest.product_code or fields[7] not in self.symbols:
            # An HTS subscription may include other accounts/products.
            return None
        quantity = amount(fields[8])
        if quantity < 0 or quantity != int(quantity) or fields[4] not in {"01", "02"}:
            raise ExchangeRequestError("kis", "WS_INVALID_NOTICE")
        # Preserve protocol flags: acceptance/cancel is not a cumulative fill.
        return {"event": "order_notice", "orderId": fields[2], "originalOrderId": fields[3],
                "symbol": fields[7], "side": "sell" if fields[4] == "01" else "buy",
                "eventQuantity": str(quantity), "price": str(amount(fields[9])), "time": fields[10],
                "rejected": fields[11], "executionFlag": fields[12], "acceptanceFlag": fields[13],
                "amendCancelCode": fields[5], "orderKind": fields[6],
                "reportedQuantity": fields[15], "night": self.night,
                "requiresReconciliation": True}
