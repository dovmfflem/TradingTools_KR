"""Fixture-only KIS/Bithumb protocol tests. Results saved by caller test runner."""
from contextlib import nullcontext
from datetime import datetime, timezone
import unittest
import requests

from src.exchanges.kis.kis_rest import KisRest
from src.exchanges.bithumb.spread import BithumbSpread
from src.exchanges.api_error import ExchangeRequestError


class Session:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if isinstance(self.payload, Exception): raise self.payload
        class Response:
            status_code = 200
            headers = {}
            def json(inner): return self.payload
        return Response()


class Cache:
    def locked(self, key): return nullcontext()
    def load(self, key): return {"access_token": "fixture-token", "expires_at": 2000000000}


class FuturesTests(unittest.TestCase):
    def client(self, payload):
        return KisRest("fixture-key", "fixture-secret", "12345678-03", token_cache=Cache(), session=Session(payload),
            clock=lambda: datetime(2026, 9, 24, 17, tzinfo=timezone.utc).timestamp())

    def test_day_night_order_wire_fields_and_no_transport_retry(self):
        for night, tr_id in ((False, "TTTO1101U"), (True, "STTN1101U")):
            client = self.client({"rt_cd": "0", "output": {"ODNO": "000001"}})
            self.assertEqual(client.future_order("175V10", "sell", night=night, quantity=2), "000001")
            method, url, kwargs = client.session.calls[0]
            self.assertEqual(method, "POST")
            self.assertTrue(url.endswith("/trading/order"))
            self.assertEqual(kwargs["headers"]["tr_id"], tr_id)
            self.assertEqual(kwargs["json"]["SHTN_PDNO"], "175V10")
            self.assertEqual(kwargs["json"]["ORD_QTY"], "2")
            self.assertEqual(kwargs["json"]["SLL_BUY_DVSN_CD"], "01")
            self.assertEqual(kwargs["json"]["ORD_DVSN_CD"], "02")
            self.assertEqual(kwargs["timeout"], (3, 5))
            self.assertFalse(kwargs["allow_redirects"])
            self.assertEqual(len(client.session.calls), 1)

    def test_timeout_unknown_order_not_retried_and_secrets_not_exposed(self):
        client = self.client(requests.Timeout("fixture-secret"))
        with self.assertRaises(ExchangeRequestError) as raised:
            client.future_order("175V10", "buy")
        self.assertTrue(raised.exception.outcome_unknown)
        self.assertNotIn("fixture-secret", str(raised.exception))
        self.assertEqual(len(client.session.calls), 1)

    def test_malformed_mutation_response_is_unknown(self):
        client = self.client({"rt_cd": "0", "output": {}})
        with self.assertRaises(ExchangeRequestError) as raised:
            client.future_order("175V10", "sell")
        self.assertTrue(raised.exception.outcome_unknown)

    def test_quote_currency_session_market_codes(self):
        for night, market in ((False, "CF"), (True, "CM")):
            client = self.client({"rt_cd": "0", "output1": {"hts_kor_isnm": "미국달러"},
                "output2": {"futs_bidp1": "1400.10", "futs_askp1": "1400.20", "aspr_acpt_hour": "020001"}})
            quote = client.future_quote("175V10", night=night)
            self.assertEqual(quote["bid"], "1400.10")
            self.assertEqual(client.session.calls[0][2]["params"]["FID_COND_MRKT_DIV_CODE"], market)

    def test_zero_crossed_and_nonfinite_quotes_rejected(self):
        for bid, ask in (("0", "1400"), ("1401", "1400"), ("NaN", "1400")):
            client = self.client({"rt_cd": "0", "output2": {"futs_bidp1": bid, "futs_askp1": ask, "aspr_acpt_hour": "100000"}})
            with self.assertRaises(ExchangeRequestError): client.future_quote("175V10")

    def test_balance_uses_short_code_and_korean_side_official_fields(self):
        client = self.client({"rt_cd": "0", "output1": [
            {"pdno": "KR4STANDARD", "shtn_pdno": "175V10", "sll_buy_dvsn_name": "매도", "cblc_qty": "2"},
            {"pdno": "KR4OTHER", "shtn_pdno": "175V11", "sll_buy_dvsn_name": "매도", "cblc_qty": "5"}],
            "output2": {"dnca_cash": "100000", "ord_psbl_cash": "50000", "evlu_amt_smtl": "28000000"}})
        balance = client.future_account("175V10")
        self.assertEqual(balance["short"], 2)
        self.assertEqual(balance["long"], 0)
        self.assertEqual(balance["buyingPower"], "50000")

    def test_incomplete_balances_never_look_flat(self):
        client = self.client({"rt_cd": "0", "ctx_area_nk200": "more", "output1": [], "output2": {}})
        with self.assertRaisesRegex(ExchangeRequestError, "PAGINATED"): client.future_account("175V10")

    def test_night_history_inclusive_start_exclusive_end_and_normalization(self):
        client = self.client({"rt_cd": "0", "output1": [{"pdno": "175V10", "odno": "000001", "ord_dt": "20260924",
            "sll_buy_dvsn_cd": "01", "ord_qty": "2", "tot_ccld_qty": "1", "qty": "1", "avg_idx": "1400.1"}]})
        rows = client.future_orders("175V10", night=True, since="20260924")
        self.assertEqual(rows[0]["filled"], "1")
        self.assertEqual(rows[0]["remaining"], "1")
        params = client.session.calls[0][2]["params"]
        self.assertEqual(params["END_ORD_DT"], "20260926")
        self.assertEqual(params["STRT_ORD_DT"], "20260924")
        self.assertEqual(client.session.calls[0][2]["headers"]["tr_id"], "STTN5201R")

    def test_truncated_history_cannot_authorize_a_new_order(self):
        client = self.client({"rt_cd": "0", "ctx_area_nk200": "next", "output1": []})
        with self.assertRaisesRegex(ExchangeRequestError, "PAGINATED"): client.future_orders("175V10")

    def test_history_continuation_returns_all_pages_with_a_bounded_cursor(self):
        client = self.client({})
        def row(id):
            return {"pdno": "175V10", "odno": id, "sll_buy_dvsn_cd": "01", "ord_qty": "1", "tot_ccld_qty": "1", "qty": "0", "avg_idx": "1400"}
        payloads = iter([{"rt_cd": "0", "output1": [row("1")], "ctx_area_fk200": "fixture", "ctx_area_nk200": "next"},
                         {"rt_cd": "0", "output1": [row("2")]}])
        original = client.session.request
        def request(*args, **kwargs):
            client.session.payload = next(payloads)
            return original(*args, **kwargs)
        client.session.request = request
        self.assertEqual([r["id"] for r in client.future_orders("175V10")], ["1", "2"])
        self.assertEqual(client.session.calls[1][2]["headers"]["tr_cont"], "N")
        self.assertEqual(client.session.calls[1][2]["params"]["CTX_AREA_NK200"], "next")
        self.assertEqual(len(client.session.calls), 2)

    def test_history_deadline_and_repeated_cursor_fail_without_partial_result(self):
        client = self.client({"rt_cd": "0", "output1": [], "ctx_area_nk200": "same"})
        with self.assertRaisesRegex(ExchangeRequestError, "PAGINATED"): client.future_orders("175V10")
        self.assertEqual(len(client.session.calls), 2)
        # Simulated deadline, no sleeps or network waits.
        client = self.client({"rt_cd": "0", "output1": [], "ctx_area_nk200": "next"})
        initial = client.clock()
        client.clock = iter([initial, initial, initial, initial, initial + 21]).__next__
        with self.assertRaisesRegex(ExchangeRequestError, "PAGINATED"): client.future_orders("175V10")
        self.assertEqual(len(client.session.calls), 1)

    def test_capacity_and_contract_metadata(self):
        client = self.client({"rt_cd": "0", "output": {"tot_psbl_qty": "3"}})
        self.assertEqual(client.future_capacity("175V10", "buy", night=True), 3)
        self.assertTrue(client.session.calls[0][1].endswith("/inquire-psbl-ngt-order"))
        client = self.client({"rt_cd": "0", "output1": {"futs_last_tr_date": "20261019", "hts_kor_isnm": "미국달러"}})
        self.assertEqual(client.future_contract("175V10")["expiry"], "20261019")

    def test_invalid_order_inputs_make_no_network_requests(self):
        client = self.client({})
        for args in (("x", "buy", 1), ("175V10", "bad", 1), ("175V10", "buy", 1.5), ("175V10", "buy", 0)):
            with self.assertRaises(ValueError): client.future_order(args[0], args[1], quantity=args[2])
        self.assertEqual(client.session.calls, [])


class BithumbReadsTests(unittest.TestCase):
    def test_malformed_and_truncated_orders_are_not_empty(self):
        class Client:
            def _request(self, *args, **kwargs): return self.value
        client = Client()
        for value in ({"error": "fixture"}, [{}] * 100):
            client.value = value
            with self.assertRaises(ValueError): BithumbSpread(client).open_orders("KRW-USDT")

    def test_holdings_include_locked_inventory(self):
        class Client:
            def get_accounts(self): return [{"currency": "USDT", "balance": "100", "locked": "200"}]
        self.assertEqual(BithumbSpread(Client()).holdings("USDT")["USDT"], {"available": "100", "total": "300"})


if __name__ == "__main__": unittest.main()
