import unittest
from unittest.mock import Mock
from src.exchanges.bybit.bybit_futures_public import BybitFuturesPublic
from src.exchanges.bybit.bybit_spot_rest import BybitSpotRestError
from src.exchanges.upbit.upbit_rest import UpbitRest
from src.exchanges.bithumb.bithumb_rest import BithumbRest
from src.exchanges.binance.binance_futures_rest import BinanceFuturesRest


class PublicExplorerTests(unittest.TestCase):
    def test_spot_batch_endpoint_and_bound(self):
        for cls in (UpbitRest, BithumbRest):
            client = cls("fake", "fake", timeout_seconds=3)
            try:
                client._public_request = Mock(return_value=[{"market": "KRW-ETH"}])
                self.assertEqual(client.get_orderbooks(["eth-krw", "btc-krw"]), [{"market": "KRW-ETH"}])
                args, kwargs = client._public_request.call_args
                self.assertEqual(args, ("GET", "/v1/orderbook"))
                self.assertEqual(kwargs["params"]["markets"], "KRW-ETH,KRW-BTC")
                with self.assertRaises(ValueError):
                    client.get_orderbooks(["KRW-ETH"] * 101)
                self.assertEqual(client._public_request.call_count, 1)
            finally:
                client._session.close()

    def test_binance_batch_quotes_not_signed(self):
        client = BinanceFuturesRest("fake", "fake", timeout_seconds=3)
        try:
            client._request_public = Mock(return_value=[{"symbol": "ETHUSDT"}])
            self.assertEqual(client.get_book_tickers(), [{"symbol": "ETHUSDT"}])
            client._request_public.assert_called_once_with("GET", "/fapi/v1/ticker/bookTicker")
        finally:
            client._session.close()

    def test_bybit_category_and_api_failure_no_retry(self):
        client = BybitFuturesPublic(timeout_seconds=3)
        try:
            client._client._request_public = Mock(return_value={"retCode": 0, "result": {"list": [], "nextPageCursor": "a"}})
            self.assertEqual(client.get_instruments_page("previous")["nextPageCursor"], "a")
            self.assertEqual(client._client._request_public.call_args.kwargs["params"], {"category": "linear", "status": "Trading", "limit": 1000, "cursor": "previous"})
            client._client._request_public.reset_mock()
            client._client._request_public.return_value = {"retCode": 10006, "retMsg": "rate limit"}
            with self.assertRaises(BybitSpotRestError):
                client.get_tickers()
            self.assertEqual(client._client._request_public.call_count, 1)
            self.assertEqual(client._client.timeout_seconds, 3)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
