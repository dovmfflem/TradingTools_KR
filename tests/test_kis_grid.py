import base64
import json
import unittest
from unittest.mock import Mock
import requests
import websocket
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from src.exchanges.api_error import ExchangeRequestError
from src.exchanges.kis.futures_websocket import KisFuturesWebSocket
from test_kis_rest import KisRestTests, Response


class KisGridRestTests(KisRestTests):
    def test_gate_covers_token_and_each_http_call(self):
        client = self.client([self.token_response(), Response({"rt_cd": "0", "output": {"ODNO": "123"}})])
        client.before_request = Mock()
        client.future_order("175V10", "buy", price="1350")
        self.assertEqual(client.before_request.call_count, 2)
        client.before_request.side_effect = TimeoutError("quota")
        with self.assertRaises(TimeoutError):
            client.future_order("175V10", "buy", price="1350")
        self.assertEqual(len(client.session.calls), 2)

    def test_day_night_market_limit_cancel_amend_and_capacity(self):
        for night in (False, True):
            c = self.client([self.token_response()] + [Response({"rt_cd": "0", "output": {"ODNO": "0001", "tot_psbl_qty": "3"}}) for _ in range(5)])
            # Each client can reuse the same cached token.
            self.cache.values.clear()
            self.assertEqual(c.future_order("175V10", "buy", night=night, price="1350.5", quantity=2), "0001")
            call = c.session.calls[-1][2]
            self.assertEqual(call["headers"]["tr_id"], "STTN1101U" if night else "TTTO1101U")
            self.assertEqual((call["json"]["UNIT_PRICE"], call["json"]["ORD_DVSN_CD"]), ("1350.5", "01"))
            c.future_order("175V10", "sell", night=night)
            self.assertEqual(c.session.calls[-1][2]["json"]["ORD_DVSN_CD"], "02")
            c.future_cancel("0001", night=night)
            call = c.session.calls[-1][2]
            self.assertEqual(call["headers"]["tr_id"], "TTTN1103U" if night else "TTTO1103U")
            self.assertEqual((call["json"]["RMN_QTY_YN"], call["json"]["ORD_QTY"]), ("Y", "0"))
            c.future_modify("0001", "1352", quantity=1, night=night)
            self.assertEqual(c.session.calls[-1][2]["json"]["RVSE_CNCL_DVSN_CD"], "01")
            self.assertEqual(c.future_capacity("175V10", "buy", price="1350", night=night), 3)
            self.assertEqual(c.session.calls[-1][2]["params"]["UNIT_PRICE"], "1350")

    def test_mutation_timeout_not_retried_and_rejection_is_definitive(self):
        for response, unknown in ((requests.Timeout(), True), (Response({"rt_cd": "1", "msg_cd": "APBK0918"}), False)):
            self.cache.values.clear()
            c = self.client([self.token_response(), response])
            with self.assertRaises(ExchangeRequestError) as caught:
                c.future_cancel("0001", night=True)
            self.assertEqual(caught.exception.outcome_unknown, unknown)
            self.assertEqual(len(c.session.calls), 2)

    def test_invalid_prices_quantities_and_identifiers_do_not_call_network(self):
        c = self.client()
        for fn in (lambda: c.future_order("175V10", "buy", price=0),
                   lambda: c.future_order("175V10", "buy", price="NaN"),
                   lambda: c.future_cancel("x"), lambda: c.future_cancel("1", quantity=0),
                   lambda: c.future_modify("1", None)):
            with self.assertRaises((ValueError, ExchangeRequestError)):
                fn()
        self.assertEqual(c.session.calls, [])

    def test_approval_uses_secretkey_and_timeout(self):
        c = self.client([Response({"approval_key": "fixture"})])
        self.assertEqual(c.get_ws_approval(), "fixture")
        self.assertEqual(c.session.calls[0][2]["json"]["secretkey"], "fixture-secret")
        self.assertEqual(c.session.calls[0][2]["timeout"], (3, 5))


class KisGridWsTests(unittest.TestCase):
    def fixture(self, night=False):
        rest = Mock(cano="12345678", product_code="03")
        rest.get_ws_approval.return_value = "approval-fixture"
        sock = Mock()
        clock = [100.0]
        ws = KisFuturesWebSocket(rest, ["175V10", "175V10"], hts_id="fixture", night=night,
                               connector=Mock(return_value=sock), clock=lambda: clock[0])
        ws.connect()
        self.addCleanup(ws.close)
        return ws, sock, clock

    def ack(self, ws, sock):
        for tr, key in ws.subscriptions:
            sock.recv.return_value = json.dumps({"header": {"tr_id": tr, "tr_key": key},
                "body": {"rt_cd": "0", "output": {"key": "k" * 32, "iv": "i" * 16}}})
            self.assertEqual(ws.receive(), [])
        self.assertTrue(ws.ready)

    def test_both_sessions_subscriptions_quote_notice_and_ping(self):
        for night in (False, True):
            ws, sock, clock = self.fixture(night)
            self.assertEqual(sock.send.call_count, 2)
            ws.connect()
            self.assertEqual(ws.connector.call_count, 1)
            self.assertEqual(ws.notice_tr, "H0MFCNI0" if night else "H0IFCNI0")
            self.ack(ws, sock)
            raw = json.dumps({"header": {"tr_id": "PINGPONG"}})
            sock.recv.return_value = raw
            ws.receive()
            sock.pong.assert_called_once_with(raw.encode())
            fields = ["0"] * 38
            fields[0:3] = ["175V10", "190000", "1351"]
            fields[7] = "1350"
            sock.recv.return_value = "0|" + ws.quote_tr + "|002|" + "^".join(fields * 2)
            events = ws.receive()
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["bid"], "1350")
            fields = [""] * (19 if night else 22)
            fields[1:6] = ["1234567803", "0001", "0000", "02", "0"]
            fields[7:16] = ["175V10", "1", "1350", "190001", "0", "2", "1", "0", "3"]
            pad = PKCS7(128).padder()
            plain = pad.update("^".join(fields).encode()) + pad.finalize()
            enc = Cipher(algorithms.AES(b"k" * 32), modes.CBC(b"i" * 16)).encryptor()
            encrypted = base64.b64encode(enc.update(plain) + enc.finalize()).decode()
            sock.recv.return_value = "1|" + ws.notice_tr + "|001|" + encrypted
            event = ws.receive()[0]
            self.assertEqual((event["orderId"], event["eventQuantity"]), ("0001", "1"))
            self.assertNotIn("cumulativeFilled", event)
            self.assertTrue(event["requiresReconciliation"])

    def test_disconnect_cooldown_reconnect_and_key_reset(self):
        ws, sock, clock = self.fixture()
        self.ack(ws, sock)
        sock.recv.return_value = ""
        with self.assertRaisesRegex(ExchangeRequestError, "WS_CLOSED"):
            ws.receive()
        self.assertFalse(ws.keys)
        with self.assertRaisesRegex(ExchangeRequestError, "RECONNECT_WAIT"):
            ws.connect()
        clock[0] += 10
        ws.connect()
        self.assertFalse(ws.ready)
        self.assertEqual(sock.send.call_count, 4)

    def test_subscription_timeout_and_silent_timeout_close_socket(self):
        for acknowledged in (False, True):
            ws, sock, clock = self.fixture()
            if acknowledged:
                self.ack(ws, sock)
            clock[0] += 91 if acknowledged else 21
            sock.recv.side_effect = websocket.WebSocketTimeoutException()
            with self.assertRaisesRegex(ExchangeRequestError, "TIMED_OUT"):
                ws.receive()
            self.assertEqual(ws.state, "RECONNECT_WAIT")
            self.assertIsNone(ws.socket)

    def test_rejected_subscription_does_not_become_healthy(self):
        ws, sock, clock = self.fixture()
        sock.recv.return_value = json.dumps({"header": {"tr_id": ws.quote_tr, "tr_key": "175V10"}, "body": {"rt_cd": "1"}})
        with self.assertRaisesRegex(ExchangeRequestError, "REJECTED"):
            ws.receive()
        self.assertFalse(ws.ready)
