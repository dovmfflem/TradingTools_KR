"""Mock contracts only. Evidence saved by the focused application test runner.

Sources checked 2026-10-22:
https://docs.digitalx.miraeasset.com/llms/ko/rest_api/withdrawal-crypto.md
https://docs.digitalx.miraeasset.com/llms/ko/rest_api/deposit-crypto.md
https://docs.coinone.co.kr/reference/coin-withdrawal-address-book
https://docs.coinone.co.kr/reference/virtual-asset-deposit-address
https://docs.coinone.co.kr/reference/single-coin-transaction-history
"""
import unittest
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
import requests
from src.exchanges.domestic_wallet import DomesticWallet
from src.exchanges.korbit.korbit_rest import KorbitRest
from src.exchanges.coinone.coinone_rest import CoinoneRest


class WalletTests(unittest.TestCase):
    captures = []

    @classmethod
    def tearDownClass(cls):
        target = Path(__file__).parent / "results" / "domestic-arbitrage" / "mock-wallet-requests.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"mocked": True, "captureTime": datetime.now(timezone.utc).isoformat(),
            "requests": cls.captures}, indent=2), encoding="utf-8")

    def test_digitalx_exact_endpoints_and_explicit_network(self):
        c = KorbitRest("mock-access", "mock-secret"); c.request = Mock(return_value={})
        self.addCleanup(c._session.close)
        calls = [
            (lambda: c.list_currencies(), "GET", "/v2/currencies", None),
            (lambda: c.list_withdraw_addresses(), "GET", "/v2/coin/withdrawableAddresses", {"accountSeq": 1}),
            (lambda: c.get_withdraw_amount("XRP"), "GET", "/v2/coin/withdrawableAmount", {"accountSeq": 1, "currency": "xrp"}),
            (lambda: c.list_deposit_addresses(), "GET", "/v2/coin/depositAddresses", {"accountSeq": 1}),
            (lambda: c.get_withdraw("XRP", "42"), "GET", "/v2/coin/withdrawal", {"accountSeq": 1, "currency": "xrp", "coinWithdrawalId": "42"}),
            (lambda: c.list_deposits("XRP"), "GET", "/v2/coin/recentDeposits", {"accountSeq": 1, "currency": "xrp", "limit": 100}),
        ]
        for invoke, method, path, params in calls:
            invoke()
            self.assertEqual(c.request.call_args.args[:2], (method, path))
            if params: self.assertEqual(c.request.call_args.args[2], params)
            self.captures.append(dict(exchange="korbit", category="wallet", api=path.rsplit("/", 1)[-1], method=method,
                endpoint=path, parameters=params or {}, state="PASSED", responseSummary="Mock endpoint contract; no network"))
        c.request.reset_mock(); c.request.return_value = {"coinWithdrawalId": 42, "status": "pending"}
        w = DomesticWallet("korbit", c)
        self.assertEqual(w.submit(dict(currency="XRP", network="XRP", address="mock-address", memo="42"), "100.000001"), "42")
        self.assertEqual(c.request.call_args.args, ("POST", "/v2/coin/withdrawal", dict(accountSeq=1, currency="xrp", network="XRP", amount="100.000001", address="mock-address", secondaryAddress="42")))
        self.assertEqual(c.request.call_count, 1)

    def test_coinone_corrected_address_and_history_contract(self):
        c = CoinoneRest("mock-access", "mock-secret"); c._request = Mock(return_value={})
        self.addCleanup(c._session.close)
        c.list_withdraw_addresses(currency="XRP")
        c._request.assert_called_with("/v2.1/transaction/coin/withdrawal/address_book", {"currency": "XRP"})
        c.get_deposit_address("xrp")
        c._request.assert_called_with("/v2.1/account/deposit_address", {"currencies": ["XRP"]})
        c.get_coin_transaction(transaction_id="mock-id")
        c._request.assert_called_with("/v2.1/transaction/coin/history/detail", {"id": "mock-id"})
        c.list_coin_transactions(type_="DEPOSIT", size=100, from_ts=1, to_ts=2)
        self.assertTrue(c._request.call_args.args[1]["is_deposit"])

    def test_missing_coinone_network_is_not_invented(self):
        c = Mock(); c.list_withdraw_addresses.return_value = {"withdrawal_addresses": [dict(currency="XRP", address="mock-address", secondary_address="42")]}
        self.assertIsNone(DomesticWallet("coinone", c).addresses("XRP")[0]["network"])

    def test_fee_precision_suspension_and_deposit_independence(self):
        c = Mock()
        route = dict(name="XRP", withdrawalStatus="launched", depositStatus="stopped", withdrawalTxFee=".1", withdrawalMinAmount="10", withdrawalPrecision=6)
        c.list_currencies.return_value = [dict(name="xrp", networkList=[route], withdrawalMaxAmountPerRequest="1000")]
        c.get_withdraw_amount.return_value = [dict(currency="xrp", withdrawableAmount="1000")]
        w = DomesticWallet("korbit", c)
        self.assertEqual(w.quote("XRP", "XRP", "100.123456")["totalDebit"], "100.223456")
        with self.assertRaisesRegex(ValueError, "DEPOSIT_UNAVAILABLE"): w.deposit_fee("XRP", "XRP")
        with self.assertRaisesRegex(ValueError, "PRECISION"): w.quote("XRP", "XRP", "100.1234567")
        route["withdrawalStatus"] = "stopped"
        with self.assertRaisesRegex(ValueError, "WITHDRAW_UNAVAILABLE"): w.quote("XRP", "XRP", "100")

    def test_no_retry_when_withdrawal_reply_is_lost(self):
        c = Mock(); c.create_coin_withdraw.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            DomesticWallet("coinone", c).submit(dict(currency="XRP", network=None, address="mock", memo=""), "100")
        self.assertEqual(c.create_coin_withdraw.call_count, 1)

    def test_coinone_withdrawal_transport_marks_ambiguity_and_forbids_retries(self):
        c = CoinoneRest("mock-access", "mock-secret"); self.addCleanup(c._session.close)
        c._session.post = Mock(side_effect=requests.Timeout())
        with self.assertRaises(Exception) as caught:
            c.create_coin_withdraw(currency="XRP", amount="100", address="mock-address")
        self.assertTrue(caught.exception.outcome_unknown)
        self.assertEqual(c._session.post.call_count, 1)
        self.captures.append(dict(exchange="coinone", category="private-wallet", api="withdrawal", method="POST",
            endpoint="/v2.1/transaction/coin/withdrawal", parameters={"currency": "XRP", "amount": "100", "address": "mock-address"},
            state="PASSED", responseSummary="Mock timeout: outcome_unknown=True, exactly one POST"))
        c._session.mount("https://", requests.adapters.HTTPAdapter(max_retries=1))
        with self.assertRaises(ValueError):
            c.create_coin_withdraw(currency="XRP", amount="100", address="mock-address")
        self.assertEqual(c._session.post.call_count, 1)

    def test_receipt_statuses_do_not_equate_pending_with_done(self):
        row = dict(id=42, currency="xrp", quantity="100", status="actionRequired", createdAt=1700000000000, network="XRP")
        w = DomesticWallet("korbit", Mock())
        self.assertEqual(w.receipt(row)["state"], "ACTION_REQUIRED")
        row["status"] = "done"
        self.assertEqual(w.receipt(row)["state"], "DONE")
        self.assertEqual(w.receipt(row)["at"], 1700000000)


if __name__ == "__main__": unittest.main()
