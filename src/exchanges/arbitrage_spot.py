"""Explicit legacy spot surface for global arbitrage; no transport or retries.

wrap_spot preserves the raw Upbit client (including pocket/cashflow methods).
Use ArbitrageSpotAdapter directly when an explicitly bounded Upbit surface is
desired. Streams and financial policy remain outside this compatibility layer.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any

from .spot_trading import spot_adapter
from .arbitrage_depth import open_spot_depth_stream
from .bithumb.bithumb_wallet import BithumbSpotWallet

__all__ = ["wrap_spot", "ArbitrageSpotAdapter", "SpotCapabilities", "SPOT_CAPABILITIES",
           "open_spot_depth_stream", "BithumbSpotWallet"]


@dataclass(frozen=True)
class SpotCapabilities:
    fok: bool = True
    ioc: bool = True
    post_only: bool = True
    identifiers: bool = True
    wallets: bool = True
    batch_orderbooks: bool = True
    # None means no additional quote restriction imposed by this adapter.
    time_in_force_quotes: tuple[str, ...] | None = None
    identifier_max_length: int = 64
    price_limited_market: bool = False
    order_query_requires_market: bool = False
    order_history: bool = True
    individual_fills: bool = True
    configurable_timeout: bool = True
    depth_stream: bool = True
    wallet_statuses: bool = True
    global_open_orders: bool = True


SPOT_CAPABILITIES = MappingProxyType({
    "upbit": SpotCapabilities(),
    "bithumb": SpotCapabilities(time_in_force_quotes=("KRW",), identifier_max_length=36),
    "coinone": SpotCapabilities(fok=False, ioc=False, wallets=False,
        batch_orderbooks=False, time_in_force_quotes=("KRW",), identifier_max_length=150,
        price_limited_market=True, order_query_requires_market=True, order_history=False,
        individual_fills=True),
    "korbit": SpotCapabilities(wallets=False, batch_orderbooks=False,
        time_in_force_quotes=("KRW",), identifier_max_length=36,
        order_query_requires_market=True, order_history=False, individual_fills=True,
        global_open_orders=False),
})


def _exchange_name(exchange: str) -> str:
    name = exchange.strip().lower()
    if name == "digitalx":
        name = "korbit"
    if name not in SPOT_CAPABILITIES:
        raise ValueError(f"unsupported arbitrage spot exchange: {exchange}")
    return name


class ArbitrageSpotAdapter:
    """Dictionary-preserving compatibility wrapper over an existing REST client.

    Only the methods below are exposed. ExchangeRequestError instances propagate
    unchanged, including exchange, rate-limit and outcome-unknown metadata.
    """

    def __init__(self, exchange: str, client: Any):
        self.exchange = _exchange_name(exchange)
        self.client = client
        self.capabilities = SPOT_CAPABILITIES[self.exchange]
        self._spot = spot_adapter(self.exchange, client)
        self._native = None
        if self.exchange in {"coinone", "korbit"}:
            from .arbitrage_native_spot import NativeArbitrageSpot
            self._native = NativeArbitrageSpot(self.exchange, client, self._spot)

    @property
    def api_url(self) -> str:
        if self.exchange == "korbit":
            from .korbit.korbit_rest import BASE_URL
            return BASE_URL
        return self.client.api_url

    @property
    def timeout_seconds(self) -> float:
        return self.client.timeout_seconds

    @timeout_seconds.setter
    def timeout_seconds(self, value: float) -> None:
        if self._native:
            from .arbitrage_timeout import bounded_timeout
            value = bounded_timeout(value)
        self.client.timeout_seconds = value

    @property
    def last_response_metadata(self) -> dict[str, Any]:
        return self.client.last_response_metadata

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
        else:
            self.client._session.close()

    def _ticker(self, ticker: str) -> str:
        if self.exchange == "upbit":
            return ticker
        raw = ticker.strip()
        pair = raw.upper()
        if not re.fullmatch(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}", pair):
            raise ValueError("invalid spot ticker; expected QUOTE-BASE or base-quote")
        if pair.startswith("KRW-"):
            return self._spot.pair(pair).lower()
        if pair.endswith("-KRW"):
            base, quote = pair.split("-")
            return self._spot.pair(f"{quote}-{base}").lower()
        first, second = pair.split("-")
        quotes = {"BTC", "USDT"}
        # Match Upbit's explicit uppercase market-code precedence when both
        # tokens are quote assets (BTC-USDT versus base-quote btc-usdt).
        if first in quotes and (raw.isupper() or second not in quotes):
            return f"{second}-{first}".lower()
        if second in quotes:
            return pair.lower()
        raise ValueError("unsupported spot quote; expected KRW, BTC or USDT")

    def _order(self, data: dict[str, Any]) -> dict[str, Any]:
        if self.exchange == "upbit":
            return data
        result = dict(data)
        for source, alias in (("order_id", "uuid"), ("client_order_id", "identifier"),
                              ("order_type", "ord_type")):
            if source in data:
                result.setdefault(alias, data[source])
        return result

    @staticmethod
    def _uuid(uuid_value: str | None, uuid: str | None) -> str | None:
        if uuid_value is not None and uuid is not None and uuid_value != uuid:
            raise ValueError("uuid and uuid_value disagree")
        return uuid_value if uuid_value is not None else uuid

    def get_accounts(self) -> list[dict[str, Any]]:
        if self._native:
            return self._native.get_accounts()
        return self.client.get_accounts()

    def get_trading_policy(self, ticker: str):
        """Existing exact Policy; reads rules/fees, never rounds or submits."""
        if self._native:
            return self._native.get_trading_policy(ticker)
        raise ValueError("USE_EXISTING_SPOT_POLICY_FOR_LEGACY_EXCHANGE")

    def get_orderbook_parse(self, ticker: str, count: int = 5) -> dict[str, Any]:
        if self._native:
            return self._native.get_orderbook_parse(ticker, count)
        return self.client.get_orderbook_parse(self._ticker(ticker), count=count)

    def get_orderbooks(self, tickers: list[str]) -> list[dict[str, Any]]:
        if self._native:
            return self._native.get_orderbooks(tickers)
        return self.client.get_orderbooks([self._ticker(ticker) for ticker in tickers])

    def get_best_quotes(self, tickers: list[str]) -> list[dict[str, Any]]:
        """All-ticker bid/ask snapshots, not depth or last-trade prices."""
        if self._native:
            return self._native.get_best_quotes(tickers)
        return self.get_orderbooks(tickers)

    def list_trading_pairs(self, *, is_details: bool = False) -> list[dict[str, Any]]:
        if self._native:
            return self._native.list_trading_pairs(is_details=is_details)
        return self.client.list_trading_pairs(is_details=is_details)

    def get_order(self, uuid_value: str | None = None, *, identifier: str | None = None,
                  uuid: str | None = None, ticker: str | None = None) -> dict[str, Any]:
        if self._native:
            return self._native.get_order(self._uuid(uuid_value, uuid), identifier, ticker)
        key = "client_order_id" if self.exchange == "bithumb" else "identifier"
        return self._order(self.client.get_order(self._uuid(uuid_value, uuid), **{key: identifier}))

    def get_order_fill_evidence(self, order_id, *, ticker, order, max_pages=4):
        """Separate bounded read for reconciliation; never called by get_order."""
        if not self._native:
            raise ValueError("SEPARATE_FILL_EVIDENCE_ONLY_FOR_NATIVE_ADAPTERS")
        return self._native.get_order_fill_evidence(order_id, ticker=ticker, order=order, max_pages=max_pages)

    def place_order(self, *, ticker: str, side: str, order_type: str,
                    price: str | float | int | None = None,
                    volume: str | float | int | None = None,
                    identifier: str | None = None,
                    time_in_force: str | None = None,
                    limit_price: str | float | int | None = None) -> dict[str, Any]:
        if self._native:
            return self._native.place_order(ticker=ticker, side=side, order_type=order_type,
                price=price, volume=volume, identifier=identifier,
                time_in_force=time_in_force, limit_price=limit_price)
        if limit_price is not None:
            raise ValueError("PRICE_LIMITED_MARKET_UNSUPPORTED")
        key = "client_order_id" if self.exchange == "bithumb" else "identifier"
        return self._order(self.client.place_order(
            ticker=self._ticker(ticker), side=side, order_type=order_type,
            price=price, volume=volume, time_in_force=time_in_force, **{key: identifier},
        ))

    def cancel_order(self, uuid_value: str | None = None, *, identifier: str | None = None,
                     uuid: str | None = None, ticker: str | None = None) -> dict[str, Any]:
        if self._native:
            return self._native.cancel_order(self._uuid(uuid_value, uuid), identifier, ticker)
        key = "client_order_id" if self.exchange == "bithumb" else "identifier"
        return self._order(self.client.cancel_order(self._uuid(uuid_value, uuid), **{key: identifier}))

    def list_orders(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Native list filters; Bithumb additionally accepts identifiers as an alias.

        Upbit retains its existing ID-list contract, not open/closed pagination.
        Bithumb accepts ticker-only recovery reads through its native list API.
        """
        if self._native:
            return self._native.list_orders(**kwargs)
        if kwargs.get("ticker") is not None:
            kwargs["ticker"] = self._ticker(kwargs["ticker"])
        if self.exchange == "bithumb" and "identifiers" in kwargs:
            if "client_order_ids" in kwargs:
                raise ValueError("use identifiers or client_order_ids, not both")
            kwargs["client_order_ids"] = kwargs.pop("identifiers")
        rows = self.client.list_orders(**kwargs)
        return [self._order(row) for row in rows] if self.exchange == "bithumb" else rows

    def list_wallet_statuses(self) -> Any:
        if self._native:
            return self._native.list_wallet_statuses()
        return self.client.list_wallet_statuses()

    def list_open_orders(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self._native:
            return self._native.list_open_orders(**kwargs)
        if self.exchange == "upbit":
            return self.client.list_open_orders(**kwargs)
        if kwargs.get("state") is None and kwargs.get("states") is None:
            kwargs["state"] = "wait"
        return self.list_orders(**kwargs)

    def list_closed_orders(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self._native:
            raise ValueError("ARBITRAGE_ORDER_HISTORY_UNSUPPORTED_USE_EXPLICIT_IDS")
        if self.exchange == "upbit":
            return self.client.list_closed_orders(**kwargs)
        if kwargs.get("state") is None and kwargs.get("states") is None:
            kwargs["states"] = ["done", "cancel"]
        return self.list_orders(**kwargs)

    def list_order_history_probe(self, *, ticker: str, limit: int = 1):
        """Native permission/health evidence, never closed-order absence proof."""
        if self._native:
            return self._native.list_order_history_probe(ticker=ticker, limit=limit)
        return self.list_closed_orders(ticker=ticker, limit=limit)

    def get_wallet_status(self) -> Any:
        return self.list_wallet_statuses()

    def is_order_missing(self, error: Exception) -> bool:
        return self._spot.is_order_missing(error)


def wrap_spot(exchange: str, client: Any) -> Any:
    """Return raw Upbit or a Bithumb/Coinone/DigitalX compatibility adapter.

    The caller owns credential selection and construction of the REST client.
    This factory performs no I/O and never reads credentials.
    """
    name = _exchange_name(exchange)
    return client if name == "upbit" else ArbitrageSpotAdapter(name, client)
