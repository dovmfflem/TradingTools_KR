"""KIS token reuse, domestic futures reads and explicit order operations.

The caller supplies secure persistence and a bounded cross-process cache lock.
Construction/token/account checks never submit trading orders. No HTTP request
is automatically retried; explicit futures mutations are provided by the mixin.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from ..api_error import ExchangeRequestError, reject_retrying_transport, response_metadata
from .futures import KisFuturesMixin


class KisRest(KisFuturesMixin):
    """token_cache implements load(id), save(id, dict), and locked(id).

    locked() must cover all processes sharing the credentials and have a finite
    acquisition deadline. save() must durably store the token as a secret.
    Tokens are refreshed only within five minutes of the server's KST expiry.
    """
    BASE_URL = "https://openapi.koreainvestment.com:9443"
    TIMEOUT = (3, 5)
    EXPIRY_MARGIN_SECONDS = 300
    REFRESH_COOLDOWN_SECONDS = 60

    def __init__(self, api_key, secret_key, account, *, token_cache, session=None, clock=time.time):
        if not api_key or not secret_key:
            raise ValueError("KIS API and Secret are required")
        if not re.fullmatch(r"[0-9]{8}-[0-9]{2}", account.strip()):
            raise ValueError("KIS Account must use 12345678-03 format")
        self.api_key = api_key
        self.secret_key = secret_key
        self.cano, self.product_code = account.strip().split("-")
        self.cache = token_cache
        self.session = session or requests.Session()
        self.clock = clock
        identity = json.dumps([self.BASE_URL, api_key, secret_key], separators=(",", ":"))
        self.cache_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def close(self):
        self.session.close()

    def _request(self, method, path, **kwargs):
        url = self.BASE_URL + path
        reject_retrying_transport(self.session, url)
        try:
            response = self.session.request(
                method, url, timeout=self.TIMEOUT, allow_redirects=False, **kwargs
            )
        except requests.Timeout:
            raise ExchangeRequestError("kis", "TIMED_OUT") from None
        except requests.RequestException:
            raise ExchangeRequestError("kis", "NETWORK_FAILED") from None
        retry_after, _ = response_metadata(response)
        if response.status_code != 200:
            raise ExchangeRequestError("kis", "HTTP_FAILED", status_code=response.status_code,
                                       retry_after_seconds=retry_after)
        try:
            payload = response.json()
        except (ValueError, TypeError):
            raise ExchangeRequestError("kis", "INVALID_RESPONSE") from None
        if not isinstance(payload, dict):
            raise ExchangeRequestError("kis", "INVALID_RESPONSE")
        self._last_tr_cont = str(getattr(response, "headers", {}).get("tr_cont", "")).strip()
        return payload

    @staticmethod
    def _timestamp(value):
        try:
            value = float(value)
            return value if math.isfinite(value) else 0.0
        except (TypeError, ValueError):
            return 0.0

    def get_access_token(self):
        with self.cache.locked(self.cache_id):
            stored = self.cache.load(self.cache_id) or {}
            now = self.clock()
            token = stored.get("access_token")
            expires_at = self._timestamp(stored.get("expires_at"))
            if isinstance(token, str) and token and expires_at > now + self.EXPIRY_MARGIN_SECONDS:
                return token
            retry_at = self._timestamp(stored.get("retry_at"))
            if retry_at > now:
                raise ExchangeRequestError("kis", "TOKEN_REFRESH_COOLDOWN",
                                           retry_after_seconds=min(retry_at - now, 86400))
            # Persist before issuance, so an interrupted process cannot immediately reissue.
            pending = {"retry_at": now + self.REFRESH_COOLDOWN_SECONDS}
            self.cache.save(self.cache_id, pending)
            try:
                payload = self._request("POST", "/oauth2/tokenP", json={
                    "grant_type": "client_credentials",
                    "appkey": self.api_key,
                    "appsecret": self.secret_key,
                })
                token = payload.get("access_token")
                try:
                    expires_at = datetime.strptime(
                        payload.get("access_token_token_expired", ""), "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=timezone(timedelta(hours=9))).timestamp()
                except (ValueError, TypeError):
                    raise ExchangeRequestError("kis", "INVALID_TOKEN_EXPIRY") from None
                if not isinstance(token, str) or not token or expires_at <= self.clock() + self.EXPIRY_MARGIN_SECONDS:
                    raise ExchangeRequestError("kis", "INVALID_TOKEN_RESPONSE")
            except ExchangeRequestError as error:
                pending["retry_at"] = max(pending["retry_at"], self.clock() + (error.retry_after_seconds or 0))
                self.cache.save(self.cache_id, pending)
                raise
            self.cache.save(self.cache_id, {"access_token": token, "expires_at": expires_at})
            return token

    def get_future_balance(self, *, night=False, context_fk="", context_nk="", continuation=""):
        token = self.get_access_token()
        path = "/uapi/domestic-futureoption/v1/trading/" + ("inquire-ngt-balance" if night else "inquire-balance")
        payload = self._request("GET", path, headers={
            "authorization": f"Bearer {token}", "appkey": self.api_key,
            "appsecret": self.secret_key, "tr_id": "CTFN6118R" if night else "CTFO6118R",
            "custtype": "P", "content-type": "application/json; charset=utf-8", "tr_cont": continuation,
        }, params={
            "CANO": self.cano, "ACNT_PRDT_CD": self.product_code,
            "MGNA_DVSN": "01", "EXCC_STAT_CD": "1", "CTX_AREA_FK200": context_fk, "CTX_AREA_NK200": context_nk,
        })
        if str(payload.get("rt_cd", "")) != "0":
            raise ExchangeRequestError("kis", "ACCOUNT_QUERY_FAILED")
        if not isinstance(payload.get("output1"), list) or not isinstance(payload.get("output2"), dict):
            raise ExchangeRequestError("kis", "INVALID_ACCOUNT_RESPONSE")
        return payload
