from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from pathlib import Path
from typing import Any

import requests

from src.core.credentials import CredentialSource, load_credentials
from src.core.event_constants import EXCHANGE_COINONE, SOURCE_ORDERBOOK


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


def _filter_none(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    return {key: value for key, value in payload.items() if value is not None}


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
        return cls.from_config(source="info_yaml", file_path=file_path)

    @classmethod
    def from_config(
        cls,
        *,
        source: CredentialSource = "auto",
        file_path: str = "info.yaml",
    ) -> "CoinoneRest":
        credentials = load_credentials("coinone", source=source, file_path=file_path)
        return cls(
            access_token=credentials.access_token,
            secret_key=credentials.secret_key,
        )

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

    def _request(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "access_token": self.access_token,
            "nonce": str(uuid.uuid4()),
            **_filter_none(body),
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
        return data if isinstance(data, dict) else {"data": data}

    def _public_get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._session.get(
            f"{self.api_url}{path}",
            params=_filter_none(params) or None,
            timeout=self.timeout_seconds,
        )
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text}
        if not response.ok:
            raise CoinoneRestError(f"GET {path} failed status={response.status_code}: {data}")
        if isinstance(data, dict) and str(data.get("result", "")).lower() == "error":
            raise CoinoneRestError(f"GET {path} error: {data}")
        return data if isinstance(data, dict) else {"data": data}

    @staticmethod
    def _currency_list(currencies: list[str] | tuple[str, ...] | str) -> list[str]:
        values = [currencies] if isinstance(currencies, str) else list(currencies)
        if not values:
            raise ValueError("currencies is required")
        return [currency.upper() for currency in values]

    def get_range_units(self, ticker: str) -> dict[str, Any]:
        quote_currency, target_currency = _to_pair(ticker)
        return self._public_get(f"/public/v2/range_units/{quote_currency}/{target_currency}")

    def list_markets(self, *, quote_currency: str = "KRW") -> dict[str, Any]:
        return self._public_get(f"/public/v2/markets/{quote_currency.upper()}")

    def get_market(self, ticker: str) -> dict[str, Any]:
        quote_currency, target_currency = _to_pair(ticker)
        return self._public_get(f"/public/v2/markets/{quote_currency}/{target_currency}")

    def get_orderbook(
        self,
        ticker: str,
        *,
        size: int | None = None,
        order_book_unit: str | float | int | None = None,
    ) -> dict[str, Any]:
        quote_currency, target_currency = _to_pair(ticker)
        return self._public_get(
            f"/public/v2/orderbook/{quote_currency}/{target_currency}",
            params={"size": size, "order_book_unit": order_book_unit},
        )

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def get_orderbook_parse(self, ticker: str, *, count: int = 5) -> dict[str, Any]:
        if count <= 0:
            raise ValueError("count must be greater than 0")

        raw_book = self.get_orderbook(ticker, size=count)
        asks: list[dict[str, float]] = []
        bids: list[dict[str, float]] = []

        for raw, target in ((raw_book.get("asks"), asks), (raw_book.get("bids"), bids)):
            if not isinstance(raw, list):
                continue
            for level in raw[:count]:
                if not isinstance(level, dict):
                    continue
                price = self._to_float(level.get("price"))
                qty = self._to_float(level.get("qty"))
                if price is not None and qty is not None:
                    target.append({"price": price, "qty": qty})

        return {
            "source": SOURCE_ORDERBOOK,
            "exchange": EXCHANGE_COINONE,
            "ticker": ticker.split("-", 1)[0].strip().lower(),
            "asks": asks,
            "bids": bids,
        }

    def get_trades(self, ticker: str, *, size: int | None = None) -> dict[str, Any]:
        quote_currency, target_currency = _to_pair(ticker)
        return self._public_get(
            f"/public/v2/trades/{quote_currency}/{target_currency}",
            params={"size": size},
        )

    def list_tickers(
        self,
        *,
        quote_currency: str = "KRW",
        additional_data: bool | None = None,
    ) -> dict[str, Any]:
        return self._public_get(
            f"/public/v2/ticker_new/{quote_currency.upper()}",
            params={"additional_data": additional_data},
        )

    def get_ticker(
        self,
        ticker: str,
        *,
        additional_data: bool | None = None,
    ) -> dict[str, Any]:
        quote_currency, target_currency = _to_pair(ticker)
        return self._public_get(
            f"/public/v2/ticker_new/{quote_currency}/{target_currency}",
            params={"additional_data": additional_data},
        )

    def list_utc_tickers(
        self,
        *,
        quote_currency: str = "KRW",
        additional_data: bool | None = None,
    ) -> dict[str, Any]:
        return self._public_get(
            f"/public/v2/ticker_utc_new/{quote_currency.upper()}",
            params={"additional_data": additional_data},
        )

    def get_utc_ticker(
        self,
        ticker: str,
        *,
        additional_data: bool | None = None,
    ) -> dict[str, Any]:
        quote_currency, target_currency = _to_pair(ticker)
        return self._public_get(
            f"/public/v2/ticker_utc_new/{quote_currency}/{target_currency}",
            params={"additional_data": additional_data},
        )

    def list_currencies(self) -> dict[str, Any]:
        return self._public_get("/public/v2/currencies")

    def get_currency(self, currency: str) -> dict[str, Any]:
        return self._public_get(f"/public/v2/currencies/{currency.upper()}")

    def get_candles(
        self,
        ticker: str,
        *,
        interval: str,
        timestamp: str | int | None = None,
        size: int | None = None,
    ) -> dict[str, Any]:
        quote_currency, target_currency = _to_pair(ticker)
        return self._public_get(
            f"/public/v2/chart/{quote_currency}/{target_currency}",
            params={"interval": interval, "timestamp": timestamp, "size": size},
        )

    def get_accounts(self) -> list[dict[str, Any]]:
        data = self._request("/v2.1/account/balance/all")
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

    def get_balance(self, currencies: list[str] | tuple[str, ...] | str) -> dict[str, Any]:
        return self._request(
            "/v2.1/account/balance",
            {"currencies": self._currency_list(currencies)},
        )

    def list_trade_fees(self) -> dict[str, Any]:
        return self._request("/v2.1/account/trade_fee")

    def get_trade_fee(self, ticker: str) -> dict[str, Any]:
        quote_currency, target_currency = _to_pair(ticker)
        return self._request(
            "/v2.1/account/trade_fee/market",
            {"quote_currency": quote_currency, "target_currency": target_currency},
        )

    def get_deposit_address(self, currency: str) -> dict[str, Any]:
        return self._request(
            "/v2.1/account/deposit_address",
            {"currency": currency.upper()},
        )

    def place_order(
        self,
        *,
        ticker: str,
        side: str,
        order_type: str,
        price: str | float | int | None = None,
        volume: str | float | int | None = None,
        amount: str | float | int | None = None,
        post_only: bool | None = False,
        limit_price: str | float | int | None = None,
        trigger_price: str | float | int | None = None,
        user_order_id: str | None = None,
    ) -> dict[str, Any]:
        order_type_upper = order_type.upper()
        if order_type_upper == "STOP_LIMIT":
            order_type_upper = "STOP_LIMIT"
        if order_type_upper not in {"LIMIT", "MARKET", "STOP_LIMIT"}:
            raise ValueError("order_type must be one of: limit, market, stop_limit")

        quote_currency, target_currency = _to_pair(ticker)
        side_value = "SELL" if side.lower() in {"ask", "sell"} else "BUY"
        if order_type_upper == "LIMIT" and (price is None or volume is None):
            raise ValueError("limit order requires price and volume")
        if order_type_upper == "MARKET" and side_value == "BUY" and amount is None:
            raise ValueError("market buy requires amount")
        if order_type_upper == "MARKET" and side_value == "SELL" and volume is None:
            raise ValueError("market sell requires volume")
        if order_type_upper == "STOP_LIMIT" and (
            price is None or volume is None or trigger_price is None
        ):
            raise ValueError("stop_limit order requires price, volume, and trigger_price")

        return self._request(
            "/v2.1/order",
            {
                "side": side_value,
                "quote_currency": quote_currency,
                "target_currency": target_currency,
                "type": order_type_upper,
                "price": str(price) if price is not None else None,
                "qty": str(volume) if volume is not None else None,
                "amount": str(amount) if amount is not None else None,
                "post_only": post_only,
                "limit_price": str(limit_price) if limit_price is not None else None,
                "trigger_price": str(trigger_price) if trigger_price is not None else None,
                "user_order_id": user_order_id,
            },
        )

    def cancel_order(
        self,
        order_id: str | None = None,
        *,
        ticker: str,
        user_order_id: str | None = None,
    ) -> dict[str, Any]:
        if not order_id and not user_order_id:
            raise ValueError("order_id or user_order_id is required")
        quote_currency, target_currency = _to_pair(ticker)
        return self._request(
            "/v2.1/order/cancel",
            {
                "order_id": order_id,
                "user_order_id": user_order_id,
                "quote_currency": quote_currency,
                "target_currency": target_currency,
            },
        )

    def cancel_all_orders(
        self,
        *,
        ticker: str,
        order_type: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        quote_currency, target_currency = _to_pair(ticker)
        return self._request(
            "/v2.1/order/cancel/all",
            {
                "quote_currency": quote_currency,
                "target_currency": target_currency,
                "order_type": list(order_type) if order_type is not None else None,
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
                "order_type": ["LIMIT", "STOP_LIMIT"],
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

    def list_all_open_orders(self) -> dict[str, Any]:
        return self._request("/v2.1/order/active_orders/all")

    def list_open_orders_by_market(
        self,
        *,
        ticker: str,
        order_type: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        quote_currency, target_currency = _to_pair(ticker)
        return self._request(
            "/v2.1/order/active_orders/market",
            {
                "quote_currency": quote_currency,
                "target_currency": target_currency,
                "order_type": list(order_type) if order_type is not None else None,
            },
        )

    def get_order(
        self,
        *,
        ticker: str,
        order_id: str | None = None,
        user_order_id: str | None = None,
    ) -> dict[str, Any]:
        if not order_id and not user_order_id:
            raise ValueError("order_id or user_order_id is required")
        quote_currency, target_currency = _to_pair(ticker)
        return self._request(
            "/v2.1/order/order_info",
            {
                "quote_currency": quote_currency,
                "target_currency": target_currency,
                "order_id": order_id,
                "user_order_id": user_order_id,
            },
        )

    def get_order_detail(self, *, ticker: str, order_id: str) -> dict[str, Any]:
        quote_currency, target_currency = _to_pair(ticker)
        return self._request(
            "/v2.1/order",
            {
                "quote_currency": quote_currency,
                "target_currency": target_currency,
                "order_id": order_id,
            },
        )

    def list_completed_orders(
        self,
        *,
        ticker: str | None = None,
        size: int | None = None,
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"size": size, "from_ts": from_ts, "to_ts": to_ts}
        if ticker:
            quote_currency, target_currency = _to_pair(ticker)
            body.update({"quote_currency": quote_currency, "target_currency": target_currency})
            return self._request("/v2.1/order/completed_orders/market", body)
        return self._request("/v2.1/order/completed_orders/all", body)

    def list_krw_transactions(
        self,
        *,
        type_: str | None = None,
        size: int | None = None,
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "/v2.1/transaction/krw/history",
            {"type": type_, "size": size, "from_ts": from_ts, "to_ts": to_ts},
        )

    def list_coin_transactions(
        self,
        *,
        currency: str | None = None,
        type_: str | None = None,
        size: int | None = None,
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "/v2.1/transaction/coin/history",
            {
                "currency": currency.upper() if currency else None,
                "type": type_,
                "size": size,
                "from_ts": from_ts,
                "to_ts": to_ts,
            },
        )

    def get_coin_transaction(self, *, transaction_id: str) -> dict[str, Any]:
        if not transaction_id:
            raise ValueError("transaction_id is required")
        return self._request(
            "/v2.1/transaction/coin",
            {"transaction_id": transaction_id},
        )

    def get_withdraw_limit(self, *, currency: str) -> dict[str, Any]:
        return self._request(
            "/v2.1/transaction/coin/withdrawal/limit",
            {"currency": currency.upper()},
        )

    def list_withdraw_addresses(self, *, currency: str | None = None) -> dict[str, Any]:
        return self._request(
            "/v2.1/transaction/coin/withdrawal/address",
            {"currency": currency.upper() if currency else None},
        )

    def create_coin_withdraw(
        self,
        *,
        currency: str,
        amount: str | float | int,
        address: str,
        secondary_address: str | None = None,
        user_transfer_id: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "/v2.1/transaction/coin/withdrawal",
            {
                "currency": currency.upper(),
                "amount": str(amount),
                "address": address,
                "secondary_address": secondary_address,
                "user_transfer_id": user_transfer_id,
            },
        )

    def get_order_reward_markets(self) -> dict[str, Any]:
        return self._request("/v2.1/order/reward/markets")

    def list_order_rewards(
        self,
        *,
        ticker: str | None = None,
        size: int | None = None,
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"size": size, "from_ts": from_ts, "to_ts": to_ts}
        if ticker:
            quote_currency, target_currency = _to_pair(ticker)
            body.update({"quote_currency": quote_currency, "target_currency": target_currency})
        return self._request("/v2.1/order/reward/history", body)
