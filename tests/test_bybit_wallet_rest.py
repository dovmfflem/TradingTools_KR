import json
import unittest
from unittest.mock import Mock, patch

from requests.adapters import HTTPAdapter
from src.exchanges.bybit.bybit_wallet_rest import BybitWalletRest, BybitWalletRestError

ID = "00000000-0000-4000-8000-000000000001"


class BybitWalletTests(unittest.TestCase):
    def setUp(self):
        self.client = BybitWalletRest("synthetic-key", "synthetic-secret", timeout_seconds=(2, 4))
        self.addCleanup(self.client.close)

    def test_transfer_uses_one_post_and_exact_decimal(self):
        response = Mock(ok=True)
        response.json.return_value = {"retCode": 0, "result": {"transferId": ID, "status": "PENDING"}}
        with patch.object(self.client._session, "request", return_value=response) as request:
            result = self.client.create_transfer(transfer_id=ID, coin="USDT", amount="0.123456789012345678",
                                                 from_account_type="FUND", to_account_type="UNIFIED")
        self.assertEqual(request.call_count, 1)
        body = json.loads(request.call_args.kwargs["data"])
        self.assertEqual(body["amount"], "0.123456789012345678")
        self.assertNotIn("fromMemberId", body)
        self.assertEqual(request.call_args.kwargs["timeout"], (2, 4))
        self.assertEqual(result["status"], "PENDING")

    def test_universal_payload(self):
        with patch.object(self.client, "_call", return_value={}) as call:
            self.client.create_transfer(transfer_id=ID, coin="USDT", amount="1", from_account_type="FUND",
                                        to_account_type="UNIFIED", from_member_id="101", to_member_id="102")
        self.assertEqual(call.call_args.args[1], "/v5/asset/transfer/universal-transfer")
        self.assertEqual(call.call_args.kwargs["fromMemberId"], 101)

    def test_invalid_amount_and_ambiguous_identity_do_not_submit(self):
        with patch.object(self.client, "_call") as call:
            for amount in ("NaN", "Infinity", "0", "-1", "no"):
                with self.assertRaises(ValueError):
                    self.client.create_transfer(transfer_id=ID, coin="USDT", amount=amount, from_account_type="FUND", to_account_type="UNIFIED")
            with self.assertRaises(ValueError):
                self.client.create_transfer(transfer_id=ID, coin="USDT", amount="1", from_account_type="FUND", to_account_type="UNIFIED", from_member_id="101")
            call.assert_not_called()

    def test_missing_success_code_is_not_success(self):
        with patch.object(self.client, "_request_signed", return_value={"result": {}}):
            with self.assertRaises(BybitWalletRestError):
                self.client.list_sub_members()

    def test_key_identity_never_returns_credentials(self):
        with patch.object(self.client, "_call", return_value={"apiKey": "synthetic", "secret": "synthetic", "userID": 101}):
            self.assertNotIn("apiKey", self.client.get_api_identity())
            self.assertNotIn("secret", self.client.get_api_identity())

    def test_retrying_transport_is_rejected(self):
        self.client._session.mount("https://", HTTPAdapter(max_retries=1))
        with self.assertRaises(ValueError):
            self.client.list_sub_members()

    def test_history_bounds_and_cursor(self):
        with patch.object(self.client, "_call", return_value={}) as call:
            with self.assertRaises(ValueError):
                self.client.list_transfers(start_time=0, end_time=8 * 86400000)
            call.assert_not_called()
            self.client.list_transfers(universal=True, start_time=0, end_time=86400000, cursor="synthetic-cursor")
            self.assertEqual(call.call_args.kwargs["cursor"], "synthetic-cursor")

    def test_safe_balance_requested(self):
        with patch.object(self.client, "_call", return_value={}) as call:
            self.client.get_coin_balance(coin="USDT", account_type="UNIFIED", to_account_type="FUND")
        self.assertEqual(call.call_args.kwargs["withTransferSafeAmount"], 1)
        self.assertEqual(call.call_args.kwargs["withLtvTransferSafeAmount"], 1)

    def test_rejection_classification_is_conservative_and_redacted(self):
        for status, code, expected in [(200, 131212, "REJECTED"), (403, 10005, "REJECTED"),
                                       (200, 131214, "UNKNOWN"), (200, 10014, "UNKNOWN"),
                                       (200, 10000, "UNKNOWN"), (200, 131230, "UNKNOWN"),
                                       (200, 999999, "UNKNOWN"), (503, 131212, "UNKNOWN")]:
            with self.subTest(status=status, code=code):
                response = Mock(ok=status < 400, status_code=status)
                response.json.return_value = {"retCode": code, "retMsg": "synthetic-secret", "result": {"apiKey": "synthetic-key"}}
                with patch.object(self.client._session, "request", return_value=response) as request:
                    with self.assertRaises(BybitWalletRestError) as raised:
                        self.client.create_transfer(transfer_id=ID, coin="USDT", amount="1",
                                                    from_account_type="FUND", to_account_type="UNIFIED")
                self.assertEqual(raised.exception.acceptance, expected)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(request.call_count, 1)
                self.assertNotIn("synthetic-secret", str(raised.exception))
                self.assertNotIn("synthetic-key", str(raised.exception.__context__))

    def test_transport_timeout_is_never_retried_or_classified_rejected(self):
        from requests.exceptions import Timeout
        with patch.object(self.client._session, "request", side_effect=Timeout("synthetic")) as request:
            with self.assertRaises(Timeout):
                self.client.create_transfer(transfer_id=ID, coin="USDT", amount="1",
                                            from_account_type="FUND", to_account_type="UNIFIED")
        self.assertEqual(request.call_count, 1)
