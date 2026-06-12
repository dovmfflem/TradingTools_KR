from __future__ import annotations

import importlib
import unittest


MODULES = [
    "main",
    "src.core.credentials",
    "src.core.event_constants",
    "src.exchanges.bithumb.bithumb_api",
    "src.exchanges.bithumb.bithumb_rest",
    "src.exchanges.bithumb.bithumb_websocket",
    "src.exchanges.bithumb.bithumb_databank",
    "src.exchanges.bithumb.bithumb_myOrder",
    "src.exchanges.upbit.upbit_rest",
    "src.exchanges.upbit.upbit_databank",
    "src.exchanges.upbit.api_surface",
    "src.exchanges.binance.binance_spot_databank",
    "src.exchanges.binance.binance_futures_databank",
    "src.exchanges.binance.binance_futures_rest",
    "src.exchanges.binance.binance_futures_myorder",
    "src.exchanges.bybit.bybit_spot_databank",
    "src.exchanges.bybit.bybit_futures_databank",
    "src.exchanges.bybit.bybit_spot_rest",
    "src.exchanges.bitget.bitget_spot_databank",
    "src.exchanges.bitget.bitget_futures_databank",
    "src.exchanges.bitget.bitget_spot_rest",
    "src.exchanges.coinone.coinone_api",
    "src.exchanges.coinone.coinone_rest",
    "src.exchanges.coinone.coinone_websocket",
    "src.exchanges.lighter.lighter_databank",
    "src.exchanges.lighter.lighter_rest",
    "src.notifications.telegram_messenger",
    "tools.credentials",
]


class ImportSmokeTest(unittest.TestCase):
    def test_public_modules_import(self) -> None:
        for module_name in MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertIsNotNone(module)


if __name__ == "__main__":
    unittest.main()
