from __future__ import annotations

import unittest

from src.exchanges.bithumb.api_surface import API_SURFACE
from src.exchanges.bithumb.bithumb_rest import BithumbRest
from src.exchanges.bithumb.bithumb_websocket import (
    BithumbDataBank,
    BithumbMyOrder,
    BithumbPublicWebSocket,
)
from examples.bithumb_exchange_test import _find_deposit_address_pair


class BithumbApiSurfaceTest(unittest.TestCase):
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
            {"currency": "USDT", "net_type": "TRX"},
            {"currency": "ETH", "net_type": "ETH"},
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
            "get_minute_candles",
            "get_day_candles",
            "get_week_candles",
            "get_month_candles",
            "get_trades",
            "get_ticker",
            "get_orderbook",
            "get_orderbook_parse",
            "list_warning_markets",
            "list_notices",
            "list_deposit_withdraw_fees",
            "get_accounts",
            "get_order",
            "list_orders",
            "get_open_orders",
            "get_order_chance",
            "place_order",
            "cancel_order",
            "place_orders",
            "cancel_orders",
            "create_twap_order",
            "cancel_twap_order",
            "list_twap_orders",
            "get_withdraw_chance",
            "list_withdraw_addresses",
            "get_withdraw",
            "list_coin_withdraws",
            "list_krw_withdraws",
            "create_coin_withdraw",
            "create_krw_withdraw",
            "cancel_coin_withdraw",
            "list_deposit_addresses",
            "get_deposit_address",
            "create_deposit_address",
            "get_deposit",
            "list_coin_deposits",
            "list_krw_deposits",
            "create_krw_deposit",
            "list_wallet_statuses",
            "list_api_keys",
        ]
        for method_name in method_names:
            with self.subTest(method=method_name):
                self.assertTrue(hasattr(BithumbRest, method_name))

    def test_websocket_declared_methods_exist(self) -> None:
        self.assertTrue(hasattr(BithumbDataBank, "start"))
        self.assertTrue(hasattr(BithumbDataBank, "stop"))
        self.assertTrue(hasattr(BithumbDataBank, "get_data"))
        for method_name in [
            "connect",
            "send_request",
            "recv_once",
            "start_listen",
            "close",
            "subscribe_orderbook",
            "subscribe_ticker",
            "subscribe_trade",
            "subscribe_candle",
        ]:
            with self.subTest(method=method_name):
                self.assertTrue(hasattr(BithumbPublicWebSocket, method_name))
        self.assertTrue(hasattr(BithumbMyOrder, "subscribe_my_order"))
        self.assertTrue(hasattr(BithumbMyOrder, "subscribe_my_asset"))

    def test_twap_parameter_validation(self) -> None:
        client = BithumbRest(api_key="api", secret_key="secret")

        with self.assertRaises(ValueError):
            client.create_twap_order(
                ticker="btc-krw",
                side="bid",
                duration=299,
                frequency=15,
                price="6000",
            )

        with self.assertRaises(ValueError):
            client.create_twap_order(
                ticker="btc-krw",
                side="ask",
                duration=300,
                frequency=17,
                volume="0.001",
            )

        with self.assertRaises(ValueError):
            client.list_twap_orders(limit=101)


if __name__ == "__main__":
    unittest.main()
