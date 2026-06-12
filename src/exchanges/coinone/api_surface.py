from __future__ import annotations

from typing import Any, Final


DOC_BASE: Final = "https://docs.coinone.co.kr/reference"


def _doc(slug: str) -> str:
    return f"{DOC_BASE}/{slug}"


API_SURFACE: Final[dict[str, Any]] = {
    "exchange": "coinone",
    "documentation": {
        "api_reference_url": _doc("range-unit"),
        "api_reference_version": "v1.7",
        "checked_date": "2026-06-12",
        "notes": [
            "Public REST endpoints use /public/v2.",
            "Private REST endpoints use /v2.1 and Coinone payload/signature auth.",
            "No live account/trading request was executed during this surface check.",
        ],
    },
    "modules": {
        "rest": {
            "client": "src.exchanges.coinone.coinone_rest.CoinoneRest",
            "source_file": "src/exchanges/coinone/coinone_rest.py",
            "base_url": "https://api.coinone.co.kr",
            "implemented": {
                "from_info_yaml(file_path='info.yaml')": {
                    "category": "configuration",
                    "description": "Load coinone_access_token and coinone_secret_key.",
                },
                "get_range_units(ticker)": {
                    "category": "Public / Market",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/public/v2/range_units/{quote_currency}/{target_currency}",
                    "doc": _doc("range-unit"),
                },
                "list_markets(quote_currency='KRW')": {
                    "category": "Public / Market",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/public/v2/markets/{quote_currency}",
                    "doc": _doc("markets"),
                },
                "get_market(ticker)": {
                    "category": "Public / Market",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/public/v2/markets/{quote_currency}/{target_currency}",
                    "doc": _doc("market"),
                },
                "get_orderbook(ticker, size=None, order_book_unit=None)": {
                    "category": "Public / Orderbook",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/public/v2/orderbook/{quote_currency}/{target_currency}",
                    "doc": _doc("orderbook"),
                },
                "get_orderbook_parse(ticker, count=5)": {
                    "category": "local helper",
                    "auth": "none",
                    "description": "Fetch and normalize top ask/bid orderbook levels.",
                },
                "get_trades(ticker, size=None)": {
                    "category": "Public / Trade",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/public/v2/trades/{quote_currency}/{target_currency}",
                    "doc": _doc("trades"),
                },
                "list_tickers(quote_currency='KRW', additional_data=None)": {
                    "category": "Public / Ticker",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/public/v2/ticker_new/{quote_currency}",
                    "doc": _doc("ticker"),
                },
                "get_ticker(ticker, additional_data=None)": {
                    "category": "Public / Ticker",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/public/v2/ticker_new/{quote_currency}/{target_currency}",
                    "doc": _doc("ticker-1"),
                },
                "list_utc_tickers(quote_currency='KRW', additional_data=None)": {
                    "category": "Public / Ticker",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/public/v2/ticker_utc_new/{quote_currency}",
                    "doc": _doc("ticker-utc"),
                },
                "get_utc_ticker(ticker, additional_data=None)": {
                    "category": "Public / Ticker",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/public/v2/ticker_utc_new/{quote_currency}/{target_currency}",
                    "doc": _doc("ticker-utc-1"),
                },
                "list_currencies()": {
                    "category": "Public / Currency",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/public/v2/currencies",
                    "doc": _doc("currencies"),
                },
                "get_currency(currency)": {
                    "category": "Public / Currency",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/public/v2/currencies/{currency}",
                    "doc": _doc("currency"),
                },
                "get_candles(ticker, interval, timestamp=None, size=None)": {
                    "category": "Public / Chart",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/public/v2/chart/{quote_currency}/{target_currency}",
                    "doc": _doc("chart"),
                },
                "get_accounts()": {
                    "category": "Private / Account",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/account/balance/all",
                    "doc": _doc("balance-all"),
                },
                "get_balance(currencies)": {
                    "category": "Private / Account",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/account/balance",
                    "doc": _doc("balance"),
                },
                "list_trade_fees()": {
                    "category": "Private / Account",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/account/trade_fee",
                    "doc": _doc("trade-fee"),
                },
                "get_trade_fee(ticker)": {
                    "category": "Private / Account",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/account/trade_fee/market",
                    "doc": _doc("trade-fee-market"),
                },
                "get_deposit_address(currency)": {
                    "category": "Private / Account",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/account/deposit_address",
                    "doc": _doc("deposit-address"),
                },
                "place_order(...)": {
                    "category": "Private / Order",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/order",
                    "doc": _doc("order"),
                },
                "cancel_order(order_id=None, ticker, user_order_id=None)": {
                    "category": "Private / Order",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/order/cancel",
                    "doc": _doc("cancel-order"),
                },
                "cancel_all_orders(ticker, order_type=None)": {
                    "category": "Private / Order",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/order/cancel/all",
                    "doc": _doc("cancel-all-orders"),
                },
                "get_open_orders(ticker, side=None, limit=100)": {
                    "category": "Private / Order",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/order/active_orders",
                    "doc": _doc("active-orders"),
                },
                "list_all_open_orders()": {
                    "category": "Private / Order",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/order/active_orders/all",
                    "doc": _doc("active-orders-all"),
                },
                "list_open_orders_by_market(ticker, order_type=None)": {
                    "category": "Private / Order",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/order/active_orders/market",
                    "doc": _doc("active-orders-market"),
                },
                "get_order(ticker, order_id=None, user_order_id=None)": {
                    "category": "Private / Order",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/order/order_info",
                    "doc": _doc("order-info"),
                },
                "get_order_detail(ticker, order_id)": {
                    "category": "Private / Order",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/order",
                    "doc": _doc("order-detail"),
                },
                "list_completed_orders(ticker=None, size=None, from_ts=None, to_ts=None)": {
                    "category": "Private / Order",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/order/completed_orders/{all|market}",
                    "doc": _doc("completed-orders"),
                },
                "list_krw_transactions(...)": {
                    "category": "Private / Transaction",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/transaction/krw/history",
                    "doc": _doc("krw-transaction-history"),
                },
                "list_coin_transactions(...)": {
                    "category": "Private / Transaction",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/transaction/coin/history",
                    "doc": _doc("coin-transaction-history"),
                },
                "get_coin_transaction(transaction_id)": {
                    "category": "Private / Transaction",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/transaction/coin",
                    "doc": _doc("coin-transaction"),
                },
                "get_withdraw_limit(currency)": {
                    "category": "Private / Withdrawal",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/transaction/coin/withdrawal/limit",
                    "doc": _doc("withdrawal-limit"),
                },
                "list_withdraw_addresses(currency=None)": {
                    "category": "Private / Withdrawal",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/transaction/coin/withdrawal/address",
                    "doc": _doc("withdrawal-address"),
                },
                "create_coin_withdraw(...)": {
                    "category": "Private / Withdrawal",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/transaction/coin/withdrawal",
                    "doc": _doc("coin-withdrawal"),
                },
                "get_order_reward_markets()": {
                    "category": "Private / Order Reward",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/order/reward/markets",
                    "doc": _doc("reward-markets"),
                },
                "list_order_rewards(ticker=None, size=None, from_ts=None, to_ts=None)": {
                    "category": "Private / Order Reward",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2.1/order/reward/history",
                    "doc": _doc("reward-history"),
                },
            },
            "missing": {},
        },
        "public_websocket": {
            "client": "src.exchanges.coinone.coinone_websocket.CoinoneDataBank",
            "source_file": "src/exchanges/coinone/coinone_websocket.py",
            "auth": "none",
            "url": "wss://stream.coinone.co.kr",
            "implemented": {
                "orderbook stream": {
                    "category": "Public / WebSocket",
                    "doc": _doc("public-websocket"),
                    "methods": ["start()", "stop(timeout_seconds=2.0)", "get_data(ticker=None)"],
                },
            },
            "missing": {
                "ticker/trade/candle generic public websocket": "Current collector focuses on orderbook data.",
            },
        },
        "private_websocket": {
            "client": "src.exchanges.coinone.coinone_websocket.CoinoneMyOrder",
            "source_file": "src/exchanges/coinone/coinone_websocket.py",
            "auth": "required",
            "url": "wss://stream.coinone.co.kr/v1/private",
            "implemented": {
                "subscribe_my_order(tickers=None)": {
                    "category": "Private / WebSocket",
                    "doc": _doc("private-websocket"),
                },
            },
            "missing": {},
        },
    },
}


__all__ = ["API_SURFACE"]
