"""Offline REST book contract; parent KIS runner saves request/test evidence."""
import unittest
from unittest.mock import Mock
import requests
from src.exchanges.api_error import ExchangeRequestError
from test_kis_rest import KisRestTests, Response


def book():
    row = {"aspr_acpt_hour": "123456"}
    for i in range(1, 6):
        row.update({f"futs_askp{i}": str(1400 + i), f"futs_bidp{i}": str(1400 - i),
                    f"askp_rsqn{i}": str(i), f"bidp_rsqn{i}": str(i + 10)})
    return row


class KisRestBookTests(unittest.TestCase):
    def setUp(self):
        self.fixture = KisRestTests()
        self.fixture.setUp()

    def client(self, response):
        self.fixture.cache.values.clear()
        client = self.fixture.client([self.fixture.token_response(), response])
        client.before_request = Mock()
        return client

    def test_full_book_product_mapping_and_shared_quota(self):
        for kind, night, code in [("commodity", False, "CF"), ("index", False, "F"),
                                  ("stock", False, "JF"), ("commodity", True, "CM")]:
            client = self.client(Response({"rt_cd": "0", "output2": book()}))
            result = client.future_orderbook("A75010", product_kind=kind, night=night)
            self.assertEqual(len(result["asks"]), 5)
            self.assertEqual(result["bids"][4], {"price": "1395", "quantity": "15"})
            self.assertEqual(result["time"], "123456")
            method, url, options = client.session.calls[-1]
            self.assertEqual(method, "GET")
            self.assertTrue(url.endswith("/quotations/inquire-asking-price"))
            self.assertEqual(options["params"], {"FID_COND_MRKT_DIV_CODE": code, "FID_INPUT_ISCD": "A75010"})
            self.assertEqual(options["headers"]["tr_id"], "FHMIF10010000")
            self.assertEqual(options["timeout"], (3, 5))
            self.assertEqual(client.before_request.call_count, 2)
            self.assertFalse(any("Approval" in call[1] for call in client.session.calls))

    def test_zero_levels_are_not_invented_and_invalid_levels_rejected(self):
        row = book(); row.update(futs_askp5="0", askp_rsqn5="0")
        self.assertEqual(len(self.client(Response({"rt_cd": "0", "output2": row})).future_orderbook("A75010")["asks"]), 4)
        for patch in [{"futs_askp1": "NaN"}, {"bidp_rsqn1": "-1"}, {"bidp_rsqn1": "1.5"},
                      {"futs_askp1": "0"}, {"aspr_acpt_hour": "invalid"}]:
            with self.subTest(patch=patch), self.assertRaises(ExchangeRequestError):
                self.client(Response({"rt_cd": "0", "output2": {**book(), **patch}})).future_orderbook("A75010")

    def test_timeout_and_rejection_are_single_attempt_reads(self):
        for response in [requests.Timeout(), Response({"rt_cd": "1", "msg_cd": "EGW00201"})]:
            client = self.client(response)
            with self.assertRaises(ExchangeRequestError) as caught:
                client.future_orderbook("A75010")
            self.assertFalse(caught.exception.outcome_unknown)
            self.assertEqual(len(client.session.calls), 2)
