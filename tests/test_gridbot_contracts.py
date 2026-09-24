"""Offline account-stream and financial request contracts for grid runtimes."""
import unittest
from unittest.mock import Mock, patch

import requests

from src.exchanges.stream_transport import private_account_connection_config
from src.exchanges.private_events import normalize_private_order_event, normalize_private_asset_event
from src.exchanges.upbit.upbit_rest import UpbitRest
from src.exchanges.bithumb.bithumb_rest import BithumbRest
from src.exchanges.coinone.coinone_rest import CoinoneRest
from src.exchanges.korbit.korbit_rest import KorbitRest
from src.exchanges.korbit.korbit_events import normalize_events as normalize_korbit_events


class GridbotContracts(unittest.TestCase):
    def test_account_stream_batches_markets_and_assets_without_exposing_secrets(self):
        markets = ["KRW-BTC", "KRW-ETH", "KRW-BTC"]
        for exchange in ("upbit", "bithumb", "coinone"):
            with self.subTest(exchange=exchange):
                url, headers, messages = private_account_connection_config(
                    exchange, "fixture-access", "fixture-secret", markets)
                self.assertTrue(url.startswith("wss://"))
                self.assertNotIn("fixture-secret", str(messages))
                self.assertEqual(len(headers) >= 1, True)
                if exchange == "coinone":
                    self.assertEqual([message["channel"] for message in messages], ["MYORDER", "MYASSET"])
                    self.assertEqual(len(messages[0]["topic"]), 2)
                else:
                    self.assertEqual(len(messages), 1)
                    self.assertEqual(messages[0][1]["codes"], ["KRW-BTC", "KRW-ETH"])
                    self.assertEqual(messages[0][2], {"type": "myAsset"})
        with patch.object(KorbitRest, "signed_params", return_value="fixture=1"):
            _, _, messages = private_account_connection_config("korbit", "access", "secret", markets)
        self.assertEqual(messages[0]["symbols"], ["btc_krw", "eth_krw"])
        self.assertEqual(messages[1]["type"], "myAsset")
        self.assertEqual(messages[2], {"requestId": 3, "method": "subscribe", "type": "myTrade",
                                       "symbols": ["btc_krw", "eth_krw"], "accountSeqs": [1]})
        for invalid in ([], ["../private"], ["KRW-BTC", "bad"]):
            with self.assertRaises(ValueError):
                private_account_connection_config("upbit", "access", "secret", invalid)

    def test_korbit_trade_and_asset_events_preserve_exact_account_values(self):
        trade = {"channelType": "myTrade", "symbol": "btc_krw", "timestamp": 1700000001000,
                 "trade": {"accountSeq": 1, "trades": [{"tradeId": 123456, "orderId": 456789,
                           "side": "buy", "price": "99051000", "qty": "0.001300000000000001",
                           "fee": "50", "feeCurrency": "krw", "filledAt": 1700000000000}]}}
        events = normalize_korbit_events(trade, "KRW-BTC")
        self.assertEqual(events[0]["event"], "fill")
        self.assertEqual(events[0]["exact"]["fillId"], "123456")
        self.assertEqual(events[0]["exact"]["fillVolume"], "0.001300000000000001")
        self.assertEqual(events[0]["exact"]["feeCurrency"], "KRW")
        self.assertEqual(normalize_korbit_events(trade, "KRW-ETH"), [])
        trade["trade"]["accountSeq"] = 2
        self.assertEqual(normalize_korbit_events(trade, "KRW-BTC"), [])
        self.assertEqual(normalize_korbit_events(trade, "KRW-BTC", account_seq=2)[0]["event"], "fill")
        assets = {"channelType": "myAsset", "asset": {"accountSeq": 1, "assets": [
            {"currency": "btc", "balance": "10", "available": "7", "tradeInUse": "2",
             "withdrawalInUse": "1", "avgPrice": "50000", "updatedAt": 1700000000000}]}}
        balance = normalize_korbit_events(assets, "KRW-BTC")[0]["assets"][0]
        self.assertEqual(balance["locked"], "3")
        self.assertEqual(balance["total"], "10")
        self.assertEqual(balance["available"], "7")

    def test_exact_fill_fields_do_not_invent_missing_identifiers_or_zero(self):
        bithumb = {"type": "myOrder", "code": "KRW-BTC", "order_id": "order-1", "state": "trade",
                   "order_price": "100.10", "order_quantity": "1.0", "remaining_quantity": "0.876543210987654322",
                   "client_order_id": "client-1", "trade_id": "fill-1", "trade_price": "100.09",
                   "trade_quantity": "0.123456789012345678", "paid_fee": "0.00000001"}
        event = normalize_private_order_event("bithumb", bithumb, "KRW-BTC")
        self.assertEqual(event["exact"]["fillVolume"], "0.123456789012345678")
        self.assertEqual(event["exact"]["remainingVolume"], "0.876543210987654322")
        self.assertEqual(event["exact"]["clientOrderId"], "client-1")
        self.assertEqual(event["exact"]["fillId"], "fill-1")
        self.assertEqual(event["exact"]["fee"], "0.00000001")
        sparse = normalize_private_order_event("upbit", {"type": "myOrder", "code": "KRW-BTC",
            "uuid": "order-2", "state": "trade", "price": "100.09"}, "KRW-BTC")
        self.assertNotIn("fillVolume", sparse["exact"])
        self.assertNotIn("fillId", sparse["exact"])
        self.assertNotIn("originalPrice", sparse["exact"])
        self.assertNotIn("price", sparse["order"])
        upbit_fill = normalize_private_order_event("upbit", {"type": "myOrder", "code": "KRW-BTC",
            "uuid": "order-2", "state": "trade", "trade_uuid": "fill-2", "price": "100.09",
            "volume": "0.123456789012345678", "executed_volume": "0.5", "trade_fee": "0.01"}, "KRW-BTC")
        self.assertEqual(upbit_fill["exact"]["fillVolume"], "0.123456789012345678")
        self.assertEqual(upbit_fill["exact"]["cumulativeFilled"], "0.5")
        coinone_cancel = normalize_private_order_event("coinone", {"response_type": "DATA", "channel": "MYORDER",
            "data": {"quote_currency": "KRW", "target_currency": "BTC", "order_id": "c1",
                     "status": "cancel", "executed_qty": "0.2"}}, "KRW-BTC")
        self.assertNotIn("fillVolume", coinone_cancel["exact"])
        coinone_fill = normalize_private_order_event("coinone", {"response_type": "DATA", "channel": "MYORDER",
            "data": {"quote_currency": "KRW", "target_currency": "BTC", "order_id": "c1",
                     "status": "trade", "trade_id": "f3", "executed_price": "101.1",
                     "executed_qty": "0.123456789012345678", "executed_fee": "0.001"}}, "KRW-BTC")
        self.assertEqual(coinone_fill["exact"]["fillVolume"], "0.123456789012345678")
        self.assertEqual(coinone_fill["exact"]["fee"], "0.001")
        asset = normalize_private_asset_event("coinone", {"response_type": "DATA", "channel": "MYASSET",
            "data": {"assets": [{"currency": "btc", "available": "0.123456789012345678", "limit": "0.1"}]}})
        self.assertEqual(asset["assets"][0]["available"], "0.123456789012345678")
        self.assertEqual(asset["assets"][0]["locked"], "0.1")

    def test_rate_limit_metadata_and_unknown_mutation_are_safe_and_single_attempt(self):
        clients = [(UpbitRest("key", "secret"), "upbit"),
                   (BithumbRest("key", "secret"), "bithumb"),
                   (CoinoneRest("key", "secret"), "coinone")]
        for client, exchange in clients:
            with self.subTest(exchange=exchange):
                response = Mock(status_code=429, ok=False,
                    headers={"Retry-After": "3", "Remaining-Req": "group=order; min=2; sec=0"},
                    json=lambda: {"error": {"name": "too_many_requests"}})
                with patch.object(client._session, "post" if exchange == "coinone" else "request",
                                  return_value=response) as send:
                    with self.assertRaises(Exception) as caught:
                        if exchange == "coinone":
                            client._request("/v2.1/order", {"side": "BUY"})
                        else:
                            client._request("POST", "/v1/orders" if exchange == "upbit" else "/v2/orders",
                                            json_body={"side": "bid"})
                    error = caught.exception
                    self.assertEqual(error.status_code, 429)
                    self.assertEqual(error.retry_after_seconds, 3)
                    self.assertEqual(error.rate_limit, "group=order; min=2; sec=0")
                    self.assertNotIn("secret", str(error))
                    send.assert_called_once()
                    self.assertFalse(send.call_args.kwargs["allow_redirects"])
                with patch.object(client._session, "post" if exchange == "coinone" else "request",
                                  side_effect=requests.Timeout("secret-raw-url")) as send:
                    with self.assertRaises(Exception) as caught:
                        if exchange == "coinone":
                            client._request("/v2.1/order", {"side": "BUY"})
                        else:
                            client._request("POST", "/v1/orders" if exchange == "upbit" else "/v2/orders")
                    self.assertTrue(caught.exception.outcome_unknown)
                    self.assertNotIn("secret-raw-url", str(caught.exception))
                    send.assert_called_once()
            client._session.close()

    def test_korbit_typed_methods_keep_protocol_inside_library(self):
        client = KorbitRest("key", "secret", session=Mock())
        client.request = Mock(return_value={"orderId": 123})
        client.get_balances()
        self.assertEqual(client.request.call_args.args, ("GET", "/v2/balance", {"accountSeq": 1}))
        client.place_order(symbol="btc_krw", side="buy", order_type="limit", client_order_id="client-1",
                           price="100", qty="1")
        self.assertEqual(client.request.call_args.args, ("POST", "/v2/orders",
            {"symbol": "btc_krw", "side": "buy", "orderType": "limit", "accountSeq": 1,
             "clientOrderId": "client-1", "price": "100", "qty": "1"}))
        client.cancel_order(symbol="btc_krw", order_id="123")
        self.assertEqual(client.request.call_args.args, ("DELETE", "/v2/orders",
            {"symbol": "btc_krw", "orderId": "123", "accountSeq": 1}))
        client.get_order("btc_krw", client_order_id="client-1")
        self.assertEqual(client.request.call_args.args, ("GET", "/v2/orders",
            {"symbol": "btc_krw", "clientOrderId": "client-1", "accountSeq": 1}))
        client.list_all_orders("btc_krw", start_time=1000, end_time=2000)
        self.assertEqual(client.request.call_args.args, ("GET", "/v2/allOrders",
            {"symbol": "btc_krw", "accountSeq": 1, "limit": 100,
             "startTime": 1000, "endTime": 2000}))
        with self.assertRaises(ValueError):
            client.get_order("btc_krw")
        with self.assertRaises(ValueError):
            client.cancel_order(symbol="btc_krw", order_id="1", client_order_id="c1")

    def test_coinone_market_fee_uses_current_documented_path(self):
        client = CoinoneRest("key", "secret")
        client._request = Mock(return_value={"fee_rates": []})
        client.get_trade_fee("BTC-KRW")
        client._request.assert_called_once_with("/v2.1/account/trade_fee/KRW/BTC")
        client._session.close()

    def test_coinone_order_reconciliation_uses_documented_detail_endpoint(self):
        client = CoinoneRest("key", "secret")
        client._request = Mock(return_value={"result": "success", "order": {"order_id": "fixture"}})
        client.get_order(ticker="BTC-KRW", user_order_id="gridbot-123")
        client._request.assert_called_once_with("/v2.1/order/detail", {
            "quote_currency": "KRW", "target_currency": "BTC",
            "order_id": None, "user_order_id": "gridbot-123"})
        with self.assertRaises(ValueError):
            client.get_order(ticker="BTC-KRW", order_id="o1", user_order_id="gridbot-123")
        client._session.close()

    def test_mutation_refuses_an_implicitly_retrying_http_adapter(self):
        client = UpbitRest("key", "secret")
        client._session.mount("https://", requests.adapters.HTTPAdapter(max_retries=2))
        with patch.object(client._session, "request") as send:
            with self.assertRaisesRegex(ValueError, "retries disabled"):
                client._request("POST", "/v1/orders", json_body={"side": "bid"})
            send.assert_not_called()
        client._session.close()

    def test_successful_responses_expose_thread_local_quota_without_raw_headers(self):
        client = UpbitRest("key", "secret")
        response = Mock(status_code=200, ok=True,
                        headers={"Remaining-Req": "group=market; min=10; sec=4", "Authorization": "secret"},
                        json=lambda: {"market": "KRW-BTC"})
        with patch.object(client._session, "request", return_value=response):
            self.assertEqual(client._public_request("GET", "/v1/orderbook"), {"market": "KRW-BTC"})
        self.assertEqual(client.last_response_metadata["rateLimit"], "group=market; min=10; sec=4")
        self.assertNotIn("secret", str(client.last_response_metadata))
        with patch.object(client._session, "request", side_effect=requests.Timeout("raw-secret")):
            with self.assertRaises(Exception):
                client._public_request("GET", "/v1/orderbook")
        self.assertEqual(client.last_response_metadata, {})
        client._session.close()


if __name__ == "__main__":
    unittest.main()
