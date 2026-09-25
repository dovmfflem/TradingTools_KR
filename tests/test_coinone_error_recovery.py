"""Read retry metadata, with mock HTTP and no authenticated traffic."""
import json
import unittest
from unittest.mock import Mock
import requests
from src.exchanges.coinone.coinone_rest import CoinoneRest, CoinoneRestError


class CoinoneErrorRecoveryTests(unittest.TestCase):
    def test_http_200_preserves_exchange_code_and_classifies_reads(self):
        client = CoinoneRest("fixture", "fixture", timeout_seconds=3)
        self.addCleanup(client._session.close)
        for code, transient in [("405", True), ("104", False), ("40", False), ("12", False), ("107", False)]:
            with self.subTest(code=code):
                response = requests.Response()
                response.status_code = 200
                response._content = json.dumps({"result": "error", "error_code": code, "error_msg": "secret"}).encode()
                client._session.post = Mock(return_value=response)
                with self.assertRaises(CoinoneRestError) as caught:
                    client.get_order(ticker="USDT-KRW", order_id="fixture")
                error = caught.exception
                self.assertEqual((error.code, error.status_code), (code, 200))
                self.assertEqual(error.transient_read_failure, transient)
                self.assertNotIn("secret", str(error))
                self.assertEqual(client._session.post.call_args.kwargs["timeout"], 3)
                self.assertEqual(client._session.post.call_count, 1)

    def test_transport_rate_limit_and_server_errors_are_read_retryable(self):
        for code, status, expected in [("TRANSPORT_FAILED", None, True), ("RATE_LIMITED", 429, True),
                                       ("REQUEST_FAILED", 503, True), ("REQUEST_FAILED", 401, False)]:
            with self.subTest(code=code, status=status):
                self.assertEqual(CoinoneRestError("coinone", code, status_code=status).transient_read_failure, expected)

    def test_mutation_transport_failure_remains_unknown_without_retry(self):
        client = CoinoneRest("fixture", "fixture", timeout_seconds=3)
        self.addCleanup(client._session.close)
        client._session.post = Mock(side_effect=requests.Timeout("secret"))
        with self.assertRaises(CoinoneRestError) as caught:
            client._request("/v2.1/order", {"price": "1360"})
        self.assertTrue(caught.exception.outcome_unknown)
        self.assertEqual(client._session.post.call_count, 1)
