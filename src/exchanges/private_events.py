"""Private spot stream protocol fields, with exact decimal strings for ledgers.

Missing fill identifiers or amounts remain missing. Callers must reconcile an
incomplete fill with REST before applying a cycle or accounting entry.
"""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any


def _exact(value: Any) -> str | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return format(number, "f") if number.is_finite() and number >= 0 else None


def _copy_known(row: dict, mappings: dict[str, tuple[str, ...]]) -> dict:
    result = {}
    for target, keys in mappings.items():
        value = next((row[key] for key in keys if row.get(key) is not None), None)
        if value is None:
            continue
        if target in {"clientOrderId", "fillId", "feeCurrency"}:
            if isinstance(value, (str, int)) and str(value):
                result[target] = str(value)
        else:
            exact = _exact(value)
            if exact is not None:
                result[target] = exact
    return result


def normalize_private_order_event(exchange: str, message: Any, market: str) -> dict | None:
    if not isinstance(message, dict):
        return None
    if exchange == "coinone":
        if message.get("response_type") != "DATA" or message.get("channel") != "MYORDER":
            return None
        row = message.get("data")
        if not isinstance(row, dict) or f"{row.get('quote_currency')}-{row.get('target_currency')}" != market:
            return None
        state = str(row.get("status", "")).lower()
        order_id, side, kind = row.get("order_id"), row.get("side"), row.get("type")
        price, volume, remaining = row.get("order_price"), row.get("order_qty"), row.get("remain_qty")
        fields = {"clientOrderId": ("user_order_id",), "originalPrice": ("order_price",),
                  "originalVolume": ("order_qty",), "remainingVolume": ("remain_qty",),
                  "fillId": ("trade_id",), "fillPrice": ("executed_price",),
                  "fillVolume": ("executed_qty",), "fee": ("executed_fee",),
                  "fillTimestamp": ("executed_timestamp",)}
    else:
        row = message
        if row.get("type") != "myOrder" or row.get("code") != market:
            return None
        state = str(row.get("state", "")).lower()
        order_id = row.get("order_id") if exchange == "bithumb" else row.get("uuid")
        side = row.get("side") if exchange == "bithumb" else row.get("ask_bid")
        kind = row.get("order_type")
        if exchange == "bithumb":
            price, volume, remaining = row.get("order_price"), row.get("order_quantity"), row.get("remaining_quantity")
            fields = {"clientOrderId": ("client_order_id",), "originalPrice": ("order_price",),
                      "originalVolume": ("order_quantity",), "remainingVolume": ("remaining_quantity",),
                      "cumulativeFilled": ("executed_quantity", "executed_volume"),
                      "fillId": ("trade_id",), "fillPrice": ("trade_price",),
                      "fillVolume": ("trade_quantity",), "fee": ("paid_fee",),
                      "feeCurrency": ("fee_currency",), "fillTimestamp": ("trade_timestamp",)}
        elif exchange == "upbit":
            price = row.get("price") if state == "wait" else None
            volume = row.get("volume") if state == "wait" else None
            remaining = row.get("remaining_volume")
            fields = {"clientOrderId": ("identifier",), "originalPrice": ("order_price",),
                      "originalVolume": ("order_volume",), "remainingVolume": ("remaining_volume",),
                      "cumulativeFilled": ("executed_volume",), "fillId": ("trade_uuid",),
                      "fillPrice": ("price",), "fillVolume": ("volume",),
                      "fee": ("trade_fee",), "feeCurrency": ("fee_currency",),
                      "fillTimestamp": ("trade_timestamp",)}
        else:
            return None
    if not isinstance(order_id, str) or not order_id or not state:
        return None
    patch: dict[str, Any] = {"id": order_id, "market": market}
    side = str(side).lower()
    if side in {"bid", "buy", "ask", "sell"}:
        patch["side"] = "buy" if side in {"bid", "buy"} else "sell"
    if kind:
        patch["orderType"] = str(kind).lower()
    if remaining is None and state == "wait":
        remaining = volume
    for key, value in (("price", price), ("volume", volume), ("remainingVolume", remaining)):
        exact = _exact(value)
        if exact is not None:
            displayed = float(exact)
            if math.isfinite(displayed):
                patch[key] = displayed
    stamp = row.get("timestamp") or row.get("executed_timestamp") or row.get("order_timestamp") or 0
    try:
        stamp = float(stamp)
        stamp = stamp * 1000 if 0 < stamp < 1e12 else stamp
        stamp = stamp if math.isfinite(stamp) else 0
    except (TypeError, ValueError):
        stamp = 0
    terminal = state in {"done", "trade_done", "cancel", "cancel_post_only"}
    terminal = terminal or patch.get("remainingVolume") == 0
    exact_order = _copy_known(row, fields)
    if exchange == "coinone" and state not in {"trade", "trade_done"}:
        for key in ("fillId", "fillPrice", "fillVolume", "fee", "fillTimestamp"):
            exact_order.pop(key, None)
    if exchange == "upbit" and state != "trade":
        for key in ("fillId", "fillPrice", "fillVolume", "fee", "fillTimestamp"):
            exact_order.pop(key, None)
    exact_order["exchangeOrderId"] = order_id
    if exchange == "upbit" and state == "wait":
        exact_order.setdefault("originalPrice", _exact(row.get("price")))
        exact_order.setdefault("originalVolume", _exact(row.get("volume")))
    exact_order = {key: value for key, value in exact_order.items() if value is not None}
    return {"event": "order", "id": order_id, "market": market, "state": state,
            "timestamp": stamp, "terminal": terminal, "order": patch, "exact": exact_order}


def normalize_private_asset_event(exchange: str, message: Any) -> dict | None:
    if not isinstance(message, dict):
        return None
    if exchange == "coinone":
        if message.get("response_type") != "DATA" or message.get("channel") != "MYASSET":
            return None
        data = message.get("data")
        data = data.get("assets") if isinstance(data, dict) else None
    elif exchange in {"upbit", "bithumb"}:
        if message.get("type") != "myAsset":
            return None
        data = message.get("assets")
    else:
        return None
    rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    assets = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        currency = row.get("currency") or row.get("target_currency")
        if not isinstance(currency, str) or not currency:
            continue
        values = _copy_known(row, {"available": ("available", "balance"),
                                   "locked": ("locked", "limit")})
        assets.append({"currency": currency.upper(), **values})
    return {"event": "account", "assets": assets}  # Empty means reconcile via REST.
