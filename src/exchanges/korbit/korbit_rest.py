"""Korbit Open API v2. Secrets stay here; requests have no automatic retries.

Reference: https://docs.korbit.co.kr/llms-full.txt (2026-09-18).
Each HTTP request has a 3s connect/read timeout; the parent job has a 25s deadline.
Signed calls never follow redirects or expose URLs/response bodies in errors.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from urllib.parse import urlencode

import requests
from ..api_error import ExchangeRequestError, ExchangeResponseMixin, response_metadata, reject_retrying_transport

BASE_URL = "https://api.korbit.co.kr"
PRIVATE_WS_URL = "wss://ws-api.korbit.co.kr/v2/private"


class KorbitRestError(ExchangeRequestError):
    pass


class KorbitRest(ExchangeResponseMixin):
    def __init__(self, access, secret, *, session=None):
        self.access, self.secret = access, secret
        self._session = session or requests.Session()
        self._server_time = None
        self._synced_at = 0.0

    def timestamp(self):
        if self._server_time is None or time.monotonic() - self._synced_at > 30:
            data = self.request("GET", "/v2/time", private=False)
            self._server_time = int(data["time"])
            self._synced_at = time.monotonic()
        return self._server_time + int((time.monotonic() - self._synced_at) * 1000)

    def signed_params(self, params=None):
        encoded = urlencode({**(params or {}), "timestamp": self.timestamp(), "recvWindow": 5000})
        if self.secret.lstrip().startswith("-----BEGIN PRIVATE KEY-----"):
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            key = load_pem_private_key(self.secret.encode(), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError("KORBIT_ED25519_KEY_REQUIRED")
            signature = base64.b64encode(key.sign(encoded.encode())).decode()
        else:
            signature = hmac.new(self.secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        return encoded + "&" + urlencode({"signature": signature})

    def request(self, method, path, params=None, *, private=True):
        self._clear_response()
        if method in {"POST", "DELETE"}:
            reject_retrying_transport(self._session, BASE_URL + path)
        encoded = self.signed_params(params) if private else urlencode(params or {})
        headers = {"X-KAPI-KEY": self.access} if private else {}
        kwargs = {"headers": headers, "timeout": (3, 3), "allow_redirects": False}
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            kwargs["data"] = encoded
        else:
            kwargs["params"] = encoded
        try:
            response = self._session.request(method, BASE_URL + path, **kwargs)
        except requests.Timeout:
            self._clear_response()
            raise KorbitRestError("korbit", "ORDER_RESULT_UNKNOWN" if method == "POST" else "TIMED_OUT",
                                  public_message=None if method == "POST" else "API_EXCHANGE_TIMED_OUT",
                                  outcome_unknown=method in {"POST", "DELETE"}) from None
        except requests.RequestException:
            self._clear_response()
            raise KorbitRestError("korbit", "ORDER_RESULT_UNKNOWN" if method == "POST" else "CONNECTION_FAILED",
                                  outcome_unknown=method in {"POST", "DELETE"}) from None
        self._remember_response(response)
        retry_after, remaining = response_metadata(response)
        if response.status_code == 429:
            raise KorbitRestError("korbit", "RATE_LIMITED", status_code=429,
                                  retry_after_seconds=retry_after, rate_limit=remaining)
        if 300 <= response.status_code < 400:
            raise KorbitRestError("korbit", "REDIRECT_REJECTED", status_code=response.status_code,
                                  rate_limit=remaining, outcome_unknown=method in {"POST", "DELETE"})
        if response.status_code >= 500:
            raise KorbitRestError("korbit", "ORDER_RESULT_UNKNOWN" if method == "POST" else "UNAVAILABLE",
                                  status_code=response.status_code, retry_after_seconds=retry_after,
                                  rate_limit=remaining, outcome_unknown=method in {"POST", "DELETE"})
        try:
            body = response.json()
        except ValueError:
            raise KorbitRestError("korbit", "ORDER_RESULT_UNKNOWN" if method == "POST" else "INVALID_RESPONSE",
                                  status_code=response.status_code, outcome_unknown=method in {"POST", "DELETE"}) from None
        if not isinstance(body, dict):
            raise KorbitRestError("korbit", "ORDER_RESULT_UNKNOWN" if method == "POST" else "INVALID_RESPONSE",
                                  status_code=response.status_code, outcome_unknown=method in {"POST", "DELETE"})
        if response.status_code >= 400 or body.get("success") is not True:
            code = (body.get("error") or {}).get("message", "")
            public_code = code if isinstance(code, str) and re.fullmatch(r"[A-Z_]{1,80}", code) else "REQUEST_FAILED"
            raise KorbitRestError("korbit", public_code, status_code=response.status_code,
                                  retry_after_seconds=retry_after, rate_limit=remaining)
        return body.get("data")

    def get_tick_size_policy(self, symbol):
        return self.request("GET", "/v2/tickSizePolicy", {"symbol": symbol}, private=False)

    def get_trading_fee_policy(self, symbol, *, account_seq=1):
        return self.request("GET", "/v2/tradingFeePolicy", {"symbol": symbol, "accountSeq": account_seq})

    def get_balances(self, *, account_seq=1):
        return self.request("GET", "/v2/balance", {"accountSeq": account_seq})

    def get_order(self, symbol, *, order_id=None, client_order_id=None, account_seq=1):
        if (order_id is None) == (client_order_id is None):
            raise ValueError("exactly one of order_id or client_order_id is required")
        params = {"symbol": symbol, "accountSeq": account_seq}
        params["orderId" if order_id is not None else "clientOrderId"] = (
            order_id if order_id is not None else client_order_id)
        return self.request("GET", "/v2/orders", params)

    def get_open_orders(self, symbol, *, account_seq=1, limit=1000):
        return self.request("GET", "/v2/openOrders", {"symbol": symbol,
                            "accountSeq": account_seq, "limit": limit})

    def list_all_orders(self, symbol, *, account_seq=1, limit=100,
                        start_time=None, end_time=None):
        params = {"symbol": symbol, "accountSeq": account_seq, "limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        return self.request("GET", "/v2/allOrders", params)

    def get_my_trades(self, symbol, *, account_seq=1, limit=20,
                      start_time=None, end_time=None):
        params = {"symbol": symbol, "accountSeq": account_seq, "limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        return self.request("GET", "/v2/myTrades", params)

    def place_order(self, *, symbol, side, order_type, client_order_id, account_seq=1,
                    price=None, qty=None, amount=None):
        if not client_order_id:
            raise ValueError("client_order_id is required")
        params = {"symbol": symbol, "side": side, "orderType": order_type,
                  "accountSeq": account_seq, "clientOrderId": client_order_id}
        if order_type == "limit":
            if price is None or qty is None:
                raise ValueError("limit order requires price and qty")
            params.update(price=price, qty=qty)
        elif order_type == "market" and side == "buy":
            if amount is None:
                raise ValueError("market buy requires amount")
            params["amt"] = amount
        elif order_type == "market" and side == "sell":
            if qty is None:
                raise ValueError("market sell requires qty")
            params["qty"] = qty
        else:
            raise ValueError("invalid order side/type")
        return self.request("POST", "/v2/orders", params)

    def cancel_order(self, *, symbol, order_id=None, client_order_id=None, account_seq=1):
        if (order_id is None) == (client_order_id is None):
            raise ValueError("exactly one of order_id or client_order_id is required")
        params = {"symbol": symbol, "accountSeq": account_seq}
        params["orderId" if order_id is not None else "clientOrderId"] = (
            order_id if order_id is not None else client_order_id)
        return self.request("DELETE", "/v2/orders", params)


def private_connection_config(access, secret, markets, *, include_assets=True, include_trades=False, account_seq=1):
    client = KorbitRest(access, secret)
    try:
        query = client.signed_params()
    finally:
        client._session.close()
    from ..stream_transport import _markets
    symbols = [f"{ticker.lower()}_{quote.lower()}"
               for quote, ticker in (market.split("-", 1) for market in _markets(markets))]
    subscriptions = [{"requestId": 1, "method": "subscribe", "type": "myOrder",
                      "symbols": symbols, "accountSeqs": [account_seq]}]
    if include_assets:
        subscriptions.append({"requestId": 2, "method": "subscribe", "type": "myAsset",
                              "accountSeqs": [account_seq]})
    if include_trades:
        subscriptions.append({"requestId": 3, "method": "subscribe", "type": "myTrade",
                              "symbols": symbols, "accountSeqs": [account_seq]})
    return PRIVATE_WS_URL + "?" + query, [f"X-KAPI-KEY: {access}"], subscriptions

