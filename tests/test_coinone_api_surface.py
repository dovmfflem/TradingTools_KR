from __future__ import annotations

import unittest

from src.exchanges.coinone.api_surface import API_SURFACE
from src.exchanges.coinone.coinone_rest import CoinoneRest
from src.exchanges.coinone.coinone_websocket import CoinoneDataBank, CoinoneMyOrder


class CoinoneApiSurfaceTest(unittest.TestCase):
    def test_rest_surface_has_no_missing_entries(self) -> None:
        self.assertEqual(API_SURFACE["modules"]["rest"]["missing"], {})

    def test_rest_declared_methods_exist(self) -> None:
        method_names = [
            "get_range_units",
            "list_markets",
            "get_market",
            "get_orderbook",
            "get_orderbook_parse",
            "get_trades",
            "list_tickers",
            "get_ticker",
            "list_utc_tickers",
            "get_utc_ticker",
            "list_currencies",
            "get_currency",
            "get_candles",
            "get_accounts",
            "get_balance",
            "list_trade_fees",
            "get_trade_fee",
            "get_deposit_address",
            "place_order",
            "cancel_order",
            "cancel_all_orders",
            "get_open_orders",
            "list_all_open_orders",
            "list_open_orders_by_market",
            "get_order",
            "get_order_detail",
            "list_completed_orders",
            "list_krw_transactions",
            "list_coin_transactions",
            "get_coin_transaction",
            "get_withdraw_limit",
            "list_withdraw_addresses",
            "create_coin_withdraw",
            "get_order_reward_markets",
            "list_order_rewards",
        ]
        for method_name in method_names:
            with self.subTest(method=method_name):
                self.assertTrue(hasattr(CoinoneRest, method_name))

    def test_websocket_declared_methods_exist(self) -> None:
        self.assertTrue(hasattr(CoinoneDataBank, "start"))
        self.assertTrue(hasattr(CoinoneDataBank, "stop"))
        self.assertTrue(hasattr(CoinoneDataBank, "get_data"))
        self.assertTrue(hasattr(CoinoneMyOrder, "subscribe_my_order"))


if __name__ == "__main__":
    unittest.main()
