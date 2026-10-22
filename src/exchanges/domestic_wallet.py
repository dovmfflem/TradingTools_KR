"""Exact wallet contract for domestic transfers; no retries or account selection.

Coinone does not publish network identifiers in its address APIs. Its rows keep
network=None; callers must obtain an explicit operator network confirmation.
Reference URLs and mocked contract evidence: tests/test_domestic_wallet.py.
"""
import time
from datetime import datetime
from decimal import Decimal

from .spot_trading import decimal, exact
from .domestic_spot import precise
from .bithumb.bithumb_wallet import BithumbSpotWallet, withdrawal_fee_upper_bound, withdrawal_beneficiary


def rows(value, key=None):
    value = value.get(key) if key and isinstance(value, dict) else value
    if not isinstance(value, list) or any(not isinstance(r, dict) for r in value):
        raise ValueError("WALLET_RESPONSE_INVALID")
    return value


def address(currency, network, value, memo, raw):
    if not isinstance(value, str) or not value:
        raise ValueError("WALLET_ADDRESS_INVALID")
    return {"currency": str(currency or "").upper(), "network": network or None,
            "address": value, "memo": str(memo or ""), "raw": raw}


class DomesticWallet:
    def __init__(self, exchange, client):
        self.exchange = exchange
        self.client = BithumbSpotWallet(client) if exchange == "bithumb" else client

    def addresses(self, currency, *, deposit=False):
        c, ex = self.client, self.exchange
        if ex == "coinone":
            raw = c.get_deposit_address(currency) if deposit else c.list_withdraw_addresses(currency=currency)
            return [address(r["currency"], None, r["address"], r.get("secondary_address"), r)
                    for r in rows(raw, "deposit_addresses" if deposit else "withdrawal_addresses")
                    if r.get("currency", "").upper() == currency]
        raw = c.list_deposit_addresses() if deposit else c.list_withdraw_addresses()
        if ex == "korbit":
            return [address(r.get("currency") or currency, r["network"], r["address"], r.get("secondaryAddress"), r)
                    for r in rows(raw) if not r.get("currency") or r["currency"].upper() == currency]
        return [address(r["currency"], r.get("net_type"), r.get("deposit_address") if deposit else r.get("withdraw_address"),
                        r.get("secondary_address"), r) for r in rows(raw) if r.get("currency") == currency]

    @precise
    def quote(self, currency, network, quantity):
        q = decimal(quantity)
        if q <= 0 or not network:
            raise ValueError("WITHDRAW_AMOUNT_INVALID")
        ex, c = self.exchange, self.client
        if ex in {"upbit", "bithumb"}:
            raw = c.get_withdraw_chance(currency=currency, net_type=network)
            limits, asset = raw["withdraw_limit"], raw["currency"]
            if limits.get("can_withdraw") is not True or asset.get("wallet_state") not in {"working", "withdraw_only"}:
                raise ValueError("WITHDRAW_UNAVAILABLE")
            fee = withdrawal_fee_upper_bound(raw, q) if ex == "bithumb" else decimal(asset["withdraw_fee"])
            minimum, precision = decimal(limits["minimum"]), limits["fixed"]
            # Exchange remaining_daily/onetime are quantities. Unknown limits
            # cannot be silently treated as unlimited.
            available = min(decimal(raw["account"]["balance"]), decimal(limits["remaining_daily"]) + fee,
                            decimal(limits["onetime"]) + fee)
            for name in ("remaining_daily_krw", "remaining_daily_fiat"):
                if limits.get(name) is not None:
                    if ex != "bithumb" or (name.endswith("fiat") and limits.get("fiat_currency") != "KRW"):
                        raise ValueError("WITHDRAW_FIAT_LIMIT_UNVERIFIED")
                    if (q + fee) * c.get_krw_price(currency) > decimal(limits[name]):
                        raise ValueError("WITHDRAW_LIMIT_EXCEEDED")
        elif ex == "korbit":
            asset = next((r for r in rows(c.list_currencies()) if r.get("name", "").upper() == currency), None)
            route = next((r for r in (asset or {}).get("networkList", []) if r.get("name") == network), None)
            if not route or route.get("withdrawalStatus") != "launched":
                raise ValueError("WITHDRAW_UNAVAILABLE")
            fee, minimum, precision = decimal(route["withdrawalTxFee"]), decimal(route["withdrawalMinAmount"]), route["withdrawalPrecision"]
            limit = next((r for r in rows(c.get_withdraw_amount(currency)) if r.get("currency", "").upper() == currency), None)
            if not limit:
                raise ValueError("WITHDRAW_POLICY_UNAVAILABLE")
            available = min(decimal(limit["withdrawableAmount"]), decimal(asset["withdrawalMaxAmountPerRequest"]) + fee)
        elif ex == "coinone":
            asset = next((r for r in rows(c.get_currency(currency), "currencies") if r.get("symbol") == currency), None)
            if not asset or asset.get("withdraw_status") != "normal":
                raise ValueError("WITHDRAW_UNAVAILABLE")
            fee, minimum, precision = decimal(asset["withdrawal_fee"]), decimal(asset["withdrawal_min_amount"]), asset["max_precision"]
            limit = c.get_withdraw_limit(currency=currency)["limit"]
            available = decimal(limit["available"])
        else:
            raise ValueError("UNSUPPORTED_EXCHANGE")
        if fee < 0 or minimum < 0 or type(precision) is not int or not 0 <= precision <= 18:
            raise ValueError("WITHDRAW_POLICY_UNAVAILABLE")
        if q < minimum or q != q.quantize(Decimal(1).scaleb(-precision)) or q + fee > available:
            raise ValueError("WITHDRAW_LIMIT_OR_PRECISION")
        return {"fee": exact(fee), "totalDebit": exact(q + fee), "receiveAmount": exact(q),
                "minimum": exact(minimum), "precision": precision}

    def submit(self, selected, quantity, *, travel_rule_type=None):
        c, ex = self.client, self.exchange
        params = {"currency": selected["currency"], "amount": quantity, "address": selected["address"],
                  "secondary_address": selected["memo"] or None}
        if ex == "korbit":
            result = c.create_coin_withdraw(**params, network=selected["network"])
            identifier = result.get("coinWithdrawalId")
        elif ex == "coinone":
            result = c.create_coin_withdraw(**params)
            identifier = result.get("transaction", {}).get("id")
        else:
            extra = withdrawal_beneficiary(selected["raw"], travel_rule_type=travel_rule_type) if ex == "bithumb" else {}
            result = c.create_coin_withdraw(**params, net_type=selected["network"], transaction_type="default", **extra)
            identifier = result.get("uuid")
        if not identifier:
            raise ValueError("WITHDRAW_RESPONSE_ID_MISSING")
        return str(identifier)

    def deposit_fee(self, currency, network):
        if self.exchange == "coinone":
            asset = next((r for r in rows(self.client.get_currency(currency), "currencies") if r.get("symbol") == currency), None)
            if not asset or asset.get("deposit_status") != "normal":
                raise ValueError("DEPOSIT_UNAVAILABLE")
            fee = decimal(asset["deposit_fee"])
        elif self.exchange == "korbit":
            asset = next((r for r in rows(self.client.list_currencies()) if r.get("name", "").upper() == currency), None)
            route = next((r for r in (asset or {}).get("networkList", []) if r.get("name") == network), None)
            if not route or route.get("depositStatus") != "launched":
                raise ValueError("DEPOSIT_UNAVAILABLE")
            fee = decimal(0)
        else:
            status = next((r for r in rows(self.client.list_wallet_statuses()) if r.get("currency") == currency and r.get("net_type") == network), None)
            if not status or status.get("wallet_state") not in {"working", "deposit_only"}:
                raise ValueError("DEPOSIT_UNAVAILABLE")
            fee = decimal(0)
        if fee < 0:
            raise ValueError("DEPOSIT_FEE_INVALID")
        return exact(fee)

    def withdrawal(self, currency, identifier):
        if self.exchange == "korbit":
            raw = self.client.get_withdraw(currency, identifier)
        elif self.exchange == "coinone":
            candidates = rows(self.client.get_coin_transaction(transaction_id=identifier), "transactions")
            raw = next((r for r in candidates if str(r.get("id")) == identifier and r.get("type") == "WITHDRAWAL"), None)
            if raw is None:
                raise ValueError("WITHDRAW_RECEIPT_NOT_FOUND")
        else:
            raw = self.client.get_withdraw(currency=currency, uuid_value=identifier)
        return self.receipt(raw)

    def deposits(self, currency, since):
        if self.exchange == "coinone":
            raw = self.client.list_coin_transactions(currency=currency, type_="DEPOSIT", size=100,
                from_ts=int(max(since - 60, time.time() - 89 * 86400) * 1000), to_ts=int(time.time() * 1000))
            raw = [r for r in rows(raw, "transaction") if r.get("type") == "DEPOSIT"]
        elif self.exchange == "korbit":
            raw = self.client.list_deposits(currency)
        else:
            raw = self.client.list_deposits(currency=currency, limit=100, order_by="desc")
        return [self.receipt(r, deposit=True) for r in rows(raw)]

    def receipt(self, r, *, deposit=False):
        ex = self.exchange
        state = str(r.get("status") if ex in {"korbit", "coinone"} else r.get("state")).upper()
        done = state in ({"DONE", "ACCEPTED", "DEPOSIT_SUCCESS"} if deposit else {"DONE", "WITHDRAWAL_SUCCESS"})
        canceled = state in {"CANCELED", "CANCELLED", "REJECTED", "FAILED", "WITHDRAWAL_FAIL", "WITHDRAWAL_REFUND"}
        stamp = r.get("createdAt") if ex == "korbit" else r.get("created_at")
        if isinstance(stamp, str) and not stamp.isdigit():
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            stamp = parsed.timestamp() if parsed.tzinfo else None
        elif stamp is not None:
            stamp = float(stamp) / 1000
        return {"id": str(r.get("id") if ex in {"coinone", "korbit"} else r.get("uuid")),
                "currency": str(r.get("currency", "")).upper(), "network": r.get("network") if ex == "korbit" else r.get("net_type"),
                "amount": exact(r.get("quantity") if ex == "korbit" else r.get("amount")),
                "fee": exact(r["fee"]) if r.get("fee") is not None else None,
                "address": r.get("to_address") if ex == "coinone" else r.get("address"),
                "memo": str((r.get("secondaryAddress") if ex == "korbit" else r.get("to_secondary_address") if ex == "coinone" else r.get("secondary_address")) or ""),
                "txid": r.get("transactionHash") if ex == "korbit" else r.get("txid"), "at": stamp,
                "state": "DONE" if done else "CANCELED" if canceled else "ACTION_REQUIRED" if state == "ACTIONREQUIRED" else "PENDING"}
