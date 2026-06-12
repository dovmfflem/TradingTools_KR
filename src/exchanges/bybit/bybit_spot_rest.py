from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


class BybitSpotRestError(Exception):
    """Raised when a Bybit Spot REST API request fails."""


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    result: list[str] = []

    for ch in line:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            break
        result.append(ch)

    return "".join(result).rstrip()


def _parse_simple_yaml_mapping(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue

        line = _strip_comment(raw_line)
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if ":" not in content:
            continue

        key, value = content.split(":", 1)
        key = key.strip()
        value = value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError("invalid YAML indentation")

        current = stack[-1][1]
        if value == "":
            child: dict[str, Any] = {}
            current[key] = child
            stack.append((indent, child))
        else:
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            current[key] = value

    return root


def _to_symbol(ticker: str) -> str:
    normalized = ticker.strip().upper()
    parts = normalized.split("-", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"invalid ticker '{ticker}'. expected format like 'xrp-usdt'")
    return f"{parts[0]}{parts[1]}"


class BybitSpotRest:
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        *,
        api_url: str = "https://api.bybit.com",
        recv_window_ms: int = 5000,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        if not secret_key:
            raise ValueError("secret_key is required")

        self.api_key = api_key
        self.secret_key = secret_key
        self.api_url = api_url.rstrip("/")
        self.recv_window_ms = recv_window_ms
        self.timeout_seconds = timeout_seconds
        self._session = requests.Session()

    @classmethod
    def from_info_yaml(cls, file_path: str = "info.yaml") -> "BybitSpotRest":
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"{file_path} not found")

        config = _parse_simple_yaml_mapping(path.read_text(encoding="utf-8"))
        api_key = config.get("bybit_api_key")
        secret_key = config.get("bybit_secret_key")
        if not api_key or not secret_key:
            raise ValueError("info.yaml must include 'bybit_api_key' and 'bybit_secret_key'")

        return cls(api_key=str(api_key), secret_key=str(secret_key))

    @staticmethod
    def _query_string(params: dict[str, Any]) -> str:
        payload = {k: v for k, v in params.items() if v is not None}
        return urlencode(payload, doseq=True)

    def _signed_headers(self, payload_str: str, timestamp_ms: int) -> dict[str, str]:
        prehash = f"{timestamp_ms}{self.api_key}{self.recv_window_ms}{payload_str}"
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            prehash.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": str(timestamp_ms),
            "X-BAPI-RECV-WINDOW": str(self.recv_window_ms),
            "X-BAPI-SIGN": signature,
            "Content-Type": "application/json",
        }

    def _request_public(self, path: str, *, params: dict[str, Any]) -> Any:
        response = self._session.get(
            f"{self.api_url}{path}",
            params=params,
            timeout=self.timeout_seconds,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}

        if not response.ok:
            raise BybitSpotRestError(f"GET {path} failed (status={response.status_code}): {payload}")
        return payload

    def _request_signed(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        params = params or {}
        body = body or {}
        timestamp_ms = int(time.time() * 1000)

        if method.upper() == "GET":
            payload_str = self._query_string(params)
            headers = self._signed_headers(payload_str, timestamp_ms)
            response = self._session.get(
                f"{self.api_url}{path}",
                params=params,
                headers=headers,
                timeout=self.timeout_seconds,
            )
        else:
            payload_str = json.dumps(body, separators=(",", ":"), ensure_ascii=True)
            headers = self._signed_headers(payload_str, timestamp_ms)
            response = self._session.request(
                method=method,
                url=f"{self.api_url}{path}",
                headers=headers,
                data=payload_str,
                timeout=self.timeout_seconds,
            )

        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}

        if not response.ok:
            raise BybitSpotRestError(
                f"{method} {path} failed (status={response.status_code}): {payload}"
            )
        if isinstance(payload, dict) and int(payload.get("retCode", 0)) != 0:
            raise BybitSpotRestError(f"{method} {path} failed: {payload}")

        return payload

    def get_orderbook(self, ticker: str, *, limit: int = 50) -> dict[str, Any]:
        symbol = _to_symbol(ticker)
        data = self._request_public(
            "/v5/market/orderbook",
            params={"category": "spot", "symbol": symbol, "limit": limit},
        )
        return data if isinstance(data, dict) else {"data": data}

    def place_limit_buy(
        self,
        ticker: str,
        *,
        price: str | float,
        qty: str | float | int,
        maker_only: bool = True,
    ) -> dict[str, Any]:
        symbol = _to_symbol(ticker)
        time_in_force = "PostOnly" if maker_only else "GTC"
        data = self._request_signed(
            "POST",
            "/v5/order/create",
            body={
                "category": "spot",
                "symbol": symbol,
                "side": "Buy",
                "orderType": "Limit",
                "qty": str(qty),
                "price": str(price),
                "timeInForce": time_in_force,
            },
        )
        return data if isinstance(data, dict) else {"data": data}

    def get_order(self, ticker: str, order_id: str) -> dict[str, Any]:
        symbol = _to_symbol(ticker)
        data = self._request_signed(
            "GET",
            "/v5/order/realtime",
            params={"category": "spot", "symbol": symbol, "orderId": order_id},
        )
        return data if isinstance(data, dict) else {"data": data}

    def cancel_order(self, ticker: str, order_id: str) -> dict[str, Any]:
        symbol = _to_symbol(ticker)
        data = self._request_signed(
            "POST",
            "/v5/order/cancel",
            body={"category": "spot", "symbol": symbol, "orderId": order_id},
        )
        return data if isinstance(data, dict) else {"data": data}
