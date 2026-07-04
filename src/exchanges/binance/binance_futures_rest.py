from __future__ import annotations

import hashlib
import hmac
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from src.core.credentials import CredentialError, CredentialSource, load_credentials
from src.core.event_constants import EXCHANGE_BINANCE_FUTURES, SOURCE_ORDERBOOK


class BinanceFuturesRestError(Exception):
    """Raised when a Binance Futures REST API request fails."""


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
        raise ValueError(f"invalid ticker '{ticker}'. expected format like 'btc-usdt'")
    base_coin, quote = parts
    return f"{base_coin}{quote}"


class BinanceFuturesRest:
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        *,
        api_url: str = "https://fapi.binance.com",
        timeout_seconds: float = 10.0,
        recv_window_ms: int = 5000,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        if not secret_key:
            raise ValueError("secret_key is required")
        if recv_window_ms <= 0:
            raise ValueError("recv_window_ms must be greater than 0")

        self.api_key = api_key
        self.secret_key = secret_key
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.recv_window_ms = recv_window_ms
        self._session = requests.Session()
        self._server_time_offset_ms = 0

    @classmethod
    def from_info_yaml(cls, file_path: str = "info.yaml") -> "BinanceFuturesRest":
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"{file_path} not found")

        config = _parse_simple_yaml_mapping(path.read_text(encoding="utf-8"))
        api_key = config.get("binance_futures_api_key") or config.get("binance_api_key")
        secret_key = config.get("binance_futures_secret_key") or config.get(
            "binance_secret_key"
        )

        if not api_key or not secret_key:
            raise ValueError(
                "info.yaml must include 'binance_futures_api_key' and "
                "'binance_futures_secret_key' (or binance_api_key/binance_secret_key)"
            )

        return cls(api_key=str(api_key), secret_key=str(secret_key))

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
    ) -> "BinanceFuturesRest":
        try:
            credentials = load_credentials(
                "binance_futures",
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
        except CredentialError:
            if source != "auto":
                raise
            credentials = load_credentials(
                "binance",
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
        filtered: dict[str, Any] = {}
        for key, value in payload.items():
            if value is None:
                continue
            filtered[key] = value
        return urlencode(filtered, doseq=True)

    def _signed_params(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if params:
            payload.update(params)

        payload["timestamp"] = self._current_timestamp_ms()
        payload["recvWindow"] = self.recv_window_ms

        query_string = self._build_query_string(payload)
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        payload["signature"] = signature
        return payload

    def _current_timestamp_ms(self) -> int:
        return int(time.time() * 1000) + int(self._server_time_offset_ms)

    def _sync_server_time_offset(self) -> None:
        data = self._request_public("GET", "/fapi/v1/time")
        if not isinstance(data, dict):
            raise BinanceFuturesRestError(f"invalid server time response: {data}")
        server_time = data.get("serverTime")
        if server_time is None:
            raise BinanceFuturesRestError(f"serverTime missing in response: {data}")
        try:
            server_ms = int(str(server_time))
        except (TypeError, ValueError):
            raise BinanceFuturesRestError(f"serverTime missing in response: {data}")
        local_ms = int(time.time() * 1000)
        self._server_time_offset_ms = server_ms - local_ms

    def _request_public(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.api_url}{path}"
        response = self._session.request(
            method=method,
            url=url,
            params=params,
            timeout=self.timeout_seconds,
        )

        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}

        if not response.ok:
            raise BinanceFuturesRestError(
                f"{method} {path} failed (status={response.status_code}): {payload}"
            )

        return payload

    def _request_api_key(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.api_url}{path}"
        response = self._session.request(
            method=method,
            url=url,
            headers={"X-MBX-APIKEY": self.api_key},
            params=params,
            timeout=self.timeout_seconds,
        )

        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}

        if not response.ok:
            raise BinanceFuturesRestError(
                f"{method} {path} failed (status={response.status_code}): {payload}"
            )

        return payload

    def _request_signed(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        ignore_error_codes: set[int] | None = None,
    ) -> Any:
        url = f"{self.api_url}{path}"

        for attempt in range(2):
            signed = self._signed_params(params)
            response = self._session.request(
                method=method,
                url=url,
                headers={"X-MBX-APIKEY": self.api_key},
                params=signed,
                timeout=self.timeout_seconds,
            )

            try:
                payload = response.json()
            except ValueError:
                payload = {"raw": response.text}

            if response.ok:
                return payload

            error_code: int | None = None
            if isinstance(payload, dict):
                raw_code = payload.get("code")
                try:
                    error_code = int(raw_code) if raw_code is not None else None
                except (TypeError, ValueError):
                    error_code = None

            if (
                ignore_error_codes is not None
                and error_code is not None
                and error_code in ignore_error_codes
            ):
                return payload

            if error_code == -1021 and attempt == 0:
                self._sync_server_time_offset()
                continue

            raise BinanceFuturesRestError(
                f"{method} {path} failed (status={response.status_code}): {payload}"
            )

        raise BinanceFuturesRestError(f"{method} {path} failed: unknown retry state")

    def get_orderbook(self, ticker: str, *, limit: int = 20) -> dict[str, Any]:
        if limit <= 0:
            raise ValueError("limit must be greater than 0")

        symbol = _to_symbol(ticker)
        data = self._request_public("GET", "/fapi/v1/depth", params={"symbol": symbol, "limit": limit})
        return data if isinstance(data, dict) else {"data": data}

    def get_exchange_info(self) -> dict[str, Any]:
        data = self._request_public("GET", "/fapi/v1/exchangeInfo")
        return data if isinstance(data, dict) else {"data": data}

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
        raw_book = self.get_orderbook(ticker, limit=max(count, 5))

        asks: list[dict[str, float]] = []
        bids: list[dict[str, float]] = []

        raw_asks = raw_book.get("asks") if isinstance(raw_book, dict) else None
        raw_bids = raw_book.get("bids") if isinstance(raw_book, dict) else None

        if isinstance(raw_asks, list):
            for level in raw_asks:
                if not isinstance(level, list) or len(level) < 2:
                    continue
                price = self._to_float(level[0])
                qty = self._to_float(level[1])
                if price is None or qty is None:
                    continue
                asks.append({"price": price, "qty": qty})
                if len(asks) >= count:
                    break

        if isinstance(raw_bids, list):
            for level in raw_bids:
                if not isinstance(level, list) or len(level) < 2:
                    continue
                price = self._to_float(level[0])
                qty = self._to_float(level[1])
                if price is None or qty is None:
                    continue
                bids.append({"price": price, "qty": qty})
                if len(bids) >= count:
                    break

        return {
            "source": SOURCE_ORDERBOOK,
            "exchange": EXCHANGE_BINANCE_FUTURES,
            "ticker": key,
            "asks": asks,
            "bids": bids,
            "updated_at": time.time(),
        }

    def get_order(self, order_id: int | str, ticker: str) -> dict[str, Any]:
        if str(order_id).strip() == "":
            raise ValueError("order_id is required")

        symbol = _to_symbol(ticker)
        data = self._request_signed(
            "GET",
            "/fapi/v1/order",
            params={"symbol": symbol, "orderId": int(order_id)},
        )
        return data if isinstance(data, dict) else {"data": data}

    def get_open_orders(self, ticker: str) -> list[dict[str, Any]]:
        symbol = _to_symbol(ticker)
        data = self._request_signed(
            "GET",
            "/fapi/v1/openOrders",
            params={"symbol": symbol},
        )
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return [item for item in data["data"] if isinstance(item, dict)]
        return []

    def place_order(
        self,
        *,
        ticker: str,
        side: str,
        order_type: str,
        quantity: str | float | int,
        price: str | float | int | None = None,
        time_in_force: str = "GTC",
        reduce_only: bool | None = None,
        position_side: str | None = None,
        close_position: bool | None = None,
        working_type: str | None = None,
    ) -> dict[str, Any]:
        side_upper = side.strip().upper()
        type_upper = order_type.strip().upper()
        tif_upper = time_in_force.strip().upper()

        if side_upper not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if type_upper not in {
            "LIMIT",
            "MARKET",
            "STOP",
            "STOP_MARKET",
            "TAKE_PROFIT",
            "TAKE_PROFIT_MARKET",
            "TRAILING_STOP_MARKET",
        }:
            raise ValueError("unsupported order_type for futures")

        symbol = _to_symbol(ticker)
        body: dict[str, Any] = {
            "symbol": symbol,
            "side": side_upper,
            "type": type_upper,
            "quantity": str(quantity),
        }

        if price is not None:
            body["price"] = str(price)
        if type_upper in {"LIMIT", "STOP", "TAKE_PROFIT"}:
            body["timeInForce"] = tif_upper
        if reduce_only is not None:
            body["reduceOnly"] = "true" if reduce_only else "false"
        if position_side is not None:
            body["positionSide"] = position_side.strip().upper()
        if close_position is not None:
            body["closePosition"] = "true" if close_position else "false"
        if working_type is not None:
            body["workingType"] = working_type.strip().upper()

        if type_upper == "LIMIT" and price is None:
            raise ValueError("limit order requires price")

        data = self._request_signed("POST", "/fapi/v1/order", params=body)
        return data if isinstance(data, dict) else {"data": data}

    def cancel_order(self, order_id: int | str, ticker: str) -> dict[str, Any]:
        if str(order_id).strip() == "":
            raise ValueError("order_id is required")

        symbol = _to_symbol(ticker)
        data = self._request_signed(
            "DELETE",
            "/fapi/v1/order",
            params={"symbol": symbol, "orderId": int(order_id)},
        )
        return data if isinstance(data, dict) else {"data": data}

    def get_balances(self) -> list[dict[str, Any]]:
        data = self._request_signed("GET", "/fapi/v3/balance")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        return []

    def get_balances_v2(self) -> list[dict[str, Any]]:
        data = self._request_signed("GET", "/fapi/v2/balance")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        return []

    def get_account(self) -> dict[str, Any]:
        data = self._request_signed("GET", "/fapi/v2/account")
        return data if isinstance(data, dict) else {"data": data}

    def get_position_risk(self, ticker: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": _to_symbol(ticker)} if ticker else None
        data = self._request_signed(
            "GET",
            "/fapi/v2/positionRisk",
            params=params,
        )
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return [item for item in data["data"] if isinstance(item, dict)]
        return []

    def get_premium_index(self, ticker: str | None = None) -> list[dict[str, Any]]:
        params = None
        if ticker:
            params = {"symbol": _to_symbol(ticker)}
        data = self._request_public("GET", "/fapi/v1/premiumIndex", params=params)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [data]
        return []

    def get_funding_info(self) -> list[dict[str, Any]]:
        data = self._request_public("GET", "/fapi/v1/fundingInfo")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return [item for item in data["data"] if isinstance(item, dict)]
        return []

    def create_listen_key(self) -> str:
        data = self._request_api_key("POST", "/fapi/v1/listenKey")
        if not isinstance(data, dict):
            raise BinanceFuturesRestError(f"invalid listenKey response: {data}")
        listen_key = data.get("listenKey")
        if not isinstance(listen_key, str) or not listen_key:
            raise BinanceFuturesRestError(f"listenKey missing in response: {data}")
        return listen_key

    def keepalive_listen_key(self, listen_key: str) -> dict[str, Any]:
        if not listen_key:
            raise ValueError("listen_key is required")
        data = self._request_api_key(
            "PUT",
            "/fapi/v1/listenKey",
            params={"listenKey": listen_key},
        )
        return data if isinstance(data, dict) else {"data": data}

    def close_listen_key(self, listen_key: str) -> dict[str, Any]:
        if not listen_key:
            raise ValueError("listen_key is required")
        data = self._request_api_key(
            "DELETE",
            "/fapi/v1/listenKey",
            params={"listenKey": listen_key},
        )
        return data if isinstance(data, dict) else {"data": data}

    def change_leverage(self, ticker: str, leverage: int) -> dict[str, Any]:
        if leverage < 1 or leverage > 125:
            raise ValueError("leverage must be in range 1..125")

        symbol = _to_symbol(ticker)
        data = self._request_signed(
            "POST",
            "/fapi/v1/leverage",
            params={"symbol": symbol, "leverage": int(leverage)},
        )
        return data if isinstance(data, dict) else {"data": data}

    def change_margin_type(self, ticker: str, margin_type: str) -> dict[str, Any]:
        normalized = margin_type.strip().upper()
        if normalized in {"CROSS", "CROSSED"}:
            normalized = "CROSSED"
        elif normalized in {"ISOLATED", "ISOLATE"}:
            normalized = "ISOLATED"
        else:
            raise ValueError("margin_type must be CROSS/CROSSED or ISOLATED")

        symbol = _to_symbol(ticker)
        data = self._request_signed(
            "POST",
            "/fapi/v1/marginType",
            params={"symbol": symbol, "marginType": normalized},
            ignore_error_codes={-4046},
        )
        return data if isinstance(data, dict) else {"data": data}


def tradeTest(client: BinanceFuturesRest, test_ticker: str = "xrp-usdt") -> None:
    order_id: int | None = None

    try:
        print("[TEST] 1) Get balances")
        try:
            balances = client.get_balances()
            print(balances)
        except Exception as balance_err:
            print(f"[TEST] get balances failed: {balance_err}")

        print("[TEST] 2) Get orderbook and place BUY LIMIT")
        orderbook = client.get_orderbook(test_ticker, limit=20)
        bids = orderbook.get("bids") if isinstance(orderbook, dict) else None
        if not isinstance(bids, list) or not bids:
            raise BinanceFuturesRestError(f"bids is empty: {orderbook}")

        target_bid = bids[4] if len(bids) >= 5 else bids[-1]
        if not isinstance(target_bid, list) or len(target_bid) < 1:
            raise BinanceFuturesRestError(f"target bid is invalid: {target_bid}")

        best_bid_price = target_bid[0]
        print(f"[TEST] target bid(5th) price={best_bid_price}")
        placed = client.place_order(
            ticker=test_ticker,
            side="BUY",
            order_type="LIMIT",
            price=best_bid_price,
            quantity="100",
            time_in_force="GTC",
        )
        print("[TEST] place_order response:", placed)

        if isinstance(placed, dict):
            raw_id = placed.get("orderId")
            if raw_id is not None:
                order_id = int(raw_id)
        if order_id is None:
            raise BinanceFuturesRestError(f"orderId missing in place response: {placed}")

        print("[TEST] 3) Get order")
        order = client.get_order(order_id, test_ticker)
        print(order)

    finally:
        if order_id is not None:
            try:
                print("[TEST] 4) Cancel order")
                cancelled = client.cancel_order(order_id, test_ticker)
                print(cancelled)
            except Exception as cancel_err:
                print(f"[TEST] cancel failed: {cancel_err}")


def orderbookTest(client: BinanceFuturesRest, test_ticker: str = "xrp-usdt") -> None:
    print("[TEST] 1) Get orderbook (raw)")
    orderbook = client.get_orderbook(test_ticker, limit=5)
    print(orderbook)

    print("[TEST] 2) Get orderbook (parsed)")
    parsed = client.get_orderbook_parse(test_ticker, count=5)
    print(parsed)


def settingsTest(
    client: BinanceFuturesRest,
    test_ticker: str = "xrp-usdt",
    leverage: int = 3,
    margin_type: str = "isolated",
) -> None:
    print(f"[TEST] 1) Change leverage -> {leverage}")
    leverage_result = client.change_leverage(test_ticker, leverage)
    print(leverage_result)

    print(f"[TEST] 2) Change margin type -> {margin_type}")
    margin_result = client.change_margin_type(test_ticker, margin_type)
    print(margin_result)


if __name__ == "__main__":
    client = BinanceFuturesRest.from_info_yaml("info.yaml")
    test_ticker = "xrp-usdt"

    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "orderbook"
    if mode in {"trade", "trade_test", "tradetest"}:
        tradeTest(client, test_ticker)
    elif mode in {"settings", "setting_test", "settingstest"}:
        settingsTest(client, test_ticker)
    else:
        orderbookTest(client, test_ticker)
