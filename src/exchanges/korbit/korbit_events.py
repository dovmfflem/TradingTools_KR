"""Korbit v2 private stream wire events for display and exact reconciliation."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


def _number(value, default="0") -> Decimal:
    try:
        result = Decimal(str(value if value is not None else default))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("KORBIT_INVALID_NUMBER") from None
    if not result.is_finite():
        raise ValueError("KORBIT_INVALID_NUMBER")
    return result


def normalized_order(row, ticker):
    qty = _number(row.get("qty"))
    return {"id": str(row["orderId"]), "market": f"KRW-{ticker}", "side": row["side"],
            "orderType": row["orderType"], "price": float(_number(row.get("price"))),
            "volume": float(qty), "remainingVolume": float(max(Decimal(0), qty - _number(row.get("filledQty")))),
            "createdAt": str(row.get("createdAt", ""))}


def normalize_events(message, market, *, account_seq=1):
    if not isinstance(message, dict):
        return []
    if message.get("channelType") == "myAsset":
        asset = message.get("asset", {})
        if not isinstance(asset, dict) or asset.get("accountSeq", account_seq) != account_seq:
            return []
        event = {"event": "account"}
        rows = asset.get("assets")
        if isinstance(rows, list):
            balances = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("currency"), str):
                    continue
                balance = {"currency": row["currency"].upper()}
                for target, source in (("total", "balance"), ("available", "available"),
                                       ("tradeInUse", "tradeInUse"),
                                       ("withdrawalInUse", "withdrawalInUse"),
                                       ("avgPrice", "avgPrice")):
                    if row.get(source) is not None:
                        balance[target] = format(_number(row[source]), "f")
                if row.get("tradeInUse") is not None and row.get("withdrawalInUse") is not None:
                    balance["locked"] = format(_number(row["tradeInUse"]) +
                                               _number(row["withdrawalInUse"]), "f")
                if row.get("updatedAt") is not None:
                    balance["updatedAt"] = row["updatedAt"]
                balances.append(balance)
            event["assets"] = balances
        return [event]
    quote, ticker = market.split("-", 1)
    channel = message.get("channelType")
    if channel not in {"myOrder", "myTrade"} or message.get("symbol") != f"{ticker.lower()}_{quote.lower()}":
        return []
    if channel == "myTrade":
        group = message.get("trade") or {}
        if not isinstance(group, dict) or group.get("accountSeq", account_seq) != account_seq:
            return []
        result = []
        for row in group.get("trades", []):
            if not isinstance(row, dict) or row.get("tradeId") is None or row.get("orderId") is None:
                continue
            exact = {"fillId": str(row["tradeId"]), "exchangeOrderId": str(row["orderId"])}
            for target, source in (("fillPrice", "price"), ("fillVolume", "qty"), ("fee", "fee")):
                if row.get(source) is not None:
                    exact[target] = format(_number(row[source]), "f")
            if row.get("feeCurrency") is not None:
                exact["feeCurrency"] = str(row["feeCurrency"]).upper()
            if row.get("filledAt") is not None:
                exact["fillTimestamp"] = row["filledAt"]
            result.append({"event": "fill", "id": str(row["tradeId"]), "orderId": str(row["orderId"]),
                           "market": market, "timestamp": row.get("filledAt", message.get("timestamp", 0)),
                           "side": row.get("side"), "exact": exact})
        return result
    group = message.get("order") or {}
    if not isinstance(group, dict) or group.get("accountSeq", account_seq) != account_seq:
        return []
    result = []
    for row in group.get("orders", []):
        if not isinstance(row, dict):
            continue
        state = row.get("status")
        if state not in {"pending", "unfilled", "open", "partiallyFilled", "filled", "canceled",
                         "partiallyFilledCanceled", "expired"}:
            continue
        order_id = str(row["orderId"])
        exact = {"exchangeOrderId": order_id}
        for target, source in (("clientOrderId", "clientOrderId"), ("originalPrice", "price"),
                               ("originalVolume", "qty"), ("cumulativeFilled", "filledQty"),
                               ("fee", "fee")):
            value = row.get(source)
            if value is None:
                continue
            exact[target] = str(value) if target == "clientOrderId" else format(_number(value), "f")
        result.append({"event": "order", "id": order_id, "market": market, "state": state,
                       "timestamp": message.get("timestamp", 0),
                       "terminal": state in {"filled", "canceled", "partiallyFilledCanceled", "expired"},
                       "order": normalized_order(row, ticker), "exact": exact})
    return result
