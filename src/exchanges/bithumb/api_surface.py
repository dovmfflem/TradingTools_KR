from __future__ import annotations

from typing import Any, Final
from urllib.parse import quote


DOC_BASE: Final = "https://apidocs.bithumb.com/reference"


def _doc(slug: str) -> str:
    return f"{DOC_BASE}/{quote(slug)}"


API_SURFACE: Final[dict[str, Any]] = {
    "exchange": "bithumb",
    "documentation": {
        "api_reference_url": _doc("api-레퍼런스"),
        "api_reference_version": "v2.1.5",
        "checked_date": "2026-06-12",
        "notes": [
            "REST endpoints follow Bithumb's current API reference.",
            "No live account/trading request was executed during this surface check.",
        ],
    },
    "modules": {
        "rest": {
            "client": "src.exchanges.bithumb.bithumb_rest.BithumbRest",
            "source_file": "src/exchanges/bithumb/bithumb_rest.py",
            "base_url": "https://api.bithumb.com",
            "implemented": {
                "from_info_yaml(file_path='info.yaml')": {
                    "category": "configuration",
                    "description": "Load bithumb_api_key and bithumb_secret_key.",
                },
                "from_config(source='auto', file_path='info.yaml')": {
                    "category": "configuration",
                    "description": "Load credentials from env, keyring, then info.yaml.",
                },
                "list_trading_pairs(is_details=False)": {
                    "category": "Quotation / Trading Pairs",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/v1/market/all",
                    "doc": _doc("거래-대상-목록-조회"),
                },
                "get_minute_candles(ticker, unit=1, to=None, count=200)": {
                    "category": "Quotation / Candle",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/v1/candles/minutes/{unit}",
                    "doc": _doc("분minute-캔들-조회"),
                },
                "get_day_candles(ticker, to=None, count=200, converting_price_unit=None)": {
                    "category": "Quotation / Candle",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/v1/candles/days",
                    "doc": _doc("일day-캔들-조회"),
                },
                "get_week_candles(ticker, to=None, count=200)": {
                    "category": "Quotation / Candle",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/v1/candles/weeks",
                    "doc": _doc("주week-캔들-조회"),
                },
                "get_month_candles(ticker, to=None, count=200)": {
                    "category": "Quotation / Candle",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/v1/candles/months",
                    "doc": _doc("월month-캔들-조회"),
                },
                "get_trades(ticker, to=None, count=200, cursor=None, days_ago=None)": {
                    "category": "Quotation / Trade",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/v1/trades/ticks",
                    "doc": _doc("체결-내역-조회"),
                },
                "get_ticker(tickers)": {
                    "category": "Quotation / Ticker",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/v1/ticker",
                    "doc": _doc("현재가-조회"),
                },
                "get_orderbook(ticker)": {
                    "category": "Quotation / Orderbook",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/v1/orderbook",
                    "doc": _doc("호가-조회"),
                },
                "get_orderbook_parse(ticker, count=5)": {
                    "category": "local helper",
                    "auth": "none",
                    "description": "Fetch and normalize top ask/bid orderbook levels.",
                },
                "list_warning_markets()": {
                    "category": "Quotation / Market Warning",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/v1/market/virtual_asset_warning",
                    "doc": _doc("경보제-조회"),
                },
                "list_notices(count=None)": {
                    "category": "Quotation / Notice",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/v1/notices",
                    "doc": _doc("공지사항-조회"),
                },
                "list_deposit_withdraw_fees(currency='ALL')": {
                    "category": "Quotation / Fee",
                    "auth": "none",
                    "method": "GET",
                    "endpoint": "/v2/fee/inout/{currency}",
                    "doc": _doc("입출금-수수료-조회"),
                },
                "get_accounts()": {
                    "category": "Exchange / Account",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/accounts",
                    "doc": _doc("전체-자산-조회"),
                },
                "get_order(uuid_value=None, client_order_id=None)": {
                    "category": "Exchange / Order",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/order",
                    "doc": _doc("개별-주문-조회"),
                },
                "list_orders(...)": {
                    "category": "Exchange / Order",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/orders",
                    "doc": _doc("주문-리스트-조회"),
                },
                "get_open_orders(...)": {
                    "category": "Exchange / Order",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/orders",
                    "doc": _doc("주문-리스트-조회"),
                },
                "get_order_chance(ticker)": {
                    "category": "Exchange / Order",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/orders/chance",
                    "doc": _doc("주문-가능-정보"),
                },
                "place_order(...)": {
                    "category": "Exchange / Order",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2/orders",
                    "doc": _doc("주문-요청"),
                },
                "cancel_order(order_id=None, client_order_id=None)": {
                    "category": "Exchange / Order",
                    "auth": "required",
                    "method": "DELETE",
                    "endpoint": "/v2/order",
                    "doc": _doc("주문-취소-접수"),
                },
                "place_orders(orders)": {
                    "category": "Exchange / Order",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v2/orders",
                    "doc": _doc("다건-주문-요청"),
                },
                "cancel_orders(orders)": {
                    "category": "Exchange / Order",
                    "auth": "required",
                    "method": "DELETE",
                    "endpoint": "/v2/orders",
                    "doc": _doc("다건-주문-취소-접수"),
                },
                "create_twap_order(...)": {
                    "category": "Exchange / TWAP",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v1/twap",
                    "doc": _doc("twap-주문-요청"),
                },
                "cancel_twap_order(algo_order_id)": {
                    "category": "Exchange / TWAP",
                    "auth": "required",
                    "method": "DELETE",
                    "endpoint": "/v1/twap",
                    "doc": _doc("twap-주문-취소"),
                },
                "list_twap_orders(...)": {
                    "category": "Exchange / TWAP",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/twap",
                    "doc": _doc("twap-주문내역-조회"),
                },
                "get_withdraw_chance(currency, net_type=None)": {
                    "category": "Exchange / Withdrawal",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/withdraws/chance",
                    "doc": _doc("출금-가능-정보"),
                },
                "list_withdraw_addresses(currency=None, net_type=None)": {
                    "category": "Exchange / Withdrawal",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/withdraws/coin_addresses",
                    "doc": _doc("출금-허용-주소-리스트-조회"),
                },
                "get_withdraw(uuid_value=None, txid=None, currency=None)": {
                    "category": "Exchange / Withdrawal",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/withdraw",
                    "doc": _doc("개별-출금-조회"),
                },
                "list_coin_withdraws(...)": {
                    "category": "Exchange / Withdrawal",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/withdraws",
                    "doc": _doc("출금-리스트-조회"),
                },
                "list_krw_withdraws(...)": {
                    "category": "Exchange / Withdrawal",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/withdraws/krw",
                    "doc": _doc("원화-출금-리스트-조회"),
                },
                "create_coin_withdraw(...)": {
                    "category": "Exchange / Withdrawal",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v1/withdraws/coin",
                    "doc": _doc("가상-자산-출금-요청"),
                },
                "create_krw_withdraw(amount, two_factor_type)": {
                    "category": "Exchange / Withdrawal",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v1/withdraws/krw",
                    "doc": _doc("원화-출금-요청"),
                },
                "cancel_coin_withdraw(uuid_value)": {
                    "category": "Exchange / Withdrawal",
                    "auth": "required",
                    "method": "DELETE",
                    "endpoint": "/v1/withdraws/coin",
                    "doc": _doc("가상-자산-출금-취소"),
                },
                "list_deposit_addresses(currency=None, net_type=None)": {
                    "category": "Exchange / Deposit",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/deposits/coin_addresses",
                    "doc": _doc("전체-입금-주소-조회"),
                },
                "get_deposit_address(currency, net_type=None)": {
                    "category": "Exchange / Deposit",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/deposits/coin_address",
                    "doc": _doc("개별-입금-주소-조회"),
                },
                "create_deposit_address(currency, net_type=None)": {
                    "category": "Exchange / Deposit",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v1/deposits/generate_coin_address",
                    "doc": _doc("입금-주소-생성-요청"),
                },
                "get_deposit(uuid_value=None, txid=None, currency=None)": {
                    "category": "Exchange / Deposit",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/deposit",
                    "doc": _doc("개별-입금-조회"),
                },
                "list_coin_deposits(...)": {
                    "category": "Exchange / Deposit",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/deposits",
                    "doc": _doc("입금-리스트-조회"),
                },
                "list_krw_deposits(...)": {
                    "category": "Exchange / Deposit",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/deposits/krw",
                    "doc": _doc("원화-입금-리스트-조회"),
                },
                "create_krw_deposit(amount)": {
                    "category": "Exchange / Deposit",
                    "auth": "required",
                    "method": "POST",
                    "endpoint": "/v1/deposits/krw",
                    "doc": _doc("원화-입금"),
                },
                "list_wallet_statuses()": {
                    "category": "Exchange / Service",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/status/wallet",
                    "doc": _doc("입출금-현황"),
                },
                "list_api_keys()": {
                    "category": "Exchange / Service",
                    "auth": "required",
                    "method": "GET",
                    "endpoint": "/v1/api_keys",
                    "doc": _doc("api-키-리스트-조회"),
                },
            },
            "not_public_api": {
                "tradeTest(client, test_ticker='usdt-krw')": "Manual live-test helper.",
                "orderbookTest(client, test_ticker='usdt-krw')": "Manual live-test helper.",
            },
            "missing": {},
        },
        "public_websocket": {
            "client": "src.exchanges.bithumb.bithumb_websocket.BithumbPublicWebSocket",
            "source_file": "src/exchanges/bithumb/bithumb_websocket.py",
            "auth": "none",
            "url": "wss://ws-api.bithumb.com/websocket/v1",
            "implemented": {
                "subscribe_orderbook(tickers)": {
                    "category": "Quotation / WebSocket",
                    "doc": _doc("호가-orderbook"),
                },
                "subscribe_ticker(tickers)": {
                    "category": "Quotation / WebSocket",
                    "doc": _doc("현재가-ticker"),
                },
                "subscribe_trade(tickers)": {
                    "category": "Quotation / WebSocket",
                    "doc": _doc("체결-trade"),
                },
                "subscribe_candle(tickers, interval='1m')": {
                    "category": "local helper",
                    "description": "Send candle.* public WebSocket subscription requests when supported by the server.",
                },
                "generic public stream": {
                    "methods": [
                        "connect()",
                        "send_request(request_types)",
                        "recv_once()",
                        "start_listen(on_message=None)",
                        "close()",
                    ],
                },
                "BithumbDataBank": {
                    "category": "legacy local orderbook data collector",
                    "source_file": "src/exchanges/bithumb/bithumb_databank.py",
                    "url": "wss://pubwss.bithumb.com/pub/ws",
                    "methods": [
                        "start()",
                        "stop(timeout_seconds=2.0)",
                        "get_data(ticker=None)",
                    ],
                },
            },
            "missing": {},
        },
        "private_websocket": {
            "client": "src.exchanges.bithumb.bithumb_websocket.BithumbMyOrder",
            "source_file": "src/exchanges/bithumb/bithumb_websocket.py",
            "auth": "required",
            "url": "wss://ws-api.bithumb.com/websocket/v1/private",
            "implemented": {
                "subscribe_my_order(tickers=None)": {
                    "category": "Exchange / WebSocket",
                    "doc": _doc("내-주문-및-체결-myorder"),
                },
                "subscribe_my_asset()": {
                    "category": "Exchange / WebSocket",
                    "doc": _doc("내-자산-myasset"),
                },
                "generic private stream": {
                    "methods": [
                        "from_info_yaml(file_path='info.yaml')",
                        "connect()",
                        "send_request(request_types)",
                        "recv_once()",
                        "start_listen(on_message=None)",
                        "close()",
                    ],
                },
            },
            "missing": {},
        },
    },
}


__all__ = ["API_SURFACE"]
