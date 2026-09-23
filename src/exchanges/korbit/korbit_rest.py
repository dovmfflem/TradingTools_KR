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

BASE_URL = "https://api.korbit.co.kr"
PRIVATE_WS_URL = "wss://ws-api.korbit.co.kr/v2/private"


class KorbitRest:
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
        except requests.Timeout as error:
            code = "KORBIT_ORDER_RESULT_UNKNOWN" if method == "POST" else "API_EXCHANGE_TIMED_OUT"
            raise RuntimeError(code) from error
        except requests.RequestException as error:
            code = "KORBIT_ORDER_RESULT_UNKNOWN" if method == "POST" else "KORBIT_CONNECTION_FAILED"
            raise RuntimeError(code) from error
        if response.status_code == 429:
            raise RuntimeError("KORBIT_RATE_LIMITED")
        if 300 <= response.status_code < 400:
            raise RuntimeError("KORBIT_REDIRECT_REJECTED")
        if response.status_code >= 500:
            raise RuntimeError("KORBIT_ORDER_RESULT_UNKNOWN" if method == "POST" else "KORBIT_UNAVAILABLE")
        try:
            body = response.json()
        except ValueError as error:
            raise RuntimeError("KORBIT_ORDER_RESULT_UNKNOWN" if method == "POST" else "KORBIT_INVALID_RESPONSE") from error
        if not isinstance(body, dict):
            raise RuntimeError("KORBIT_ORDER_RESULT_UNKNOWN" if method == "POST" else "KORBIT_INVALID_RESPONSE")
        if response.status_code >= 400 or body.get("success") is not True:
            code = (body.get("error") or {}).get("message", "")
            safe_code = code if isinstance(code, str) and re.fullmatch(r"[A-Z_]{1,80}", code) else "REQUEST_FAILED"
            raise RuntimeError("KORBIT_" + safe_code)
        return body.get("data")


def private_connection_config(access, secret, market):
    client = KorbitRest(access, secret)
    try:
        query = client.signed_params()
    finally:
        client._session.close()
    quote, ticker = market.split("-", 1)
    return PRIVATE_WS_URL + "?" + query, [f"X-KAPI-KEY: {access}"], [
        {"requestId": 1, "method": "subscribe", "type": "myOrder", "symbols": [f"{ticker.lower()}_{quote.lower()}"], "accountSeqs": [1]},
        {"requestId": 2, "method": "subscribe", "type": "myAsset", "accountSeqs": [1]},
    ]

