from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
import uuid

os.environ.setdefault("CRYPTOGRAPHY_OPENSSL_NO_LEGACY", "1")

import jwt
import requests

from src.core.event_constants import EXCHANGE_BITHUMB, SOURCE_ORDERBOOK


class BithumbRestError(Exception):
    """Raised when a Bithumb REST API request fails."""


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


def _to_market_code(ticker: str) -> str:
    normalized = ticker.strip().upper()
    parts = normalized.split("-", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"invalid ticker '{ticker}'. expected format like 'btc-krw'")
    base_coin, quote = parts
    return f"{quote}-{base_coin}"


class BithumbRest:
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        *,
        api_url: str = "https://api.bithumb.com",
        timeout_seconds: float = 10.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        if not secret_key:
            raise ValueError("secret_key is required")

        self.api_key = api_key
        self.secret_key = secret_key
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._session = requests.Session()

    @classmethod
    def from_info_yaml(cls, file_path: str = "info.yaml") -> "BithumbRest":
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"{file_path} not found")

        config = _parse_simple_yaml_mapping(path.read_text(encoding="utf-8"))
        api_key = config.get("bithumb_api_key")
        secret_key = config.get("bithumb_secret_key")

        if not api_key or not secret_key:
            raise ValueError(
                "info.yaml must include 'bithumb_api_key' and 'bithumb_secret_key'"
            )

        return cls(api_key=str(api_key), secret_key=str(secret_key))

    @staticmethod
    def _build_query_string(payload: dict[str, Any]) -> str:
        filtered: dict[str, Any] = {}
        for key, value in payload.items():
            if value is None:
                continue
            filtered[key] = value
        return urlencode(filtered, doseq=True)

    def _auth_headers(
        self,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        jwt_payload: dict[str, Any] = {
            "access_key": self.api_key,
            "nonce": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
        }

        merged: dict[str, Any] = {}
        if params:
            merged.update(params)
        if json_body:
            merged.update(json_body)

        if merged:
            query_string = self._build_query_string(merged)
            jwt_payload["query_hash"] = hashlib.sha512(
                query_string.encode("utf-8")
            ).hexdigest()
            jwt_payload["query_hash_alg"] = "SHA512"

        token = jwt.encode(jwt_payload, self.secret_key, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.api_url}{path}"
        response = self._session.request(
            method=method,
            url=url,
            headers=self._auth_headers(params=params, json_body=json_body),
            params=params,
            json=json_body,
            timeout=self.timeout_seconds,
        )

        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}

        if not response.ok:
            raise BithumbRestError(
                f"{method} {path} failed (status={response.status_code}): {payload}"
            )

        return payload

    def get_orderbook(self, ticker: str) -> dict[str, Any]:
        market = _to_market_code(ticker)
        url = f"{self.api_url}/v1/orderbook"
        response = self._session.get(
            url,
            params={"markets": market},
            headers={"accept": "application/json"},
            timeout=self.timeout_seconds,
        )

        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}

        if not response.ok:
            raise BithumbRestError(
                f"GET /v1/orderbook failed (status={response.status_code}): {payload}"
            )

        if isinstance(payload, list) and payload:
            first = payload[0]
            return first if isinstance(first, dict) else {"data": payload}
        if isinstance(payload, dict):
            return payload
        return {"data": payload}

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def get_orderbook_parse(self, ticker: str, *, count: int = 5) -> dict[str, Any]:
        if count <= 0:
            raise ValueError("count must be greater than 0")

        key = ticker.split("-", 1)[0].strip().lower()
        raw_book = self.get_orderbook(ticker)
        raw_units = (
            raw_book.get("orderbook_units") if isinstance(raw_book, dict) else None
        )

        asks: list[dict[str, float]] = []
        bids: list[dict[str, float]] = []

        if isinstance(raw_units, list):
            for level in raw_units:
                if not isinstance(level, dict):
                    continue

                ask_price = self._to_float(level.get("ask_price"))
                ask_qty = self._to_float(level.get("ask_size"))
                if ask_price is not None and ask_qty is not None and len(asks) < count:
                    asks.append({"price": ask_price, "qty": ask_qty})

                bid_price = self._to_float(level.get("bid_price"))
                bid_qty = self._to_float(level.get("bid_size"))
                if bid_price is not None and bid_qty is not None and len(bids) < count:
                    bids.append({"price": bid_price, "qty": bid_qty})

                if len(asks) >= count and len(bids) >= count:
                    break

        return {
            "source": SOURCE_ORDERBOOK,
            "exchange": EXCHANGE_BITHUMB,
            "ticker": key,
            "asks": asks,
            "bids": bids,
            "updated_at": time.time(),
        }

    def get_order(self, uuid_value: str) -> dict[str, Any]:
        if not uuid_value:
            raise ValueError("uuid is required")
        data = self._request("GET", "/v1/order", params={"uuid": uuid_value})
        return data if isinstance(data, dict) else {"data": data}

    def list_orders(
        self,
        *,
        ticker: str,
        state: str = "wait",
        side: str | None = None,
        limit: int = 100,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit must be greater than 0")
        if page <= 0:
            raise ValueError("page must be greater than 0")

        params: dict[str, Any] = {
            "market": _to_market_code(ticker),
            "state": state,
            "limit": limit,
            "page": page,
            "order_by": "desc",
        }
        if side is not None:
            params["side"] = side.lower()

        data = self._request("GET", "/v1/orders", params=params)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return [item for item in data["data"] if isinstance(item, dict)]
        return []

    def get_open_orders(
        self,
        *,
        ticker: str,
        side: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.list_orders(ticker=ticker, state="wait", side=side, limit=limit)

    def place_order(
        self,
        *,
        ticker: str,
        side: str,
        order_type: str,
        price: str | float | int | None = None,
        volume: str | float | int | None = None,
    ) -> dict[str, Any]:
        side_lower = side.lower()
        order_type_lower = order_type.lower()

        if side_lower not in {"bid", "ask"}:
            raise ValueError("side must be 'bid' or 'ask'")
        if order_type_lower not in {"limit", "price", "market"}:
            raise ValueError("order_type must be one of: limit, price, market")

        market = _to_market_code(ticker)
        body: dict[str, Any] = {
            "market": market,
            "side": side_lower,
            "order_type": order_type_lower,
        }

        if price is not None:
            body["price"] = str(price)
        if volume is not None:
            body["volume"] = str(volume)

        if order_type_lower == "limit":
            if price is None or volume is None:
                raise ValueError("limit order requires both price and volume")
        elif order_type_lower == "price":
            if side_lower != "bid":
                raise ValueError("price order_type is for market buy(bid) only")
            if price is None:
                raise ValueError("price order_type requires price")
        elif order_type_lower == "market":
            if side_lower != "ask":
                raise ValueError("market order_type is for market sell(ask) only")
            if volume is None:
                raise ValueError("market order_type requires volume")

        data = self._request("POST", "/v2/orders", json_body=body)
        return data if isinstance(data, dict) else {"data": data}

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        if not order_id:
            raise ValueError("order_id is required")
        data = self._request("DELETE", "/v2/order", params={"order_id": order_id})
        return data if isinstance(data, dict) else {"data": data}

    def get_accounts(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v1/accounts")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        return []


def tradeTest(client: BithumbRest, test_ticker: str = "usdt-krw") -> None:
    order_id: str | None = None

    try:
        print("[TEST] 1) Get accounts")
        try:
            accounts = client.get_accounts()
            # Example return: [{"currency": "KRW", "balance": "12345.67", "locked": "0", "avg_buy_price": "0", "avg_buy_price_modified": False, "unit_currency": "KRW"}]
            print(accounts)
        except Exception as account_err:
            print(f"[TEST] get accounts failed: {account_err}")

        print("[TEST] 2) Get orderbook and place bid limit (USDT 5)")
        orderbook = client.get_orderbook(test_ticker)
        units = (
            orderbook.get("orderbook_units") if isinstance(orderbook, dict) else None
        )
        if not isinstance(units, list) or not units:
            raise BithumbRestError(f"orderbook_units is empty: {orderbook}")

        best_unit = units[0]
        best_bid_price = best_unit.get("bid_price")
        if best_bid_price is None:
            raise BithumbRestError(f"best bid price is missing: {best_unit}")

        print(f"[TEST] best bid price={best_bid_price}")
        placed = client.place_order(
            ticker=test_ticker,
            side="bid",
            order_type="limit",
            price=str(best_bid_price),
            volume="5",
        )
        # Example return: {"order_id": "1234567890", "market": "KRW-USDT", "side": "bid", "order_type": "limit", "price": "1476", "volume": "5", "state": "wait", "created_at": "2026-02-15T20:48:49+09:00"}
        print("[TEST] place_order response:", placed)

        if isinstance(placed, dict):
            order_id = str(placed.get("order_id") or "")
        if not order_id:
            raise BithumbRestError(f"order_id missing in place response: {placed}")

        print("[TEST] 3) Get order")
        order = client.get_order(order_id)
        # Example return: {"order_id": "1234567890", "market": "KRW-USDT", "side": "bid", "order_type": "limit", "state": "wait", "price": "1476", "volume": "5", "remaining_volume": "5", "executed_volume": "0", "paid_fee": "0", "created_at": "2026-02-15T20:48:49+09:00"}
        print(order)
        time.sleep(1)

    finally:
        if order_id:
            try:
                print("[TEST] 4) Cancel order")
                cancelled = client.cancel_order(order_id)
                # Example return: {"order_id": "1234567890", "market": "KRW-USDT", "side": "bid", "order_type": "limit", "state": "cancel", "price": "1476", "volume": "5", "remaining_volume": "5", "executed_volume": "0"}
                print(cancelled)
            except Exception as cancel_err:
                print(f"[TEST] cancel failed: {cancel_err}")

            try:
                print("[TEST] 5) Get order")
                order = client.get_order(order_id)
                # Example return: {"order_id": "1234567890", "market": "KRW-USDT", "side": "bid", "order_type": "limit", "state": "wait", "price": "1476", "volume": "5", "remaining_volume": "5", "executed_volume": "0", "paid_fee": "0", "created_at": "2026-02-15T20:48:49+09:00"}
                print(order)
            except Exception as order_err:
                print(f"[TEST] get order failed: {order_err}")


def orderbookTest(client: BithumbRest, test_ticker: str = "usdt-krw") -> None:
    print("[TEST] 1) Get orderbook (raw)")
    orderbook = client.get_orderbook(test_ticker)
    print(orderbook)

    print("[TEST] 2) Get orderbook (parsed)")
    parsed = client.get_orderbook_parse(test_ticker, count=5)
    print(parsed)


if __name__ == "__main__":
    client = BithumbRest.from_info_yaml("info.yaml")
    test_ticker = "usdt-krw"

    # Default to orderbook test for safer quick checks.
    # Use `python bithumb_rest.py trade` to run tradeTest.
    if len(sys.argv) > 1 and sys.argv[1].lower() in {
        "trade",
        "trade_test",
        "tradetest",
    }:
        tradeTest(client, test_ticker)
    else:
        orderbookTest(client, test_ticker)
