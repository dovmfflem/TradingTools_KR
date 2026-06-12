from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from pathlib import Path
from typing import Any

import requests


class CoinoneRestError(Exception):
    pass


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


def _to_pair(ticker: str) -> tuple[str, str]:
    normalized = ticker.strip().upper()
    parts = normalized.split("-", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"invalid ticker '{ticker}'. expected format like 'xrp-krw'")
    return parts[1], parts[0]


class CoinoneRest:
    def __init__(
        self,
        access_token: str,
        secret_key: str,
        *,
        api_url: str = "https://api.coinone.co.kr",
        timeout_seconds: float = 10.0,
    ) -> None:
        if not access_token:
            raise ValueError("access_token is required")
        if not secret_key:
            raise ValueError("secret_key is required")

        self.access_token = access_token
        self.secret_key = secret_key
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._session = requests.Session()

    @classmethod
    def from_info_yaml(cls, file_path: str = "info.yaml") -> "CoinoneRest":
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"{file_path} not found")

        config = _parse_simple_yaml_mapping(path.read_text(encoding="utf-8"))
        access_token = config.get("coinone_access_token")
        secret_key = config.get("coinone_secret_key")
        if not access_token or not secret_key:
            raise ValueError(
                "info.yaml must include 'coinone_access_token' and 'coinone_secret_key'"
            )
        return cls(access_token=str(access_token), secret_key=str(secret_key))

    def _headers(self, body: dict[str, Any]) -> dict[str, str]:
        payload_json = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        payload_b64 = base64.b64encode(payload_json.encode("utf-8")).decode("utf-8")
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-COINONE-PAYLOAD": payload_b64,
            "X-COINONE-SIGNATURE": signature,
        }

    def _request(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "access_token": self.access_token,
            "nonce": str(uuid.uuid4()),
            **body,
        }
        response = self._session.post(
            f"{self.api_url}{path}",
            headers=self._headers(payload),
            json=payload,
            timeout=self.timeout_seconds,
        )
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text}
        if not response.ok:
            raise CoinoneRestError(f"POST {path} failed status={response.status_code}: {data}")
        if isinstance(data, dict) and str(data.get("result", "")).lower() == "error":
            raise CoinoneRestError(f"POST {path} error: {data}")
        if isinstance(data, dict):
            return data
        return {"data": data}

    def get_accounts(self) -> list[dict[str, Any]]:
        data = self._request("/v2.1/account/balance/all", {})
        balances = data.get("balances")
        if not isinstance(balances, list):
            return []
        rows: list[dict[str, Any]] = []
        for item in balances:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "currency": str(item.get("currency") or "").lower(),
                    "balance": item.get("available") or "0",
                }
            )
        return rows

    def place_order(
        self,
        *,
        ticker: str,
        side: str,
        order_type: str,
        price: str,
        volume: str,
    ) -> dict[str, Any]:
        if order_type.lower() != "limit":
            raise ValueError("coinone adapter currently supports limit only")
        quote_currency, target_currency = _to_pair(ticker)
        side_value = "SELL" if side.lower() in {"ask", "sell"} else "BUY"
        return self._request(
            "/v2.1/order",
            {
                "side": side_value,
                "quote_currency": quote_currency,
                "target_currency": target_currency,
                "type": "LIMIT",
                "price": str(price),
                "qty": str(volume),
                "post_only": False,
            },
        )

    def cancel_order(self, order_id: str, *, ticker: str) -> dict[str, Any]:
        if not order_id:
            raise ValueError("order_id is required")
        quote_currency, target_currency = _to_pair(ticker)
        return self._request(
            "/v2.1/order/cancel",
            {
                "order_id": order_id,
                "quote_currency": quote_currency,
                "target_currency": target_currency,
            },
        )

    def get_open_orders(
        self,
        *,
        ticker: str,
        side: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        _ = limit
        quote_currency, target_currency = _to_pair(ticker)
        data = self._request(
            "/v2.1/order/active_orders",
            {
                "quote_currency": quote_currency,
                "target_currency": target_currency,
                "order_type": ["LIMIT"],
            },
        )
        rows = data.get("active_orders")
        if not isinstance(rows, list):
            return []
        wanted = None if side is None else side.lower()
        result: list[dict[str, Any]] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            side_raw = str(item.get("side") or "").strip().lower()
            normalized_side = "ask" if side_raw == "sell" else "bid" if side_raw == "buy" else side_raw
            if wanted is not None and normalized_side != wanted:
                continue
            result.append(
                {
                    "order_id": item.get("order_id"),
                    "price": item.get("price"),
                    "remaining_volume": item.get("remain_qty"),
                    "volume": item.get("original_qty"),
                    "side": normalized_side,
                }
            )
        return result
