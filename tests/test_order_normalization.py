"""Synthetic responses only. No network, credentials, or account data."""
from decimal import Decimal
from unittest import TestCase

from src.core.order_models import Acceptance, OrderState
from src.exchanges.binance.order_normalization import normalize_order as binance
from src.exchanges.upbit.order_normalization import normalize_order as upbit


class OrderNormalizationTests(TestCase):
    def test_identity_is_normalized_without_inventing_missing_values(self):
        spot = upbit({"market": "KRW-XRP", "side": "bid"})
        futures = binance({"symbol": "xrpusdt", "side": "Sell"})
        self.assertEqual((spot.market, spot.side), ("KRW-XRP", "BUY"))
        self.assertEqual((futures.market, futures.side), ("XRPUSDT", "SELL"))
        for normalize in (upbit, binance):
            self.assertIsNone(normalize({}).market)
            self.assertIsNone(normalize({}).side)

    def test_ack_is_not_a_fill(self):
        for normalize, payload in [(upbit, {"uuid": "synthetic-order"}), (binance, {"orderId": 9007199254740993})]:
            result = normalize(payload)
            self.assertEqual(result.acknowledgement.acceptance, Acceptance.ACCEPTED)
            self.assertIsNone(result.filled_quantity)
            self.assertIsNone(result.average_fill_price)
            self.assertEqual(result.state, OrderState.UNKNOWN)
            self.assertFalse(result.terminal)
        self.assertEqual(binance({"orderId": 9007199254740993}).order_id, "9007199254740993")

    def test_client_id_is_not_an_exchange_order_id(self):
        result = binance({"clientOrderId": "synthetic-client"})
        self.assertIsNone(result.order_id)
        self.assertEqual(result.acknowledgement.acceptance, Acceptance.UNKNOWN)

    def test_zero_and_unknown_are_different(self):
        for value in (None, "", "NaN", "Infinity", "-1", "nonsense", True):
            with self.subTest(value=value):
                self.assertIsNone(binance({"executedQty": value}).filled_quantity)
        self.assertEqual(binance({"executedQty": "0"}).filled_quantity, Decimal("0"))

    def test_upbit_market_buy_budget_is_not_average_price(self):
        result = upbit({"uuid": "synthetic", "state": "wait", "ord_type": "price", "price": "100000"})
        self.assertIsNone(result.average_fill_price)

    def test_upbit_average_uses_complete_fill_evidence(self):
        payload = {"state": "done", "executed_volume": "3", "remaining_volume": "0",
                   "trades": [{"volume": "1", "funds": "100"}, {"volume": "2", "funds": "220"}]}
        result = upbit(payload)
        self.assertEqual(result.filled_quantity, Decimal("3"))
        self.assertAlmostEqual(result.average_fill_price, Decimal(320) / Decimal(3))
        payload["trades"].pop()
        self.assertIsNone(upbit(payload).average_fill_price)

    def test_binance_average_never_falls_back_to_order_limit(self):
        result = binance({"status": "NEW", "executedQty": "0", "price": "999", "avgPrice": "0"})
        self.assertIsNone(result.average_fill_price)
        result = binance({"status": "FILLED", "executedQty": "2", "origQty": "2", "cumQuote": "200", "avgPrice": "0"})
        self.assertEqual(result.average_fill_price, Decimal("100"))
        self.assertEqual(result.remaining_quantity, Decimal("0"))

    def test_partial_cancel_retains_fills(self):
        for normalize, payload in [(upbit, {"state": "cancel", "executed_volume": "0.25"}),
                                   (binance, {"status": "CANCELED", "executedQty": "0.25"})]:
            result = normalize(payload)
            self.assertEqual(result.state, OrderState.CANCELED)
            self.assertEqual(result.filled_quantity, Decimal("0.25"))
            self.assertTrue(result.terminal)

    def test_done_without_quantity_is_incomplete_evidence(self):
        self.assertIn("INCOMPLETE_FILL_EVIDENCE", upbit({"state": "done"}).issues)
        self.assertIn("INCOMPLETE_FILL_EVIDENCE", binance({"status": "FILLED"}).issues)

    def test_unknown_state_stays_unknown(self):
        result = binance({"status": "NEW_EXCHANGE_STATE", "executedQty": "3"})
        self.assertEqual(result.state, OrderState.UNKNOWN)
        self.assertEqual(result.filled_quantity, Decimal("3"))
        self.assertFalse(result.terminal)

    def test_partial_and_expired_states(self):
        self.assertEqual(upbit({"state": "wait", "executed_volume": "1"}).state, OrderState.PARTIALLY_FILLED)
        self.assertEqual(binance({"status": "EXPIRED_IN_MATCH"}).state, OrderState.EXPIRED)

    def test_invalid_shape_fails_closed(self):
        for normalize in (upbit, binance):
            with self.assertRaises(TypeError):
                normalize([])

    def test_inconsistent_fill_amount_is_marked(self):
        self.assertIn("INCOMPLETE_FILL_EVIDENCE", binance({"status": "FILLED", "executedQty": "1", "origQty": "2"}).issues)
        self.assertIn("FILLED_EXCEEDS_REQUESTED", binance({"executedQty": "3", "origQty": "2"}).issues)

    def test_native_units_are_not_multiplier_converted(self):
        result = binance({"symbol": "1000SHIBUSDT", "status": "FILLED", "executedQty": "12.5", "avgPrice": "0.02"})
        self.assertEqual(result.filled_quantity, Decimal("12.5"))
        self.assertEqual(result.average_fill_price, Decimal("0.02"))
