"""Synthetic wallet bridge evidence; credentials/addresses are never real."""
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from src.exchanges.global_spot_wallet import GlobalSpotWallet
from src.exchanges.coinone.coinone_rest import CoinoneRest
from src.exchanges.korbit.korbit_rest import KorbitRest


class GlobalWalletContractTests(unittest.TestCase):
    captures = []

    @classmethod
    def tearDownClass(cls):
        target = Path(__file__).parent / "results" / "native-wallet" / "synthetic.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"captureTime": datetime.now(timezone.utc).isoformat(), "network": "blocked",
            "requests": cls.captures}, indent=2), encoding="utf-8")

    def setUp(self):
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("NETWORK_FORBIDDEN")))

    def capture(self, exchange, method, endpoint, parameters, summary):
        self.captures.append(dict(exchange=exchange, category="wallet", api=endpoint, method=method, endpoint=endpoint,
            parameters=parameters, state="PASSED", responseSummary=summary))

    def test_coinone_exact_historical_minute_not_current_price(self):
        raw = CoinoneRest("mock-access", "mock-secret")
        self.addCleanup(raw._session.close)
        raw._public_get = Mock(return_value={"chart": [{"timestamp": 1792627200000, "close": "1400"}]})
        wallet = GlobalSpotWallet("coinone", raw)
        self.assertEqual(wallet.historical_krw_price("USDT", "2026-10-22T00:00:00Z"), "1400")
        raw._public_get.assert_called_once_with("/public/v2/chart/KRW/USDT", params={"interval": "1m", "timestamp": 1792627259999, "size": 2})
        self.capture("coinone", "GET", "/public/v2/chart/KRW/USDT", raw._public_get.call_args.kwargs["params"], "exact historical minute close=1400")
        raw._public_get.return_value = {"chart": []}
        with self.assertRaisesRegex(ValueError, "UNAVAILABLE"):
            wallet.historical_krw_price("USDT", "2026-10-22T00:00:00Z")

    def test_korbit_unknown_response_and_history_gap(self):
        raw = KorbitRest("mock-access", "mock-secret")
        self.addCleanup(raw._session.close)
        raw.request = Mock(side_effect=TimeoutError("synthetic lost reply"))
        wallet = GlobalSpotWallet("korbit", raw)
        with self.assertRaises(TimeoutError):
            wallet.create_coin_withdraw(currency="USDT", net_type="TRX", amount="10", address="synthetic-address")
        raw.request.assert_called_once()
        self.capture("korbit", "POST", "/v2/coin/withdrawal", {"currency": "usdt", "network": "TRX", "amount": "10", "address": "synthetic-address"}, "timeout; exactly one submission; no retry")
        with self.assertRaisesRegex(ValueError, "LIST_API_UNAVAILABLE"):
            wallet.list_withdraws(currency="USDT")

    def test_coinone_missing_network_is_explicit_not_inferred(self):
        raw = CoinoneRest("mock-access", "mock-secret")
        self.addCleanup(raw._session.close)
        raw._request = Mock(return_value={"withdrawal_addresses": [{"currency": "USDT", "address": "synthetic-address"}]})
        wallet = GlobalSpotWallet("coinone", raw)
        self.assertIsNone(wallet.list_withdraw_addresses()[0]["net_type"])
        with self.assertRaisesRegex(ValueError, "CONFIRMATION_REQUIRED"):
            wallet.get_withdraw_chance(currency="USDT", net_type="TRX")
        raw._request.assert_called_once()
        self.capture("coinone", "POST", "/v2.1/transaction/coin/withdrawal/address_book", {}, "network absent; explicit confirmation required before quote/submit")


if __name__ == "__main__":
    unittest.main()
