from __future__ import annotations

import hashlib
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import jwt
import requests

from src.core.credentials import CredentialSource, load_credentials
from src.core.event_constants import EXCHANGE_UPBIT, SOURCE_ORDERBOOK


class UpbitRestError(Exception):
    """Raised when an Upbit REST API request fails."""


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
    raw = ticker.strip()
    normalized = raw.upper()
    raw_parts = raw.split("-", 1)
    parts = normalized.split("-", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"invalid ticker '{ticker}'. expected format like 'btc-krw' or 'KRW-BTC'"
        )
    first, second = parts
    quote_currencies = {"KRW", "BTC", "USDT"}

    # Preserve explicit Upbit market codes such as "KRW-BTC".
    if (
        len(raw_parts) == 2
        and raw_parts[0].isupper()
        and first in quote_currencies
    ):
        return f"{first}-{second}"

    if second in quote_currencies:
        return f"{second}-{first}"
    if first in quote_currencies:
        return f"{first}-{second}"
    return f"{second}-{first}"


class UpbitRest:
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        *,
        api_url: str = "https://api.upbit.com",
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
    def from_info_yaml(cls, file_path: str = "info.yaml") -> "UpbitRest":
        return cls.from_config(source="info_yaml", file_path=file_path)

    @classmethod
    def from_config(
        cls,
        *,
        source: CredentialSource = "auto",
        file_path: str = "info.yaml",
        env_prefix: str | None = None,
        env_primary: str | None = None,
        env_secret: str | None = None,
        yaml_primary: str | None = None,
        yaml_secret: str | None = None,
        keyring_primary: str | None = None,
        keyring_secret: str | None = None,
        keyring_service: str = "TradingTools_KR",
    ) -> "UpbitRest":
        credentials = load_credentials(
            "upbit",
            source=source,
            file_path=file_path,
            env_prefix=env_prefix,
            env_primary=env_primary,
            env_secret=env_secret,
            yaml_primary=yaml_primary,
            yaml_secret=yaml_secret,
            keyring_primary=keyring_primary,
            keyring_secret=keyring_secret,
            keyring_service=keyring_service,
        )
        return cls(api_key=credentials.api_key, secret_key=credentials.secret_key)

    @classmethod
    def from_pocket_info_yaml(
        cls,
        file_path: str = "info.yaml",
        *,
        pocket_index: int = 1,
    ) -> "UpbitRest":
        return cls.from_pocket_config(
            source="info_yaml",
            file_path=file_path,
            pocket_index=pocket_index,
        )

    @classmethod
    def from_pocket_config(
        cls,
        *,
        source: CredentialSource = "auto",
        file_path: str = "info.yaml",
        pocket_index: int = 1,
        env_prefix: str | None = None,
        env_primary: str | None = None,
        env_secret: str | None = None,
        yaml_primary: str | None = None,
        yaml_secret: str | None = None,
        keyring_primary: str | None = None,
        keyring_secret: str | None = None,
        keyring_service: str = "TradingTools_KR",
    ) -> "UpbitRest":
        if pocket_index < 1 or pocket_index > 5:
            raise ValueError("pocket_index must be between 1 and 5")
        exchange_key = f"upbit_pocket_{pocket_index}"
        credentials = load_credentials(
            exchange_key,
            source=source,
            file_path=file_path,
            env_prefix=env_prefix,
            env_primary=env_primary,
            env_secret=env_secret,
            yaml_primary=yaml_primary,
            yaml_secret=yaml_secret,
            keyring_primary=keyring_primary,
            keyring_secret=keyring_secret,
            keyring_service=keyring_service,
        )
        return cls(api_key=credentials.api_key, secret_key=credentials.secret_key)

    @staticmethod
    def _build_query_string(payload: dict[str, Any]) -> str:
        return urlencode(UpbitRest._filter_none(payload), doseq=True)

    @staticmethod
    def _filter_none(payload: dict[str, Any] | None) -> dict[str, Any]:
        if not payload:
            return {}
        return {key: value for key, value in payload.items() if value is not None}

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
        clean_params = self._filter_none(params)
        clean_json_body = self._filter_none(json_body)
        url = f"{self.api_url}{path}"
        response = self._session.request(
            method=method,
            url=url,
            headers=self._auth_headers(params=clean_params, json_body=clean_json_body),
            params=clean_params or None,
            json=clean_json_body or None,
            timeout=self.timeout_seconds,
        )

        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}

        if not response.ok:
            raise UpbitRestError(
                f"{method} {path} failed (status={response.status_code}): {payload}"
            )

        return payload

    def _public_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        clean_params = self._filter_none(params)
        url = f"{self.api_url}{path}"
        response = self._session.request(
            method=method,
            url=url,
            headers={"accept": "application/json"},
            params=clean_params or None,
            timeout=self.timeout_seconds,
        )

        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}

        if not response.ok:
            raise UpbitRestError(
                f"{method} {path} failed (status={response.status_code}): {payload}"
            )

        return payload

    @staticmethod
    def _market_list(tickers: list[str] | tuple[str, ...] | str) -> str:
        if isinstance(tickers, str):
            values = [tickers]
        else:
            values = list(tickers)
        if not values:
            raise ValueError("tickers is required")
        return ",".join(_to_market_code(ticker) for ticker in values)

    @staticmethod
    def _comma_join(values: list[str] | tuple[str, ...] | str | None) -> str | None:
        if values is None:
            return None
        if isinstance(values, str):
            return values
        return ",".join(str(value) for value in values)

    def list_trading_pairs(self, *, is_details: bool = False) -> list[dict[str, Any]]:
        data = self._public_request(
            "GET",
            "/v1/market/all",
            params={"is_details": "true" if is_details else "false"},
        )
        return data if isinstance(data, list) else []

    def get_second_candles(
        self,
        ticker: str,
        *,
        to: str | None = None,
        count: int = 200,
    ) -> list[dict[str, Any]]:
        data = self._public_request(
            "GET",
            "/v1/candles/seconds",
            params={"market": _to_market_code(ticker), "to": to, "count": count},
        )
        return data if isinstance(data, list) else []

    def get_minute_candles(
        self,
        ticker: str,
        *,
        unit: int = 1,
        to: str | None = None,
        count: int = 200,
    ) -> list[dict[str, Any]]:
        if unit not in {1, 3, 5, 10, 15, 30, 60, 240}:
            raise ValueError("unit must be one of: 1, 3, 5, 10, 15, 30, 60, 240")
        data = self._public_request(
            "GET",
            f"/v1/candles/minutes/{unit}",
            params={"market": _to_market_code(ticker), "to": to, "count": count},
        )
        return data if isinstance(data, list) else []

    def get_day_candles(
        self,
        ticker: str,
        *,
        to: str | None = None,
        count: int = 200,
        converting_price_unit: str | None = None,
    ) -> list[dict[str, Any]]:
        data = self._public_request(
            "GET",
            "/v1/candles/days",
            params={
                "market": _to_market_code(ticker),
                "to": to,
                "count": count,
                "converting_price_unit": converting_price_unit,
            },
        )
        return data if isinstance(data, list) else []

    def get_week_candles(
        self,
        ticker: str,
        *,
        to: str | None = None,
        count: int = 200,
    ) -> list[dict[str, Any]]:
        data = self._public_request(
            "GET",
            "/v1/candles/weeks",
            params={"market": _to_market_code(ticker), "to": to, "count": count},
        )
        return data if isinstance(data, list) else []

    def get_month_candles(
        self,
        ticker: str,
        *,
        to: str | None = None,
        count: int = 200,
    ) -> list[dict[str, Any]]:
        data = self._public_request(
            "GET",
            "/v1/candles/months",
            params={"market": _to_market_code(ticker), "to": to, "count": count},
        )
        return data if isinstance(data, list) else []

    def get_year_candles(
        self,
        ticker: str,
        *,
        to: str | None = None,
        count: int = 200,
    ) -> list[dict[str, Any]]:
        data = self._public_request(
            "GET",
            "/v1/candles/years",
            params={"market": _to_market_code(ticker), "to": to, "count": count},
        )
        return data if isinstance(data, list) else []

    def get_trades(
        self,
        ticker: str,
        *,
        to: str | None = None,
        count: int = 200,
        cursor: str | None = None,
        days_ago: int | None = None,
    ) -> list[dict[str, Any]]:
        data = self._public_request(
            "GET",
            "/v1/trades/ticks",
            params={
                "market": _to_market_code(ticker),
                "to": to,
                "count": count,
                "cursor": cursor,
                "days_ago": days_ago,
            },
        )
        return data if isinstance(data, list) else []

    def get_ticker(self, tickers: list[str] | tuple[str, ...] | str) -> list[dict[str, Any]]:
        data = self._public_request(
            "GET",
            "/v1/ticker",
            params={"markets": self._market_list(tickers)},
        )
        return data if isinstance(data, list) else []

    def get_quote_tickers(
        self,
        quote_currencies: list[str] | tuple[str, ...] | str,
    ) -> list[dict[str, Any]]:
        data = self._public_request(
            "GET",
            "/v1/ticker/all",
            params={"quote_currencies": self._comma_join(quote_currencies)},
        )
        return data if isinstance(data, list) else []

    def get_orderbook_policy(
        self,
        tickers: list[str] | tuple[str, ...] | str,
    ) -> Any:
        return self._public_request(
            "GET",
            "/v1/orderbook/instruments",
            params={"markets": self._market_list(tickers)},
        )

    def get_orderbook(
        self,
        ticker: str,
        *,
        level: str | int | None = None,
        count: int | None = None,
    ) -> dict[str, Any]:
        payload = self._public_request(
            "GET",
            "/v1/orderbook",
            params={
                "markets": _to_market_code(ticker),
                "level": level,
                "count": count,
            },
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
            "exchange": EXCHANGE_UPBIT,
            "ticker": key,
            "asks": asks,
            "bids": bids,
            "updated_at": time.time(),
        }

    def get_order(
        self,
        uuid_value: str | None = None,
        *,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        if not uuid_value and not identifier:
            raise ValueError("uuid_value or identifier is required")
        data = self._request(
            "GET",
            "/v1/order",
            params={"uuid": uuid_value, "identifier": identifier},
        )
        return data if isinstance(data, dict) else {"data": data}

    def get_order_chance(self, ticker: str) -> dict[str, Any]:
        data = self._request(
            "GET",
            "/v1/orders/chance",
            params={"market": _to_market_code(ticker)},
        )
        return data if isinstance(data, dict) else {"data": data}

    def list_orders_by_ids(
        self,
        *,
        ticker: str | None = None,
        uuids: list[str] | tuple[str, ...] | None = None,
        identifiers: list[str] | tuple[str, ...] | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        if not uuids and not identifiers:
            raise ValueError("uuids or identifiers is required")
        params: dict[str, Any] = {
            "market": _to_market_code(ticker) if ticker else None,
            "uuids[]": list(uuids) if uuids is not None else None,
            "identifiers[]": list(identifiers) if identifiers is not None else None,
            "order_by": order_by,
        }
        data = self._request("GET", "/v1/orders/uuids", params=params)
        return data if isinstance(data, list) else []

    def list_orders(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_orders_by_ids(**kwargs)

    def list_open_orders(
        self,
        *,
        ticker: str | None = None,
        state: str | None = None,
        states: list[str] | tuple[str, ...] | None = None,
        page: int | None = None,
        limit: int | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            "/v1/orders/open",
            params={
                "market": _to_market_code(ticker) if ticker else None,
                "state": state,
                "states[]": list(states) if states is not None else None,
                "page": page,
                "limit": limit,
                "order_by": order_by,
            },
        )
        return data if isinstance(data, list) else []

    def list_closed_orders(
        self,
        *,
        ticker: str | None = None,
        state: str | None = None,
        states: list[str] | tuple[str, ...] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page: int | None = None,
        limit: int | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            "/v1/orders/closed",
            params={
                "market": _to_market_code(ticker) if ticker else None,
                "state": state,
                "states[]": list(states) if states is not None else None,
                "start_time": start_time,
                "end_time": end_time,
                "page": page,
                "limit": limit,
                "order_by": order_by,
            },
        )
        return data if isinstance(data, list) else []

    def _build_order_body(
        self,
        *,
        ticker: str,
        side: str,
        order_type: str,
        price: str | float | int | None = None,
        volume: str | float | int | None = None,
        identifier: str | None = None,
        time_in_force: str | None = None,
        smp_type: str | None = None,
    ) -> dict[str, Any]:
        side_lower = side.lower()
        order_type_lower = order_type.lower()

        if side_lower not in {"bid", "ask"}:
            raise ValueError("side must be 'bid' or 'ask'")
        if order_type_lower not in {"limit", "price", "market", "best"}:
            raise ValueError("order_type must be one of: limit, price, market, best")

        market = _to_market_code(ticker)
        body: dict[str, Any] = {
            "market": market,
            "side": side_lower,
            "ord_type": order_type_lower,
            "identifier": identifier,
            "time_in_force": time_in_force,
            "smp_type": smp_type,
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
        elif order_type_lower == "best":
            if time_in_force not in {"ioc", "fok"}:
                raise ValueError("best order_type requires time_in_force ioc or fok")
            if side_lower == "bid" and price is None:
                raise ValueError("best bid order requires price")
            if side_lower == "ask" and volume is None:
                raise ValueError("best ask order requires volume")

        return body

    def place_order(
        self,
        *,
        ticker: str,
        side: str,
        order_type: str,
        price: str | float | int | None = None,
        volume: str | float | int | None = None,
        identifier: str | None = None,
        time_in_force: str | None = None,
        smp_type: str | None = None,
    ) -> dict[str, Any]:
        body = self._build_order_body(
            ticker=ticker,
            side=side,
            order_type=order_type,
            price=price,
            volume=volume,
            identifier=identifier,
            time_in_force=time_in_force,
            smp_type=smp_type,
        )
        data = self._request("POST", "/v1/orders", json_body=body)
        return data if isinstance(data, dict) else {"data": data}

    def test_order(
        self,
        *,
        ticker: str,
        side: str,
        order_type: str,
        price: str | float | int | None = None,
        volume: str | float | int | None = None,
        identifier: str | None = None,
        time_in_force: str | None = None,
        smp_type: str | None = None,
    ) -> dict[str, Any]:
        body = self._build_order_body(
            ticker=ticker,
            side=side,
            order_type=order_type,
            price=price,
            volume=volume,
            identifier=identifier,
            time_in_force=time_in_force,
            smp_type=smp_type,
        )
        data = self._request("POST", "/v1/orders/test", json_body=body)
        return data if isinstance(data, dict) else {"data": data}

    def cancel_order(
        self,
        uuid_value: str | None = None,
        *,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        if not uuid_value and not identifier:
            raise ValueError("uuid_value or identifier is required")
        data = self._request(
            "DELETE",
            "/v1/order",
            params={"uuid": uuid_value, "identifier": identifier},
        )
        return data if isinstance(data, dict) else {"data": data}

    def cancel_orders_by_ids(
        self,
        *,
        uuids: list[str] | tuple[str, ...] | None = None,
        identifiers: list[str] | tuple[str, ...] | None = None,
    ) -> Any:
        if not uuids and not identifiers:
            raise ValueError("uuids or identifiers is required")
        return self._request(
            "DELETE",
            "/v1/orders/uuids",
            params={
                "uuids[]": list(uuids) if uuids is not None else None,
                "identifiers[]": list(identifiers) if identifiers is not None else None,
            },
        )

    def cancel_all_orders(
        self,
        *,
        quote_currencies: list[str] | tuple[str, ...] | str | None = None,
        cancel_side: str | None = None,
        count: int | None = None,
        order_by: str | None = None,
        pairs: list[str] | tuple[str, ...] | str | None = None,
        exclude_pairs: list[str] | tuple[str, ...] | str | None = None,
    ) -> Any:
        return self._request(
            "DELETE",
            "/v1/orders/open",
            params={
                "quote_currencies": self._comma_join(quote_currencies),
                "cancel_side": cancel_side,
                "count": count,
                "order_by": order_by,
                "pairs": self._comma_join(pairs),
                "exclude_pairs": self._comma_join(exclude_pairs),
            },
        )

    def cancel_and_new_order(
        self,
        *,
        prev_order_uuid: str | None = None,
        prev_order_identifier: str | None = None,
        new_order_type: str,
        new_price: str | float | int | None = None,
        new_volume: str | float | int | None = None,
        new_identifier: str | None = None,
        new_time_in_force: str | None = None,
        new_smp_type: str | None = None,
    ) -> dict[str, Any]:
        if not prev_order_uuid and not prev_order_identifier:
            raise ValueError("prev_order_uuid or prev_order_identifier is required")

        body: dict[str, Any] = {
            "prev_order_uuid": prev_order_uuid,
            "prev_order_identifier": prev_order_identifier,
            "new_ord_type": new_order_type,
            "new_identifier": new_identifier,
            "new_time_in_force": new_time_in_force,
            "new_smp_type": new_smp_type,
        }
        if new_price is not None:
            body["new_price"] = str(new_price)
        if new_volume is not None:
            body["new_volume"] = str(new_volume)

        data = self._request("POST", "/v1/orders/cancel_and_new", json_body=body)
        return data if isinstance(data, dict) else {"data": data}

    def get_accounts(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v1/accounts")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        return []

    def get_pocket(self) -> dict[str, Any]:
        data = self._request("GET", "/v1/pockets")
        return data if isinstance(data, dict) else {"data": data}

    def list_pocket_api_keys(
        self,
        *,
        uuids: list[str] | tuple[str, ...] | None = None,
        include_expired: bool | None = None,
    ) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            "/v1/pockets/api_keys",
            params={
                "uuids[]": list(uuids) if uuids is not None else None,
                "include_expired": include_expired,
            },
        )
        return data if isinstance(data, list) else []

    def get_subpocket_accounts(
        self,
        *,
        uuid_value: str,
    ) -> list[dict[str, Any]]:
        if not uuid_value:
            raise ValueError("uuid_value is required")
        data = self._request(
            "GET",
            "/v1/pockets/assets",
            params={"uuid": uuid_value},
        )
        return data if isinstance(data, list) else []

    def create_main_pocket_transfer(
        self,
        *,
        to: str,
        currency: str,
        amount: str | float | int,
        from_uuid: str | None = None,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "from": from_uuid,
            "to": to,
            "currency": currency,
            "amount": str(amount),
            "identifier": identifier,
        }
        data = self._request("POST", "/v1/pockets/universal_transfers", json_body=body)
        return data if isinstance(data, dict) else {"data": data}

    def list_main_pocket_transfers(
        self,
        *,
        from_uuid: str | None = None,
        to: str | None = None,
        currency: str | None = None,
        states: list[str] | tuple[str, ...] | None = None,
        uuids: list[str] | tuple[str, ...] | None = None,
        identifiers: list[str] | tuple[str, ...] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            "/v1/pockets/universal_transfers",
            params={
                "from": from_uuid,
                "to": to,
                "currency": currency,
                "states[]": list(states) if states is not None else None,
                "uuids[]": list(uuids) if uuids is not None else None,
                "identifiers[]": list(identifiers)
                if identifiers is not None
                else None,
                "start_time": start_time,
                "end_time": end_time,
                "limit": limit,
                "order_by": order_by,
            },
        )
        return data if isinstance(data, list) else []

    def create_subpocket_transfer(
        self,
        *,
        currency: str,
        amount: str | float | int,
        to: str | None = None,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "to": to,
            "currency": currency,
            "amount": str(amount),
            "identifier": identifier,
        }
        data = self._request("POST", "/v1/pockets/transfers", json_body=body)
        return data if isinstance(data, dict) else {"data": data}

    def list_subpocket_transfers(
        self,
        *,
        currency: str | None = None,
        direction: str | None = None,
        to: str | None = None,
        states: list[str] | tuple[str, ...] | None = None,
        uuids: list[str] | tuple[str, ...] | None = None,
        identifiers: list[str] | tuple[str, ...] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            "/v1/pockets/transfers",
            params={
                "currency": currency,
                "direction": direction,
                "to": to,
                "states[]": list(states) if states is not None else None,
                "uuids[]": list(uuids) if uuids is not None else None,
                "identifiers[]": list(identifiers)
                if identifiers is not None
                else None,
                "start_time": start_time,
                "end_time": end_time,
                "limit": limit,
                "order_by": order_by,
            },
        )
        return data if isinstance(data, list) else []

    def get_withdraw_chance(self, *, currency: str, net_type: str | None = None) -> dict[str, Any]:
        data = self._request(
            "GET",
            "/v1/withdraws/chance",
            params={"currency": currency, "net_type": net_type},
        )
        return data if isinstance(data, dict) else {"data": data}

    def list_withdraw_addresses(self, *, currency: str | None = None, net_type: str | None = None) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            "/v1/withdraws/coin_addresses",
            params={"currency": currency, "net_type": net_type},
        )
        return data if isinstance(data, list) else []

    def get_withdraw(self, *, uuid_value: str | None = None, txid: str | None = None, currency: str | None = None) -> dict[str, Any]:
        if not uuid_value and not txid:
            raise ValueError("uuid_value or txid is required")
        data = self._request(
            "GET",
            "/v1/withdraw",
            params={"uuid": uuid_value, "txid": txid, "currency": currency},
        )
        return data if isinstance(data, dict) else {"data": data}

    def list_withdraws(
        self,
        *,
        currency: str | None = None,
        state: str | None = None,
        uuids: list[str] | tuple[str, ...] | None = None,
        txids: list[str] | tuple[str, ...] | None = None,
        page: int | None = None,
        limit: int | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            "/v1/withdraws",
            params={
                "currency": currency,
                "state": state,
                "uuids[]": list(uuids) if uuids is not None else None,
                "txids[]": list(txids) if txids is not None else None,
                "page": page,
                "limit": limit,
                "order_by": order_by,
            },
        )
        return data if isinstance(data, list) else []

    def create_coin_withdraw(
        self,
        *,
        currency: str,
        net_type: str,
        amount: str | float | int,
        address: str,
        secondary_address: str | None = None,
        transaction_type: str | None = None,
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            "/v1/withdraws/coin",
            json_body={
                "currency": currency,
                "amount": str(amount),
                "address": address,
                "secondary_address": secondary_address,
                "transaction_type": transaction_type,
                "net_type": net_type,
            },
        )
        return data if isinstance(data, dict) else {"data": data}

    def create_krw_withdraw(
        self,
        *,
        amount: str | float | int,
        two_factor_type: str,
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            "/v1/withdraws/krw",
            json_body={"amount": str(amount), "two_factor_type": two_factor_type},
        )
        return data if isinstance(data, dict) else {"data": data}

    def cancel_withdraw(self, *, uuid_value: str) -> dict[str, Any]:
        if not uuid_value:
            raise ValueError("uuid is required")
        data = self._request("DELETE", "/v1/withdraws/coin", params={"uuid": uuid_value})
        return data if isinstance(data, dict) else {"data": data}

    def list_deposit_addresses(self, *, currency: str | None = None, net_type: str | None = None) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            "/v1/deposits/coin_addresses",
            params={"currency": currency, "net_type": net_type},
        )
        return data if isinstance(data, list) else []

    def get_deposit_address(self, *, currency: str, net_type: str) -> dict[str, Any]:
        data = self._request(
            "GET",
            "/v1/deposits/coin_address",
            params={"currency": currency, "net_type": net_type},
        )
        return data if isinstance(data, dict) else {"data": data}

    def create_deposit_address(self, *, currency: str, net_type: str) -> dict[str, Any]:
        data = self._request(
            "POST",
            "/v1/deposits/generate_coin_address",
            json_body={"currency": currency, "net_type": net_type},
        )
        return data if isinstance(data, dict) else {"data": data}

    def get_deposit(self, *, uuid_value: str | None = None, txid: str | None = None, currency: str | None = None) -> dict[str, Any]:
        if not uuid_value and not txid:
            raise ValueError("uuid_value or txid is required")
        data = self._request(
            "GET",
            "/v1/deposit",
            params={"uuid": uuid_value, "txid": txid, "currency": currency},
        )
        return data if isinstance(data, dict) else {"data": data}

    def list_deposits(
        self,
        *,
        currency: str | None = None,
        state: str | None = None,
        uuids: list[str] | tuple[str, ...] | None = None,
        txids: list[str] | tuple[str, ...] | None = None,
        page: int | None = None,
        limit: int | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            "/v1/deposits",
            params={
                "currency": currency,
                "state": state,
                "uuids[]": list(uuids) if uuids is not None else None,
                "txids[]": list(txids) if txids is not None else None,
                "page": page,
                "limit": limit,
                "order_by": order_by,
            },
        )
        return data if isinstance(data, list) else []

    def get_deposit_chance(self, *, currency: str, net_type: str) -> dict[str, Any]:
        data = self._request(
            "GET",
            "/v1/deposits/chance/coin",
            params={"currency": currency, "net_type": net_type},
        )
        return data if isinstance(data, dict) else {"data": data}

    def list_deposit_allowed_assets(self, *, currency: str, net_type: str) -> dict[str, Any]:
        return self.get_deposit_chance(currency=currency, net_type=net_type)

    def create_krw_deposit(self, *, amount: str | float | int) -> dict[str, Any]:
        data = self._request(
            "POST",
            "/v1/deposits/krw",
            json_body={"amount": str(amount)},
        )
        return data if isinstance(data, dict) else {"data": data}

    def list_travel_rule_vasps(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v1/travel_rule/vasps")
        return data if isinstance(data, list) else []

    def verify_travel_rule_by_deposit_uuid(
        self,
        *,
        deposit_uuid: str,
        vasp_uuid: str,
        **extra_body: Any,
    ) -> dict[str, Any]:
        body = {
            "deposit_uuid": deposit_uuid,
            "vasp_uuid": vasp_uuid,
            **extra_body,
        }
        data = self._request(
            "POST",
            "/v1/travel_rule/deposit/uuid",
            json_body=body,
        )
        return data if isinstance(data, dict) else {"data": data}

    def verify_travel_rule_by_deposit_txid(
        self,
        *,
        txid: str,
        currency: str,
        net_type: str,
        vasp_uuid: str,
        **extra_body: Any,
    ) -> dict[str, Any]:
        body = {
            "txid": txid,
            "currency": currency,
            "net_type": net_type,
            "vasp_uuid": vasp_uuid,
            **extra_body,
        }
        data = self._request(
            "POST",
            "/v1/travel_rule/deposit/txid",
            json_body=body,
        )
        return data if isinstance(data, dict) else {"data": data}

    def list_wallet_statuses(self) -> Any:
        return self._request("GET", "/v1/status/wallet")

    def list_api_keys(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/v1/api_keys")
        return data if isinstance(data, list) else []


def tradeTest(client: UpbitRest, test_ticker: str = "usdt-krw") -> None:
    order_uuid: str | None = None

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
            raise UpbitRestError(f"orderbook_units is empty: {orderbook}")

        best_unit = units[0]
        best_bid_price = best_unit.get("bid_price")
        if best_bid_price is None:
            raise UpbitRestError(f"best bid price is missing: {best_unit}")

        print(f"[TEST] best bid price={best_bid_price}")
        placed = client.place_order(
            ticker=test_ticker,
            side="bid",
            order_type="limit",
            price=str(best_bid_price),
            volume="5",
        )
        # Example return: {"uuid": "b3e8bdb2-086f-4144-a483-32019af9e05c", "side": "bid", "ord_type": "limit", "price": "1476", "state": "wait", "market": "KRW-USDT", "created_at": "2026-02-15T20:48:49+09:00", "volume": "5", "remaining_volume": "5", "reserved_fee": "3.69", "remaining_fee": "3.69", "paid_fee": "0", "locked": "7383.69", "executed_volume": "0", "trades_count": 0}
        print("[TEST] place_order response:", placed)

        if isinstance(placed, dict):
            order_uuid = str(placed.get("uuid") or "")
        if not order_uuid:
            raise UpbitRestError(f"uuid missing in place response: {placed}")

        print("[TEST] 3) Get order")
        order = client.get_order(order_uuid)
        # Example return: {"uuid": "b3e8bdb2-086f-4144-a483-32019af9e05c", "side": "bid", "ord_type": "limit", "state": "wait", "market": "KRW-USDT", "price": "1476", "volume": "5", "remaining_volume": "5", "executed_volume": "0", "paid_fee": "0", "created_at": "2026-02-15T20:48:49+09:00", "trades_count": 0}
        print(order)

    finally:
        if order_uuid:
            try:
                print("[TEST] 4) Cancel order")
                cancelled = client.cancel_order(order_uuid)
                # Example return: {"uuid": "b3e8bdb2-086f-4144-a483-32019af9e05c", "side": "bid", "ord_type": "limit", "state": "cancel", "market": "KRW-USDT", "price": "1476", "volume": "5", "remaining_volume": "5", "executed_volume": "0"}
                print(cancelled)
            except Exception as cancel_err:
                print(f"[TEST] cancel failed: {cancel_err}")


def orderbookTest(client: UpbitRest, test_ticker: str = "usdt-krw") -> None:
    print("[TEST] 1) Get orderbook (raw)")
    orderbook = client.get_orderbook(test_ticker)
    print(orderbook)

    print("[TEST] 2) Get orderbook (parsed)")
    parsed = client.get_orderbook_parse(test_ticker, count=5)
    print(parsed)


if __name__ == "__main__":
    client = UpbitRest.from_info_yaml("info.yaml")
    test_ticker = "usdt-krw"

    # Default to orderbook test for quicker non-trading checks.
    # Use `python upbit_rest.py trade` to run tradeTest.
    if len(sys.argv) > 1 and sys.argv[1].lower() in {
        "trade",
        "trade_test",
        "tradetest",
    }:
        tradeTest(client, test_ticker)
    else:
        orderbookTest(client, test_ticker)
