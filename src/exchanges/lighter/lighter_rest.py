from __future__ import annotations

import asyncio
import importlib
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import requests


class LighterRestError(Exception):
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


def _normalize_perp_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper().replace("-", "").replace("/", "")
    if not normalized:
        raise ValueError("symbol is required")
    if normalized.endswith("USDT") and len(normalized) > 4:
        return normalized[:-4]
    return normalized


def _to_scaled_int(value: float | str, decimals: int, *, field_name: str) -> int:
    decimal_value = Decimal(str(value))
    if decimal_value <= 0:
        raise ValueError(f"{field_name} must be greater than 0")

    scale = Decimal(10) ** decimals
    scaled = (decimal_value * scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(scaled)


@dataclass(frozen=True)
class LighterMarketDetail:
    symbol: str
    market_id: int
    price_decimals: int
    size_decimals: int


class LighterRest:
    def __init__(
        self,
        account_index: int,
        api_key_index: int,
        api_private_key: str,
        *,
        base_url: str = "https://mainnet.zklighter.elliot.ai",
        timeout_seconds: float = 10.0,
    ) -> None:
        if account_index < 0:
            raise ValueError("account_index must be >= 0")
        if api_key_index < 0:
            raise ValueError("api_key_index must be >= 0")
        if not api_private_key:
            raise ValueError("api_private_key is required")

        try:
            lighter_module = importlib.import_module("lighter")
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "lighter-sdk is required. install: pip install lighter-sdk"
            ) from exc

        self.account_index = account_index
        self.api_key_index = api_key_index
        self.api_private_key = api_private_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._lighter_module: object = lighter_module

    @classmethod
    def from_info_yaml(cls, file_path: str = "info.yaml") -> "LighterRest":
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"{file_path} not found")

        config = _parse_simple_yaml_mapping(path.read_text(encoding="utf-8"))
        account_index = config.get("lighter_account_index")
        api_key_index = config.get("lighter_api_key_index")
        api_private_key = config.get("lighter_api_private_key")
        base_url = config.get("lighter_base_url")

        if account_index is None or api_key_index is None or not api_private_key:
            raise ValueError(
                "info.yaml must include 'lighter_account_index', 'lighter_api_key_index', and 'lighter_api_private_key'"
            )

        return cls(
            account_index=int(account_index),
            api_key_index=int(api_key_index),
            api_private_key=str(api_private_key),
            base_url=str(base_url) if base_url else "https://mainnet.zklighter.elliot.ai",
        )

    def _fetch_market_detail(self, *, symbol: str, market_id: int | None = None) -> LighterMarketDetail:
        response = requests.get(
            f"{self.base_url}/api/v1/orderBookDetails",
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("order_book_details") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise LighterRestError("orderBookDetails response does not include order_book_details")

        wanted_symbol = _normalize_perp_symbol(symbol)
        for item in rows:
            if not isinstance(item, dict):
                continue
            row_symbol = str(item.get("symbol") or "").upper()
            if row_symbol != wanted_symbol:
                continue
            if str(item.get("market_type") or "").lower() != "perp":
                continue
            if str(item.get("status") or "").lower() != "active":
                continue
            row_market_id = item.get("market_id")
            if not isinstance(row_market_id, int):
                continue
            if market_id is not None and row_market_id != market_id:
                continue

            price_decimals = item.get("price_decimals")
            size_decimals = item.get("size_decimals")
            if not isinstance(price_decimals, int) or not isinstance(size_decimals, int):
                raise LighterRestError("market decimals are missing from orderBookDetails")

            return LighterMarketDetail(
                symbol=row_symbol,
                market_id=row_market_id,
                price_decimals=price_decimals,
                size_decimals=size_decimals,
            )

        raise LighterRestError(
            f"active perp market not found for symbol={wanted_symbol}"
            + ("" if market_id is None else f", market_id={market_id}")
        )

    async def _create_market_order_async(
        self,
        *,
        symbol: str,
        size: float,
        max_avg_price: float,
        is_ask: bool,
        market_id: int | None = None,
        reduce_only: bool = False,
        client_order_index: int | None = None,
    ) -> dict[str, Any]:
        detail = self._fetch_market_detail(symbol=symbol, market_id=market_id)
        scaled_size = _to_scaled_int(size, detail.size_decimals, field_name="size")
        scaled_price = _to_scaled_int(max_avg_price, detail.price_decimals, field_name="max_avg_price")
        order_index = (
            int(time.time() * 1000) % 2_000_000_000
            if client_order_index is None
            else int(client_order_index)
        )

        signer_client_cls = getattr(self._lighter_module, "SignerClient", None)
        if signer_client_cls is None:
            raise LighterRestError("lighter-sdk does not expose SignerClient")

        client = signer_client_cls(
            url=self.base_url,
            api_private_keys={self.api_key_index: self.api_private_key},
            account_index=self.account_index,
        )
        try:
            check_error = client.check_client()
            if check_error is not None:
                raise LighterRestError(f"SignerClient check failed: {check_error}")

            tx, tx_hash, error = await client.create_order(
                market_index=detail.market_id,
                client_order_index=order_index,
                base_amount=scaled_size,
                price=scaled_price,
                is_ask=is_ask,
                order_type=client.ORDER_TYPE_MARKET,
                time_in_force=client.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
                reduce_only=reduce_only,
                order_expiry=client.DEFAULT_IOC_EXPIRY,
            )
            if error is not None:
                raise LighterRestError(f"create_order failed: {error}")

            return {
                "symbol": detail.symbol,
                "market_id": detail.market_id,
                "side": "sell" if is_ask else "buy",
                "size": size,
                "max_avg_price": max_avg_price,
                "client_order_index": order_index,
                "tx_hash": tx_hash,
                "tx": str(tx),
            }
        finally:
            await client.close()

    def place_market_buy(
        self,
        *,
        symbol: str = "XRPUSDT",
        size: float,
        max_avg_price: float,
        market_id: int | None = None,
        reduce_only: bool = False,
        client_order_index: int | None = None,
    ) -> dict[str, Any]:
        return asyncio.run(
            self._create_market_order_async(
                symbol=symbol,
                size=size,
                max_avg_price=max_avg_price,
                is_ask=False,
                market_id=market_id,
                reduce_only=reduce_only,
                client_order_index=client_order_index,
            )
        )

    def place_market_sell(
        self,
        *,
        symbol: str = "XRPUSDT",
        size: float,
        max_avg_price: float,
        market_id: int | None = None,
        reduce_only: bool = False,
        client_order_index: int | None = None,
    ) -> dict[str, Any]:
        return asyncio.run(
            self._create_market_order_async(
                symbol=symbol,
                size=size,
                max_avg_price=max_avg_price,
                is_ask=True,
                market_id=market_id,
                reduce_only=reduce_only,
                client_order_index=client_order_index,
            )
        )
