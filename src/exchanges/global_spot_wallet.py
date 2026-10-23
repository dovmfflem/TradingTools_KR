"""Global main-wallet compatibility over existing native wallet methods.

No network inference for Coinone. All transports share an absolute deadline,
including DigitalX's server-time synchronization before a signed mutation.
"""
import re
import time
from datetime import datetime, timezone
from decimal import Decimal

from .domestic_wallet import DomesticWallet, rows
from .spot_trading import spot_adapter, decimal, exact


class DeadlineSession:
    def __init__(self, session, wallet):
        self.session, self.wallet = session, wallet

    def __getattr__(self, name):
        return getattr(self.session, name)

    def request(self, method, url, **kwargs):
        remaining = self.wallet.deadline - self.wallet.clock()
        if remaining <= 0:
            raise TimeoutError("WALLET_DEADLINE_EXCEEDED")
        kwargs["timeout"] = (min(3, remaining / 2), min(3, remaining / 2))
        return self.session.request(method, url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


class GlobalSpotWallet(DomesticWallet):
    def __init__(self, exchange, client, *, currency=None, network=None, network_confirmed=False,
                 clock=time.monotonic):
        if exchange not in {"coinone", "korbit"}:
            raise ValueError("UNSUPPORTED_WALLET_EXCHANGE")
        super().__init__(exchange, client)
        self.currency, self.network = currency, network
        self.network_confirmed = network_confirmed is True
        self.clock, self.deadline = clock, clock() + 30
        self.timeout_seconds = 3
        client._session = DeadlineSession(client._session, self)

    def close(self):
        self.client._session.close()

    def network_check(self, network):
        if not network:
            raise ValueError("WITHDRAW_NETWORK_REQUIRED")
        if self.exchange == "coinone" and (not self.network_confirmed or network != self.network):
            raise ValueError("COINONE_NETWORK_CONFIRMATION_REQUIRED")

    def get_accounts(self):
        balances = spot_adapter(self.exchange, self.client).balance_details()
        return [{"currency": key, "balance": row["available"], "locked": row["locked"]}
                for key, row in balances.items()]

    def list_withdraw_addresses(self):
        if self.exchange == "coinone":
            raw = rows(self.client.list_withdraw_addresses(currency=self.currency), "withdrawal_addresses")
            result = []
            for r in raw:
                currency = str(r["currency"]).upper()
                result.append({"currency": currency, "net_type": self.network if self.network_confirmed and currency == self.currency else None,
                    "withdraw_address": r["address"], "secondary_address": r.get("secondary_address") or "",
                    "network_confirmation_required": True})
            return result
        return [{"currency": str(r.get("currency") or "").upper(), "net_type": r["network"],
                 "withdraw_address": r["address"], "secondary_address": r.get("secondaryAddress") or ""}
                for r in rows(self.client.list_withdraw_addresses())]

    def get_withdraw_chance(self, *, currency, net_type):
        self.network_check(net_type)
        if self.exchange == "coinone":
            asset = self._one([r for r in rows(self.client.get_currency(currency), "currencies") if r.get("symbol") == currency])
            fee, minimum, precision = asset["withdrawal_fee"], asset["withdrawal_min_amount"], asset["max_precision"]
            available = decimal(self.client.get_withdraw_limit(currency=currency)["limit"]["available"])
            enabled = asset.get("withdraw_status") == "normal"
            maximum = max(Decimal(0), available - decimal(fee))
        else:
            asset = self._one([r for r in rows(self.client.list_currencies()) if r.get("name", "").upper() == currency])
            route = self._one([r for r in asset["networkList"] if r.get("name") == net_type])
            fee, minimum, precision = route["withdrawalTxFee"], route["withdrawalMinAmount"], route["withdrawalPrecision"]
            limit = self._one([r for r in rows(self.client.get_withdraw_amount(currency)) if r.get("currency", "").upper() == currency])
            available = decimal(limit["withdrawableAmount"])
            maximum = min(decimal(asset["withdrawalMaxAmountPerRequest"]), max(Decimal(0), available - decimal(fee)))
            enabled = route.get("withdrawalStatus") == "launched"
        if type(precision) is not int or not 0 <= precision <= 18 or any(decimal(v) < 0 for v in (fee, minimum, available, maximum)):
            raise ValueError("WITHDRAW_POLICY_INVALID")
        balances = [r for r in self.get_accounts() if r["currency"] == currency]
        balance = self._one(balances) if balances else {"balance": "0"}
        return {"account": {"balance": exact(min(decimal(balance["balance"]), available))},
                "currency": {"code": currency, "withdraw_fee": exact(fee), "wallet_state": "working" if enabled else "paused",
                             "wallet_support": ["withdraw"] if enabled else []},
                "withdraw_limit": {"minimum": exact(minimum), "fixed": precision, "can_withdraw": enabled,
                                   "onetime": exact(maximum), "remaining_daily": exact(maximum)},
                "limit_semantics": "effective_available_quantity_not_reported_daily_limit"}

    @staticmethod
    def _one(values):
        if len(values) != 1:
            raise ValueError("WALLET_IDENTITY_MISSING_OR_AMBIGUOUS")
        return values[0]

    @staticmethod
    def fee(chance, quantity):
        value = decimal(chance["currency"]["withdraw_fee"])
        if value < 0:
            raise ValueError("WITHDRAW_FEE_INVALID")
        return value

    def get_deposit_address(self, *, currency, net_type):
        self.network_check(net_type)
        self.deposit_fee(currency, net_type)
        selected = self._one([r for r in self.addresses(currency, deposit=True)
                              if r["network"] == net_type or (self.exchange == "coinone" and r["network"] is None)])
        return {"currency": currency, "net_type": net_type, "deposit_address": selected["address"],
                "secondary_address": selected["memo"], "network_confirmation_required": self.exchange == "coinone"}

    def list_wallet_statuses(self):
        if self.exchange == "coinone":
            self.network_check(self.network)
            asset = self._one([r for r in rows(self.client.get_currency(self.currency), "currencies") if r.get("symbol") == self.currency])
            withdraw, deposit = asset.get("withdraw_status") == "normal", asset.get("deposit_status") == "normal"
            state = "working" if withdraw and deposit else "withdraw_only" if withdraw else "deposit_only" if deposit else "paused"
            return [{"currency": self.currency, "net_type": self.network, "wallet_state": state,
                     "network_confirmation_required": True}]
        result = []
        for asset in rows(self.client.list_currencies()):
            for route in asset["networkList"]:
                withdraw, deposit = route.get("withdrawalStatus") == "launched", route.get("depositStatus") == "launched"
                state = "working" if withdraw and deposit else "withdraw_only" if withdraw else "deposit_only" if deposit else "paused"
                result.append({"currency": asset["name"].upper(), "net_type": route["name"], "wallet_state": state})
        return result

    def create_coin_withdraw(self, *, currency, net_type, amount, address, secondary_address=None, **unused):
        self.network_check(net_type)
        identifier = self.submit({"currency": currency, "network": net_type, "address": address,
                                  "memo": secondary_address or ""}, amount)
        return {"uuid": identifier, "currency": currency, "net_type": net_type, "amount": amount, "state": "PROCESSING"}

    def normalized_receipt(self, receipt, *, deposit=False):
        result = {"uuid": receipt["id"], "currency": receipt["currency"], "net_type": receipt["network"],
                  "amount": receipt["amount"], "fee": receipt["fee"], "txid": receipt["txid"],
                  "state": "ACCEPTED" if deposit and receipt["state"] == "DONE" else receipt["state"],
                  "created_at": datetime.fromtimestamp(receipt["at"], timezone.utc).isoformat() if receipt["at"] is not None else None}
        if receipt["address"] is not None:
            result.update(address=receipt["address"], secondary_address=receipt["memo"])
        if self.exchange == "coinone" and result["net_type"] is None and self.network_confirmed:
            result.update(net_type=self.network, network_confirmation_required=True)
        return result

    def get_withdraw(self, *, uuid_value, currency):
        return self.normalized_receipt(self.withdrawal(currency, uuid_value))

    def list_deposits(self, *, currency=None, txids=None, page=1, **unused):
        if not currency or page != 1:
            raise ValueError("DEPOSIT_HISTORY_REQUIRES_CURRENCY_SINGLE_PAGE")
        result = [self.normalized_receipt(r, deposit=True) for r in self.deposits(currency, time.time() - 30 * 86400)]
        # Use the existing wallet deposit-fee policy, not an arbitrary missing
        # field default. Fetch each network's policy only once per response.
        if self.exchange in {"korbit", "coinone"}:
            fees = {}
            for r in result:
                if r["net_type"] and r["fee"] is None:
                    network = r["net_type"]
                    if network not in fees:
                        fees[network] = self.deposit_fee(currency, network)
                    r["fee"] = fees[network]
        return [r for r in result if not txids or r["txid"] in txids]

    def list_withdraws(self, *, currency=None, page=1, **unused):
        if self.exchange == "korbit":
            raise ValueError("KORBIT_WITHDRAWAL_LIST_API_UNAVAILABLE_USE_KNOWN_ID")
        if not currency or page != 1:
            raise ValueError("WITHDRAW_HISTORY_REQUIRES_CURRENCY_SINGLE_PAGE")
        raw = self.client.list_coin_transactions(currency=currency, type_="WITHDRAWAL", size=100)
        return [self.normalized_receipt(self.receipt(r)) for r in rows(raw, "transaction") if r.get("type") == "WITHDRAWAL"]

    def historical_krw_price(self, currency, occurred_at):
        """Exact closed event-minute close, not a trade at the event's second.

        Coinone /reference/chart; DigitalX /llms-full.txt#get-_v2_candles.
        One bounded public read; missing history never falls back to last price.
        """
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z0-9]{1,20}", currency):
            raise ValueError("HISTORICAL_PRICE_CURRENCY_INVALID")
        currency = currency.upper()
        if currency == "KRW":
            return "1"
        if not isinstance(occurred_at, str):
            raise ValueError("HISTORICAL_PRICE_TIMESTAMP_INVALID")
        try:
            stamp = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("HISTORICAL_PRICE_TIMESTAMP_INVALID") from None
        if stamp.tzinfo is None:
            raise ValueError("HISTORICAL_PRICE_TIMESTAMP_INVALID")
        minute = int(stamp.timestamp() // 60) * 60000
        if minute < 0:
            raise ValueError("HISTORICAL_PRICE_TIMESTAMP_INVALID")
        if minute + 60000 > int(time.time() * 1000):
            raise ValueError("HISTORICAL_CANDLE_NOT_CLOSED")
        if self.clock() >= self.deadline:
            raise TimeoutError("WALLET_DEADLINE_EXCEEDED")
        if self.exchange == "coinone":
            raw = self.client.get_candles(currency.lower() + "-krw", interval="1m", timestamp=minute + 59999, size=2)
            candles = rows(raw, "chart")
        else:
            raw = self.client.get_candles(currency.lower() + "_krw", interval="1",
                                          start=minute, end=minute + 59999, limit=2)
            candles = rows(raw)
        if self.clock() >= self.deadline:
            raise TimeoutError("WALLET_DEADLINE_EXCEEDED")
        if any(type(r.get("timestamp")) is not int or r["timestamp"] < 0 for r in candles):
            raise ValueError("HISTORICAL_CANDLE_TIMESTAMP_INVALID")
        matches = [r for r in candles if r["timestamp"] == minute]
        if len(matches) != 1 or decimal(matches[0]["close"]) <= 0:
            raise ValueError("HISTORICAL_KRW_PRICE_UNAVAILABLE")
        return exact(matches[0]["close"])
