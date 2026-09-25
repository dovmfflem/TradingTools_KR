"""Public quote wire contracts; synthetic responses, no exchange credentials."""
import unittest
from unittest.mock import Mock

from src.exchanges.coinone import CoinoneRest
from src.exchanges.korbit import KorbitRest
from src.exchanges.api_error import ExchangeRequestError


BOOK = {"bids": [{"price": "1362", "qty": "5"}, {"price": "1361", "qty": "6"}],
        "asks": [{"price": "1363", "qty": "7"}, {"price": "1364", "qty": "8"}]}


def response(body, status=200):
    return Mock(status_code=status, ok=status < 400, headers={}, json=Mock(return_value=body))


class PublicGridOrderbooksTests(unittest.TestCase):
    def test_coinone_display_depth_maps_to_supported_wire_size(self):
        client = CoinoneRest(access_token="public-test", secret_key="public-test", timeout_seconds=8)
        client._session.close()
        client._session = Mock()
        client._session.get.return_value = response({"result": "success", **BOOK})
        for count, size in [(1, 5), (5, 5), (6, 10), (11, 15), (16, 16), (30, 16)]:
            with self.subTest(count=count):
                result = client.get_orderbook_parse("usdt-krw", count=count)
                client._session.get.assert_called_with(
                    "https://api.coinone.co.kr/public/v2/orderbook/KRW/USDT",
                    params={"size": size}, timeout=8, allow_redirects=False)
                self.assertEqual(len(result["bids"]), min(count, 2))
                self.assertEqual(result["asks"][0]["price"], 1363)

    def test_digitalx_public_best_prices_use_current_host_without_signing(self):
        session = Mock()
        session.request.return_value = response({"success": True, "data": {
            "bids": list(reversed(BOOK["bids"])), "asks": list(reversed(BOOK["asks"]))}})
        client = KorbitRest("", "", session=session)
        client.signed_params = Mock(side_effect=AssertionError("public quote must not sign"))
        result = client.get_orderbook_parse("usdt-krw", count=1)
        session.request.assert_called_once_with("GET", "https://api.digitalx.miraeasset.com/v2/orderbook",
            headers={}, timeout=(3, 3), allow_redirects=False, params="symbol=usdt_krw")
        self.assertEqual(result["bids"], [{"price": 1362.0, "qty": 5.0}])
        self.assertEqual(result["asks"], [{"price": 1363.0, "qty": 7.0}])

    def test_rate_limit_is_propagated_without_retry(self):
        clients = [CoinoneRest(access_token="public-test", secret_key="public-test"), KorbitRest("", "")]
        for client in clients:
            with self.subTest(client=type(client).__name__):
                client._session.close()
                client._session = Mock()
                client._session.get.return_value = response({}, 429)
                client._session.request.return_value = response({}, 429)
                with self.assertRaises(ExchangeRequestError) as caught:
                    client.get_orderbook_parse("usdt-krw", count=1)
                self.assertEqual(caught.exception.status_code, 429)
                self.assertEqual(client._session.get.call_count + client._session.request.call_count, 1)

    def test_invalid_depth_never_sends_request(self):
        clients = [CoinoneRest(access_token="public-test", secret_key="public-test"), KorbitRest("", "")]
        for client in clients:
            client._session.close()
            client._session = Mock()
            for count in [0, -1, True, 1.5]:
                with self.assertRaises(ValueError):
                    client.get_orderbook_parse("usdt-krw", count=count)
            self.assertEqual(client._session.mock_calls, [])
