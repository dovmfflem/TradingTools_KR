"""Safe exchange HTTP failure metadata for account-level schedulers.

Never retain response bodies, signed URLs, authorization headers or secrets here.
The caller decides whether a read may be retried; a possibly submitted mutation
must first be reconciled with its client/order identifier.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from threading import local


class ExchangeRequestError(RuntimeError):
    def __init__(self, exchange: str, code: str, *, status_code: int | None = None,
                 retry_after_seconds: float | None = None, rate_limit: str | None = None,
                 outcome_unknown: bool = False, public_message: str | None = None):
        self.exchange = exchange
        self.code = code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.rate_limit = rate_limit
        self.outcome_unknown = outcome_unknown
        super().__init__(public_message or f"{exchange.upper()}_{code}")


def response_metadata(response):
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return None, None
    retry = headers.get("Retry-After") or headers.get("retry-after")
    try:
        retry_after = float(retry)
        if not 0 <= retry_after <= 86400:
            retry_after = None
    except (ValueError, TypeError):
        retry_after = None
    remaining = headers.get("Remaining-Req") or headers.get("remaining-req")
    if not isinstance(remaining, str) or len(remaining) > 200:
        remaining = None
    return retry_after, remaining


class ExchangeResponseMixin:
    """The caller may inspect the latest response's safe quota data on its thread."""

    @property
    def last_response_metadata(self):
        holder = getattr(self, "_response_local", None)
        return dict(getattr(holder, "value", {})) if holder is not None else {}

    def _remember_response(self, response):
        holder = getattr(self, "_response_local", None)
        if holder is None:
            holder = self._response_local = local()
        retry_after, remaining = response_metadata(response)
        holder.value = {"statusCode": response.status_code,
                        "retryAfterSeconds": retry_after, "rateLimit": remaining}

    def _clear_response(self):
        holder = getattr(self, "_response_local", None)
        if holder is not None:
            holder.value = {}


def safe_code(payload):
    if not isinstance(payload, dict):
        return "REQUEST_FAILED"
    error = payload.get("error")
    value = (error.get("name") or error.get("code")) if isinstance(error, dict) else error
    value = value or payload.get("error_code")
    if not isinstance(value, (str, int)) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(value)):
        return "REQUEST_FAILED"
    return str(value)


def reject_retrying_transport(session, url):
    """A mutation must never inherit a requests adapter that resubmits it."""
    from requests.adapters import HTTPAdapter
    get_adapter = getattr(session, "get_adapter", None)
    if not callable(get_adapter):
        return
    adapter = get_adapter(url)
    if isinstance(adapter, HTTPAdapter) and adapter.max_retries.total not in (0, False):
        raise ValueError("financial request requires retries disabled")
