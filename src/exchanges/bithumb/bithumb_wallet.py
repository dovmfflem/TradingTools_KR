"""Explicit Bithumb wallet compatibility surface. No retries or route inference."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING, localcontext
import math
import re
import time
from types import MappingProxyType
from typing import Any

from .bithumb_rest import BithumbRest, BithumbRestError, _wallet_object, _wallet_rows

__all__ = ["BithumbSpotWallet", "withdrawal_fee", "withdrawal_fee_upper_bound", "withdrawal_beneficiary"]

_DEPOSIT_STATES = {
    "DEPOSIT_ACCEPTED": "ACCEPTED",
    "DEPOSIT_PROCESSING": "PROCESSING",
    "DEPOSIT_CANCELLED": "CANCELLED",
    "REFUNDED_ACCEPTED": "REFUNDED",
}


def _transfer(row: dict[str, Any], *, deposit: bool = False) -> dict[str, Any]:
    result = dict(_wallet_object(row))
    if isinstance(row.get("state"), str):
        state = row["state"].upper()
        result["raw_state"] = row["state"]
        result["state"] = _DEPOSIT_STATES.get(state, state) if deposit else state
    return result


def _uuid(uuid_value: str | None, uuid: str | None) -> str | None:
    if uuid_value is not None and uuid is not None and uuid_value != uuid:
        raise ValueError("uuid and uuid_value disagree")
    return uuid_value if uuid_value is not None else uuid


def withdrawal_beneficiary(registered_address: dict[str, Any], *,
                           travel_rule_type: str | None = None) -> dict[str, str]:
    """Map a freshly selected registry row, never infer the travel-rule route.

    Without a route, return available owner metadata (possibly incomplete).
    The caller must obtain/confirm missing CODE details before submission.
    Route selection is local validation only, never an API request parameter.
    """
    if not isinstance(registered_address, dict):
        raise ValueError("registered_address must be a registry row")
    if travel_rule_type not in {None, "internal", "id_connect", "whitelist", "code"}:
        raise ValueError("travel_rule_type must be internal, id_connect, whitelist or code")
    if travel_rule_type == "internal":
        return {}
    exchange = registered_address.get("exchange_name")
    if not isinstance(exchange, str) or not exchange.strip():
        raise ValueError("registered exchange_name is required")
    result = {"exchange_name": exchange}
    owner_type = registered_address.get("owner_type")
    if travel_rule_type in {"id_connect", "whitelist"}:
        if travel_rule_type == "id_connect" and owner_type == "corporation":
            raise ValueError("CODE ID Connect supports personal accounts only")
        return result
    for source, target in (
        ("owner_type", "receiver_type"), ("owner_ko_name", "receiver_ko_name"),
        ("owner_en_name", "receiver_en_name"), ("owner_corp_ko_name", "receiver_corp_ko_name"),
        ("owner_corp_en_name", "receiver_corp_en_name"),
    ):
        value = registered_address.get(source)
        if isinstance(value, str) and value.strip():
            result[target] = value
    if owner_type is not None and owner_type not in {"personal", "corporation"}:
        raise ValueError("unrecognized registered owner_type")
    if travel_rule_type == "code":
        required = ["receiver_type", "receiver_ko_name", "receiver_en_name"]
        if owner_type == "corporation":
            required += ["receiver_corp_ko_name", "receiver_corp_en_name"]
        missing = [field for field in required if not result.get(field)]
        if missing:
            raise ValueError("verified CODE beneficiary fields required: " + ", ".join(missing))
    return result


def withdrawal_fee(chance: dict[str, Any], amount: str | int | float | Decimal) -> Decimal:
    """Unrounded fee from official fixed OR proportional fee fields.

    An absent/invalid fee is unknown, not zero. Exchange precision/rounding and
    total debit policy remain the caller's responsibility.
    """
    def number(value: Any) -> Decimal:
        try:
            value = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError("fee inputs must be finite nonnegative decimals") from None
        if not value.is_finite() or value < 0:
            raise ValueError("fee inputs must be finite nonnegative decimals")
        return value

    quantity = number(amount)
    currency = chance.get("currency")
    if not isinstance(currency, dict):
        raise ValueError("currency fee metadata is missing")
    fixed, rate = currency.get("withdraw_fee"), currency.get("withdraw_rate")
    if fixed is not None and rate is not None:
        raise ValueError("ambiguous fixed and proportional fee metadata")
    if fixed is not None:
        return number(fixed)
    if rate is None:
        raise ValueError("withdrawal fee is unknown")
    rate = number(rate)
    with localcontext() as context:
        context.prec = max(28, len(quantity.as_tuple().digits) + len(rate.as_tuple().digits))
        fee = quantity * rate
    minimum, maximum = currency.get("withdraw_fee_min"), currency.get("withdraw_fee_max")
    if minimum is not None and maximum is not None and number(minimum) > number(maximum):
        raise ValueError("invalid fee bounds")
    if minimum is not None:
        fee = max(fee, number(minimum))
    if maximum is not None:
        fee = min(fee, number(maximum))
    return fee


def withdrawal_fee_upper_bound(chance: dict[str, Any], amount: str | int | float | Decimal) -> Decimal:
    """Budget estimate: ceil the calculated fee to withdrawal amount precision.

    This is a library budgeting convention, NOT documented exchange fee rounding
    or a guaranteed bound on the eventual charged fee. Requote before submission.
    """
    limits = chance.get("withdraw_limit")
    fixed = limits.get("fixed") if isinstance(limits, dict) else None
    if type(fixed) is not int or not 0 <= fixed <= 18:
        raise ValueError("withdraw_limit.fixed must be an integer from 0 to 18")
    fee = withdrawal_fee(chance, amount)
    with localcontext() as context:
        context.prec = max(28, len(fee.as_tuple().digits), fee.adjusted() + fixed + 2)
        return fee.quantize(Decimal((0, (1,), -fixed)), rounding=ROUND_CEILING)


class BithumbSpotWallet:
    """Wrap an existing BithumbRest; never constructs clients or reads secrets.

    Native history aliases return the same normalized states as their legacy
    counterparts. raw_state preserves Bithumb's original state verbatim.
    """

    exchange = "bithumb"
    capabilities = MappingProxyType({
        "wallets": True, "registered_addresses": True, "withdraw_chance": True,
        "withdrawals": True, "deposits": True, "deposit_address_creation": True,
        "beneficiary_fields": True, "withdrawal_idempotency": False,
        "address_registration": False, "travel_rule_consent": False,
        "history_time_filter": False, "history_network_filter": False,
        "krw_ticker_price": True,
    })

    def __init__(self, client: BithumbRest):
        self.client = client

    @property
    def api_url(self) -> str:
        return self.client.api_url

    @property
    def timeout_seconds(self) -> float:
        return self.client.timeout_seconds

    @timeout_seconds.setter
    def timeout_seconds(self, value: float) -> None:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        self.client.timeout_seconds = value

    @property
    def last_response_metadata(self) -> Any:
        return self.client.last_response_metadata

    def close(self) -> None:
        self.client.close()

    def get_accounts(self) -> list[dict[str, Any]]:
        return _wallet_rows(self.client.get_accounts())

    def get_krw_price(self, currency: str, *, max_age_seconds: float = 5.0) -> Decimal:
        """Fresh public KRW ticker price, not the exchange's limit valuation.

        No indirect/cross-market fallback. Freshness uses ticker snapshot time,
        not the last trade time. KRW itself is exactly one without a request.
        """
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z0-9]{1,20}", currency):
            raise ValueError("currency must be an asset code, not a market pair")
        if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be finite and positive")
        currency = currency.upper()
        if currency == "KRW":
            return Decimal("1")
        rows = _wallet_rows(self.client.get_ticker(f"{currency.lower()}-krw"))
        if len(rows) != 1 or rows[0].get("market") != f"KRW-{currency}":
            raise BithumbRestError("bithumb", "KRW_PRICE_UNAVAILABLE")
        row = rows[0]
        try:
            price = Decimal(str(row.get("trade_price")))
            stamp = Decimal(str(row.get("timestamp")))
        except InvalidOperation:
            raise BithumbRestError("bithumb", "INVALID_RESPONSE") from None
        if not price.is_finite() or price <= 0 or not stamp.is_finite() or stamp <= 0:
            raise BithumbRestError("bithumb", "INVALID_RESPONSE")
        if abs(Decimal(str(time.time())) * 1000 - stamp) > Decimal(str(max_age_seconds)) * 1000:
            raise BithumbRestError("bithumb", "STALE_KRW_PRICE")
        return price

    def list_wallet_statuses(self) -> list[dict[str, Any]]:
        return _wallet_rows(self.client.list_wallet_statuses())

    get_wallet_status = list_wallet_statuses

    def list_withdraw_addresses(self, *, currency: str | None = None,
                                net_type: str | None = None) -> list[dict[str, Any]]:
        return self.client.list_withdraw_addresses(currency=currency, net_type=net_type)

    def get_withdraw_chance(self, *, currency: str, net_type: str) -> dict[str, Any]:
        return self.client.get_withdraw_chance(currency=currency, net_type=net_type)

    def list_withdraws(self, *, currency: str | None = None, state: str | None = None,
                      uuids: list[str] | tuple[str, ...] | None = None,
                      txids: list[str] | tuple[str, ...] | None = None,
                      page: int | None = None, limit: int | None = None,
                      order_by: str | None = None) -> list[dict[str, Any]]:
        return [_transfer(row) for row in self.client.list_coin_withdraws(
            currency=currency, state=state, uuids=uuids, txids=txids,
            page=page, limit=limit, order_by=order_by)]

    list_coin_withdraws = list_withdraws

    def list_krw_withdraws(self, *, state: str | None = None, page: int | None = None,
                          limit: int | None = None, order_by: str | None = None) -> list[dict[str, Any]]:
        return [_transfer(row) for row in self.client.list_krw_withdraws(
            state=state, page=page, limit=limit, order_by=order_by)]

    def get_withdraw(self, uuid_value: str | None = None, *, currency: str,
                     txid: str | None = None, uuid: str | None = None) -> dict[str, Any]:
        return _transfer(self.client.get_withdraw(
            uuid_value=_uuid(uuid_value, uuid), currency=currency, txid=txid))

    def create_coin_withdraw(self, *, currency: str, net_type: str, amount: str | float | int,
                             address: str, secondary_address: str | None = None,
                             transaction_type: str | None = None,
                             exchange_name: str | None = None, receiver_type: str | None = None,
                             receiver_ko_name: str | None = None, receiver_en_name: str | None = None,
                             receiver_corp_ko_name: str | None = None,
                             receiver_corp_en_name: str | None = None) -> dict[str, Any]:
        return _transfer(self.client.create_coin_withdraw(
            currency=currency, net_type=net_type, amount=amount, address=address,
            secondary_address=secondary_address, transaction_type=transaction_type,
            exchange_name=exchange_name, receiver_type=receiver_type,
            receiver_ko_name=receiver_ko_name, receiver_en_name=receiver_en_name,
            receiver_corp_ko_name=receiver_corp_ko_name, receiver_corp_en_name=receiver_corp_en_name))

    def cancel_coin_withdraw(self, *, uuid_value: str) -> dict[str, Any]:
        return _transfer(self.client.cancel_coin_withdraw(uuid_value=uuid_value))

    def get_deposit_address(self, *, currency: str, net_type: str) -> dict[str, Any]:
        return self.client.get_deposit_address(currency=currency, net_type=net_type)

    def create_deposit_address(self, *, currency: str, net_type: str) -> dict[str, Any]:
        return self.client.create_deposit_address(currency=currency, net_type=net_type)

    def list_deposit_addresses(self, *, currency: str | None = None,
                               net_type: str | None = None) -> list[dict[str, Any]]:
        return self.client.list_deposit_addresses(currency=currency, net_type=net_type)

    def get_deposit(self, uuid_value: str | None = None, *, currency: str,
                    txid: str | None = None, uuid: str | None = None) -> dict[str, Any]:
        return _transfer(self.client.get_deposit(
            uuid_value=_uuid(uuid_value, uuid), currency=currency, txid=txid), deposit=True)

    def list_deposits(self, *, currency: str | None = None, state: str | None = None,
                      uuids: list[str] | tuple[str, ...] | None = None,
                      txids: list[str] | tuple[str, ...] | None = None,
                      page: int | None = None, limit: int | None = None,
                      order_by: str | None = None) -> list[dict[str, Any]]:
        native_state = {value: key for key, value in _DEPOSIT_STATES.items()}.get(state, state)
        return [_transfer(row, deposit=True) for row in self.client.list_coin_deposits(
            currency=currency, state=native_state, uuids=uuids, txids=txids,
            page=page, limit=limit, order_by=order_by)]

    list_coin_deposits = list_deposits

    def list_krw_deposits(self, *, state: str | None = None, page: int | None = None,
                         limit: int | None = None, order_by: str | None = None) -> list[dict[str, Any]]:
        return [_transfer(row, deposit=True) for row in self.client.list_krw_deposits(
            state=state, page=page, limit=limit, order_by=order_by)]
