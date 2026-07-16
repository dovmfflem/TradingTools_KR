from __future__ import annotations

import unittest

from src.exchanges.binance.api_surface import API_SURFACE
from src.exchanges.binance.binance_futures_rest import BinanceFuturesRest
from src.exchanges.binance.binance_spot_rest import BinanceSpotRest


class BinanceApiSurfaceTest(unittest.TestCase):
    def test_surface_has_no_missing_entries(self) -> None:
        self.assertEqual(API_SURFACE["modules"]["spot_rest"]["missing"], {})
        self.assertEqual(API_SURFACE["modules"]["futures_rest"]["missing"], {})

    def test_spot_declared_methods_exist(self) -> None:
        for method_name in ("from_info_yaml", "from_config", "get_account", "get_balances"):
            with self.subTest(method=method_name):
                self.assertTrue(hasattr(BinanceSpotRest, method_name))

    def test_futures_declared_methods_exist(self) -> None:
        for method_name in (
            "from_info_yaml",
            "from_config",
            "get_balances",
            "get_balances_v2",
            "get_user_trades",
        ):
            with self.subTest(method=method_name):
                self.assertTrue(hasattr(BinanceFuturesRest, method_name))

    def test_futures_user_trades_queries_symbol_and_order_id(self) -> None:
        client = BinanceFuturesRest(api_key="api", secret_key="secret")
        calls = []

        def fake_request(method, path, *, params=None, **_kwargs):
            calls.append((method, path, params))
            return [{"orderId": 123, "commission": "0.01"}]

        client._request_signed = fake_request

        result = client.get_user_trades("eth-usdt", "123")

        self.assertEqual(result, [{"orderId": 123, "commission": "0.01"}])
        self.assertEqual(
            calls,
            [("GET", "/fapi/v1/userTrades", {"symbol": "ETHUSDT", "orderId": 123})],
        )

    def test_spot_balance_parser_returns_list(self) -> None:
        client = BinanceSpotRest(api_key="api", secret_key="secret")
        client.get_account = lambda omit_zero_balances=True: {
            "balances": [
                {"asset": "BTC", "free": "0.1", "locked": "0"},
                "invalid",
            ]
        }
        self.assertEqual(
            client.get_balances(),
            [{"asset": "BTC", "free": "0.1", "locked": "0"}],
        )


if __name__ == "__main__":
    unittest.main()
