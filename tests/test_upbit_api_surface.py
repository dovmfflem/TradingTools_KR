from __future__ import annotations

import unittest

from src.exchanges.upbit.api_surface import API_SURFACE
from src.exchanges.upbit.upbit_databank import (
    UpbitMyOrder,
    UpbitPublicWebSocket,
    _to_upbit_market_code,
)
from src.exchanges.upbit.upbit_rest import UpbitRest, _to_market_code
from examples.upbit_exchange_test import _find_deposit_address_pair


class UpbitApiSurfaceTest(unittest.TestCase):
    def test_market_code_normalization(self) -> None:
        self.assertEqual(_to_market_code("btc-krw"), "KRW-BTC")
        self.assertEqual(_to_market_code("eth-krw"), "KRW-ETH")
        self.assertEqual(_to_market_code("KRW-BTC"), "KRW-BTC")
        self.assertEqual(_to_market_code("BTC-ETH"), "BTC-ETH")
        self.assertEqual(_to_upbit_market_code("btc-krw"), "KRW-BTC")
        self.assertEqual(_to_upbit_market_code("KRW-BTC"), "KRW-BTC")

    def test_deposit_address_pair_prefers_requested_pair(self) -> None:
        addresses = [
            {"currency": "USDT", "net_type": "TRX"},
            {"currency": "BTC", "net_type": "BTC"},
        ]

        self.assertEqual(
            _find_deposit_address_pair(
                addresses,
                preferred_currency="BTC",
                preferred_net_type="BTC",
            ),
            {"currency": "BTC", "net_type": "BTC"},
        )

    def test_deposit_address_pair_returns_none_when_requested_pair_is_missing(self) -> None:
        addresses = [
            {"currency": "LUNC", "net_type": "LUNC"},
            {"currency": "USDT", "net_type": "TRX"},
        ]

        self.assertIsNone(
            _find_deposit_address_pair(
                addresses,
                preferred_currency="BTC",
                preferred_net_type="BTC",
            )
        )

    def test_surface_has_no_missing_entries(self) -> None:
        for module in API_SURFACE["modules"].values():
            self.assertEqual(module["missing"], {})

    def test_rest_declared_methods_exist(self) -> None:
        method_names = [
            "list_trading_pairs",
            "get_second_candles",
            "get_minute_candles",
            "get_day_candles",
            "get_week_candles",
            "get_month_candles",
            "get_year_candles",
            "get_trades",
            "get_ticker",
            "get_quote_tickers",
            "get_orderbook_policy",
            "get_order_chance",
            "from_pocket_info_yaml",
            "test_order",
            "list_orders_by_ids",
            "list_orders",
            "list_open_orders",
            "list_closed_orders",
            "cancel_orders_by_ids",
            "cancel_all_orders",
            "cancel_and_new_order",
            "get_withdraw_chance",
            "get_pocket",
            "list_pocket_api_keys",
            "get_subpocket_accounts",
            "create_main_pocket_transfer",
            "list_main_pocket_transfers",
            "create_subpocket_transfer",
            "list_subpocket_transfers",
            "list_withdraw_addresses",
            "get_withdraw",
            "list_withdraws",
            "create_coin_withdraw",
            "create_krw_withdraw",
            "cancel_withdraw",
            "list_deposit_addresses",
            "get_deposit_address",
            "create_deposit_address",
            "get_deposit",
            "list_deposits",
            "get_deposit_chance",
            "list_deposit_allowed_assets",
            "create_krw_deposit",
            "list_travel_rule_vasps",
            "verify_travel_rule_by_deposit_uuid",
            "verify_travel_rule_by_deposit_txid",
            "list_wallet_statuses",
            "list_api_keys",
        ]
        for method_name in method_names:
            with self.subTest(method=method_name):
                self.assertTrue(hasattr(UpbitRest, method_name))

    def test_websocket_declared_methods_exist(self) -> None:
        public_method_names = [
            "subscribe_orderbook",
            "subscribe_ticker",
            "subscribe_trade",
            "subscribe_candle",
        ]
        for method_name in public_method_names:
            with self.subTest(method=method_name):
                self.assertTrue(hasattr(UpbitPublicWebSocket, method_name))

        self.assertTrue(hasattr(UpbitMyOrder, "subscribe_my_asset"))


if __name__ == "__main__":
    unittest.main()
