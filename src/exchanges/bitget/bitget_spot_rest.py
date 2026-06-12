from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


class BitgetSpotRestError(Exception):
    """Raised when a Bitget Spot REST API request fails."""


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


class BitgetSpotRest:
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        passphrase: str,
        *,
        api_url: str = "https://api.bitget.com",
        timeout_seconds: float = 10.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        if not secret_key:
            raise ValueError("secret_key is required")
        if not passphrase:
            raise ValueError("passphrase is required")

        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._session = requests.Session()

    @classmethod
    def from_info_yaml(cls, file_path: str = "info.yaml") -> "BitgetSpotRest":
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"{file_path} not found")

        config = _parse_simple_yaml_mapping(path.read_text(encoding="utf-8"))
        api_key = config.get("bitget_api_key")
        secret_key = config.get("bitget_secret_key")
        passphrase = config.get("bitget_passphrase")
        if not api_key or not secret_key or not passphrase:
            raise ValueError(
                "info.yaml must include 'bitget_api_key', 'bitget_secret_key', and 'bitget_passphrase'"
            )

        return cls(
            api_key=str(api_key),
            secret_key=str(secret_key),
            passphrase=str(passphrase),
        )

    @staticmethod
    def _query_string(params: dict[str, Any]) -> str:
        payload = {k: v for k, v in params.items() if v is not None}
        return urlencode(payload, doseq=True)

    def _signature(
        self,
        *,
        timestamp_ms: str,
        method: str,
        request_path: str,
        query_string: str,
        body_text: str,
    ) -> str:
        full_path = request_path
        if query_string:
            full_path = f"{request_path}?{query_string}"
        prehash = f"{timestamp_ms}{method.upper()}{full_path}{body_text}"
        digest = hmac.new(
            self.secret_key.encode("utf-8"),
            prehash.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

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
            raise BitgetSpotRestError(f"GET {path} failed (status={response.status_code}): {payload}")
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

        timestamp_ms = str(int(time.time() * 1000))
        query_string = self._query_string(params)
        body_text = ""
        if method.upper() != "GET":
            body_text = json.dumps(body, separators=(",", ":"), ensure_ascii=True)

        signature = self._signature(
            timestamp_ms=timestamp_ms,
            method=method,
            request_path=path,
            query_string=query_string,
            body_text=body_text,
        )

        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp_ms,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }

        response = self._session.request(
            method=method,
            url=f"{self.api_url}{path}",
            params=params if method.upper() == "GET" else None,
            data=body_text if method.upper() != "GET" else None,
            headers=headers,
            timeout=self.timeout_seconds,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}

        if not response.ok:
            raise BitgetSpotRestError(
                f"{method} {path} failed (status={response.status_code}): {payload}"
            )
        if isinstance(payload, dict) and str(payload.get("code", "")) not in {"0", "00000"}:
            raise BitgetSpotRestError(f"{method} {path} failed: {payload}")

        return payload

    def get_orderbook(self, ticker: str, *, limit: int = 15) -> dict[str, Any]:
        symbol = _to_symbol(ticker)
        data = self._request_public(
            "/api/v2/spot/market/orderbook",
            params={"symbol": symbol, "type": "step0", "limit": limit},
        )
        return data if isinstance(data, dict) else {"data": data}

    def place_limit_buy(
        self,
        ticker: str,
        *,
        price: str | float,
        size: str | float | int,
        maker_only: bool = True,
    ) -> dict[str, Any]:
        symbol = _to_symbol(ticker)
        force = "post_only" if maker_only else "gtc"
        data = self._request_signed(
            "POST",
            "/api/v2/spot/trade/place-order",
            body={
                "symbol": symbol,
                "side": "buy",
                "orderType": "limit",
                "force": force,
                "price": str(price),
                "size": str(size),
            },
        )
        return data if isinstance(data, dict) else {"data": data}

    def get_order(self, ticker: str, order_id: str) -> dict[str, Any]:
        if not order_id:
            raise ValueError("order_id is required")
        data = self._request_signed(
            "GET",
            "/api/v2/spot/trade/orderInfo",
            params={"orderId": order_id},
        )
        return data if isinstance(data, dict) else {"data": data}

    def cancel_order(self, ticker: str, order_id: str) -> dict[str, Any]:
        symbol = _to_symbol(ticker)
        data = self._request_signed(
            "POST",
            "/api/v2/spot/trade/cancel-order",
            body={"symbol": symbol, "orderId": order_id},
        )
        return data if isinstance(data, dict) else {"data": data}
