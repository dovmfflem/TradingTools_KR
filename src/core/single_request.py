"""Signed single-attempt transport for workflows that reconcile uncertain outcomes.

Unlike the legacy Binance helpers this never retries -1021 (including for POST).
The caller owns response parsing/closing and its overall operation deadline.
"""
import math


def signed_request_once(client, method, path, params=None, *, scheme, timeout=(3, 5)):
    if scheme not in {"upbit", "binance"}:
        raise ValueError("unsupported signing scheme")
    if method not in {"GET", "POST", "DELETE", "PUT"} or not path.startswith("/") or path.startswith("//") or "://" in path:
        raise ValueError("invalid request method/path")
    if len(timeout) != 2 or any(not math.isfinite(t) or t <= 0 for t in timeout):
        raise ValueError("connect/read timeout must be finite and positive")
    values = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    options = {"method": method, "url": client.api_url + path,
               "timeout": tuple(timeout), "allow_redirects": False}
    if scheme == "upbit":
        options["headers"] = client._auth_headers(params=values if method == "GET" else None,
                                                 json_body=values if method != "GET" else None)
        options["params" if method == "GET" else "json"] = values
    else:
        options["headers"] = {"X-MBX-APIKEY": client.api_key}
        options["params"] = client._signed_params(values)
    # Refuse an explicitly retrying requests adapter instead of silently resubmitting
    # a financial request. Standard TradingTools sessions have total=0.
    from requests.adapters import HTTPAdapter
    adapter = client._session.get_adapter(options["url"])
    if isinstance(adapter, HTTPAdapter) and adapter.max_retries.total not in (0, False):
        raise ValueError("single-attempt transport requires retries disabled")
    return client._session.request(**options)
