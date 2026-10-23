"""Real raw transports, synthetic signed HTTP only; evidence saved by fixture."""
from copy import deepcopy
from unittest.mock import patch

from urllib3.util import Timeout
import requests

from src.exchanges.api_error import ExchangeRequestError
from tests.test_arbitrage_native_spot import NativeSpotFixture


NOW = 1800000000000


class NativeFillEvidenceTests(NativeSpotFixture):
    def setUp(self):
        super().setUp()
        self.enterContext(patch("src.exchanges.arbitrage_fill_evidence.time.time", return_value=NOW / 1000))

    def detail(self, exchange="coinone", **changes):
        self.order(exchange, **changes)
        return self.adapters[exchange].get_order(identifier="intent-1", ticker="KRW-XRP")

    def fill(self, exchange="coinone", *, tid=1, qty="1.5", ms=NOW-1000, oid=None, **changes):
        if exchange == "coinone":
            row = dict(trade_id=f"fill-{tid}", order_id=oid or "synthetic-order",
                       quote_currency="krw", target_currency="xrp", is_ask=False,
                       price="3000", qty=qty, timestamp=ms, fee="2.25", fee_currency="krw")
        else:
            row = dict(tradeId=tid, orderId=oid or 123, symbol="xrp_krw", side="buy",
                       price="3000", qty=qty, amt="4500", tradedAt=ms, feeQty="2.25", feeCurrency="krw")
        return {**row, **changes}

    def evidence(self, exchange, order, pages, **kwargs):
        self.bounded_fill_timeout = True
        original_http = self.http
        iterator = iter(pages)

        def http(venue, method, url, **kw):
            self.assertTrue(url.endswith("/completed_orders" if venue == "coinone" else "/myTrades"))
            page = next(iterator)
            self.payload = {"completed_orders": page} if venue == "coinone" else page
            return original_http(venue, method, url, **kw)

        with patch.object(self, "http", side_effect=http):
            return self.adapters[exchange].get_order_fill_evidence(order["uuid"], ticker="KRW-XRP", order=order, **kwargs)

    def test_separate_read_no_hedge_path_fanout_and_input_not_mutated(self):
        for exchange in self.adapters:
            with self.subTest(exchange=exchange):
                before = len(self.calls)
                order = self.detail(exchange)
                self.assertEqual(len(self.calls), before + 1)
                snapshot = deepcopy(order)
                result = self.evidence(exchange, order, [[self.fill(exchange)]])
                self.assertEqual(order, snapshot)
                self.assertTrue(result["trades_complete"])
                self.assertEqual(result["paid_fee"], "2.25")
                self.assertTrue(result["fees_complete"])
                trade = result["trades"][0]
                self.assertEqual(trade["uuid"], "fill-1" if exchange == "coinone" else "1")
                self.assertEqual(trade["volume"], "1.5")
                self.assertEqual(trade["created_at"], "2027-01-15T07:59:59.000Z")
                self.assertEqual(trade["timestamp"], NOW-1000)
                self.assertEqual(self.calls[-1]["request_parameters"].get("size", self.calls[-1]["request_parameters"].get("limit")),
                                 100 if exchange == "coinone" else "1000")
                self.bounded_fill_timeout = False

    def test_exact_order_filter_and_unknown_fee_is_not_zero(self):
        for exchange in self.adapters:
            order = self.detail(exchange, **({"fee": None} if exchange == "coinone" else {}))
            one = self.fill(exchange, **({"fee": None} if exchange == "coinone" else {"feeQty": None}))
            result = self.evidence(exchange, order, [[self.fill(exchange, tid=2, oid="123-other"), one]])
            self.assertEqual(len(result["trades"]), 1)
            self.assertIsNone(result["paid_fee"])
            self.assertFalse(result["fees_complete"])
            self.bounded_fill_timeout = False

    def test_coinone_cursor_pages_and_duplicate_dedup(self):
        order = self.detail()
        first = [self.fill(tid=1, qty="0.5", fee="0.75")]
        first += [self.fill(tid=n, oid="other") for n in range(2, 101)]
        second = [first[0], self.fill(tid=101, qty="1", fee="1.5")]
        result = self.evidence("coinone", order, [first, second])
        self.assertTrue(result["trades_complete"])
        self.assertEqual(len(result["trades"]), 2)
        self.assertEqual(result["fill_evidence_pages"], 2)
        self.assertEqual(self.calls[-1]["request_parameters"]["to_trade_id"], "fill-100")
        params = self.calls[-1]["request_parameters"]
        self.assertEqual(params["to_ts"] - params["from_ts"], 90*24*3600*1000)

    def test_korbit_time_pages_overlap_boundary_millisecond(self):
        order = self.detail("korbit")
        first = [self.fill("korbit", tid=n, oid=999, ms=NOW-500) for n in range(1, 1000)]
        first += [self.fill("korbit", tid=1000, qty="0.5", amt="1500", feeQty="0.75")]
        second = [first[-1], self.fill("korbit", tid=1001, qty="1", amt="3000", feeQty="1.5", ms=NOW-1500)]
        result = self.evidence("korbit", order, [first, second])
        self.assertTrue(result["trades_complete"])
        self.assertEqual(len(result["trades"]), 2)
        self.assertEqual(self.calls[-1]["request_parameters"]["endTime"], str(NOW-999))

    def test_korbit_saturated_timestamp_never_skips_unseen_fills(self):
        order = self.detail("korbit")
        page = [self.fill("korbit", tid=n, oid=999) for n in range(1, 1001)]
        result = self.evidence("korbit", order, [page, page])
        self.assertFalse(result["trades_complete"])
        self.assertNotIn("trades", result)
        self.assertEqual(result["fill_evidence_error"], "FILL_TIMESTAMP_PAGE_SATURATED")

    def test_missing_delayed_or_expired_history_never_fabricates(self):
        for exchange in self.adapters:
            order = self.detail(exchange)
            result = self.evidence(exchange, order, [[]])
            self.assertEqual(result["executed_volume"], "1.5")
            self.assertEqual(result["state"], order["state"])
            self.assertFalse(result["trades_complete"])
            self.assertNotIn("trades", result)
            self.assertEqual(result["fill_evidence_error"], "FILL_HISTORY_INCOMPLETE")
            self.bounded_fill_timeout = False

    def test_page_bound_never_exposes_partial_trades(self):
        order = self.detail()
        page = [self.fill(tid=1, qty="0.5", fee="0.75")]
        page += [self.fill(tid=n, oid="other") for n in range(2, 101)]
        result = self.evidence("coinone", order, [page], max_pages=1)
        self.assertFalse(result["trades_complete"])
        self.assertNotIn("trades", result)
        self.assertEqual(result["fill_evidence_error"], "FILL_PAGE_LIMIT")

    def test_coinone_repeated_cursor_stops(self):
        order = self.detail()
        page = [self.fill(tid=n, oid="other") for n in range(1, 101)]
        result = self.evidence("coinone", order, [page, page])
        self.assertEqual(result["fill_evidence_error"], "FILL_CURSOR_NOT_PROGRESSING")
        self.assertEqual(result["fill_evidence_pages"], 2)

    def test_duplicate_conflict_and_wrong_side_market_fail_closed(self):
        for exchange in self.adapters:
            order = self.detail(exchange)
            wrong_side = {"is_ask": True} if exchange == "coinone" else {"side": "sell"}
            wrong_market = {"target_currency": "btc"} if exchange == "coinone" else {"symbol": "btc_krw"}
            for pages in ([[self.fill(exchange), self.fill(exchange, price="3001")]],
                          [[self.fill(exchange, **wrong_side)]], [[self.fill(exchange, **wrong_market)]]):
                with self.subTest(exchange=exchange), self.assertRaises(ValueError):
                    self.evidence(exchange, order, pages)
            self.bounded_fill_timeout = False

    def test_invalid_times_quantities_prices_ids_fail_closed(self):
        for exchange in self.adapters:
            order = self.detail(exchange)
            timekey = "timestamp" if exchange == "coinone" else "tradedAt"
            idkey = "trade_id" if exchange == "coinone" else "tradeId"
            cases = [{"qty": v} for v in ("0", "-1", "NaN", "Infinity", True)]
            cases += [{"price": "0"}, {idkey: None}, {idkey: True}]
            cases += [{timekey: v} for v in (None, NOW+1, NOW-1000.5, 1800000000, True, "NaN")]
            for changes in cases:
                with self.subTest(exchange=exchange, changes=changes), self.assertRaises((ValueError, TypeError)):
                    self.evidence(exchange, order, [[self.fill(exchange, **changes)]])
            self.bounded_fill_timeout = False

    def test_aggregate_mismatch_rejected_estimate_replaced_by_actual_fills(self):
        order = self.detail(traded_amount=None, average_executed_price="2999")
        result = self.evidence("coinone", order, [[self.fill()]])
        self.assertEqual(result["executed_funds"], "4500.0")
        self.assertEqual(result["average_executed_price"], "3000")
        self.assertFalse(result["executed_funds_estimated"])
        for changes in ({"executed_volume": "1"}, {"executed_funds": "4501", "executed_funds_estimated": False}, {"paid_fee": "4"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.evidence("coinone", {**order, **changes}, [[self.fill()]])

    def test_other_fee_currency_preserved_no_krw_conversion(self):
        order = self.detail("korbit")
        result = self.evidence("korbit", order, [[self.fill("korbit", feeCurrency="xrp", feeQty="0.00075")]])
        self.assertTrue(result["trades_complete"])
        self.assertFalse(result["fees_complete"])
        self.assertIsNone(result["paid_fee"])
        self.assertEqual(result["trades"][0]["fee_currency"], "XRP")

    def test_terminal_zero_fills_is_empty_without_network(self):
        order = self.detail(executed_qty="0", traded_amount="0", fee=None)
        before = len(self.calls)
        result = self.evidence("coinone", order, [])
        self.assertTrue(result["trades_complete"])
        self.assertEqual(result["trades"], [])
        self.assertIsNone(result["paid_fee"])
        self.assertEqual(len(self.calls), before)

    def test_input_guards_before_io(self):
        order = self.detail()
        before = len(self.calls)
        for changes in ({"uuid": "other"}, {"market": "KRW-BTC"}, {"terminal": False}, {"state": "wait"}):
            with self.assertRaises(ValueError):
                self.adapters["coinone"].get_order_fill_evidence(order["uuid"], ticker="KRW-XRP", order={**order, **changes})
        for max_pages in (0, 9, True, 1.5):
            with self.assertRaises(ValueError):
                self.evidence("coinone", order, [], max_pages=max_pages)
        self.assertEqual(len(self.calls), before)

    def test_timeout_total_forwarded_without_mutating_original(self):
        order = self.detail("korbit")
        original = Timeout(total=2, connect=1, read=1.5)
        self.adapters["korbit"].timeout_seconds = original
        saved = self.korb.timeout_seconds
        self.evidence("korbit", order, [[self.fill("korbit")]])
        timeout = self.calls[-1]["timeout_seconds"]
        self.assertLessEqual(timeout["total"], 2)
        self.assertLessEqual(timeout["connect"], 1)
        self.assertLessEqual(timeout["read"], 1.5)
        self.assertIs(self.korb.timeout_seconds, saved)

    def test_deadline_terminal_state_no_retry(self):
        order = self.detail()
        before = len(self.calls)
        with patch("src.exchanges.arbitrage_fill_evidence.time.monotonic", side_effect=[0, 0, 6]):
            with self.assertRaisesRegex(TimeoutError, "FILL_EVIDENCE_DEADLINE"):
                self.evidence("coinone", order, [[self.fill()]])
        self.assertEqual(len(self.calls), before + 1)

    def test_transport_errors_propagate_without_retry(self):
        order = self.detail()
        self.error = requests.exceptions.ReadTimeout("synthetic")
        before = len(self.calls)
        with self.assertRaises(ExchangeRequestError):
            self.evidence("coinone", order, [[]])
        self.assertEqual(len(self.calls), before + 1)

    def test_official_average_purchase_price_preserved_one_call(self):
        for exchange in self.adapters:
            row = dict(currency="xrp", available="1.5", **({"limit": "0.5", "average_price": "2999.12345678"}
                if exchange == "coinone" else {"balance": "2", "avgPrice": "2999.12345678"}))
            self.payload = {"balances": [row]} if exchange == "coinone" else [row]
            before = len(self.calls)
            result = self.adapters[exchange].get_accounts()
            self.assertEqual(len(self.calls), before + 1)
            self.assertEqual(result, [dict(currency="XRP", balance="1.5", locked="0.5", avg_buy_price="2999.12345678")])

    def test_absent_average_not_invented_invalid_average_rejected(self):
        for exchange in self.adapters:
            row = dict(currency="xrp", available="1.5", **({"limit": "0.5"} if exchange == "coinone" else {"balance": "2"}))
            self.payload = {"balances": [row]} if exchange == "coinone" else [row]
            self.assertNotIn("avg_buy_price", self.adapters[exchange].get_accounts()[0])
            key = "average_price" if exchange == "coinone" else "avgPrice"
            for value in ("NaN", "-1", True):
                row[key] = value
                with self.assertRaises(ValueError):
                    self.adapters[exchange].get_accounts()
