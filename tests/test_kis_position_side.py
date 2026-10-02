"""Offline balance-side regressions; evidence saved by the caller test runner."""
import unittest
import test_spread_futures as fixtures
from src.exchanges.api_error import ExchangeRequestError


class PositionSideTests(unittest.TestCase):
    def account(self, rows, night=False):
        client = fixtures.FuturesTests().client({"rt_cd": "0", "output1": rows, "output2": {
            "dnca_cash": "100000", "ord_psbl_cash": "50000", "evlu_amt_smtl": "100000"}})
        return client.future_account("175V10", night=night)

    def row(self, qty="2", **side):
        return {"shtn_pdno": "175V10", "cblc_qty": qty, **side}

    def test_blank_code_does_not_hide_day_or_night_side_name(self):
        for night in (False, True):
            with self.subTest(night=night):
                result = self.account([self.row(sll_buy_dvsn_cd="  ", sll_buy_dvsn_name=" 매수 ")], night)
                self.assertEqual((result["long"], result["short"]), (2, 0))

    def test_zero_balance_without_side_is_not_a_position(self):
        result = self.account([self.row("0", sll_buy_dvsn_cd="", sll_buy_dvsn_name="")])
        self.assertEqual((result["long"], result["short"]), (0, 0))

    def test_recognized_codes_and_names_agree(self):
        result = self.account([self.row(sll_buy_dvsn_cd="01", sll_buy_dvsn_name="매도"),
                               self.row("3", sll_buy_dvsn_name="매수")], True)
        self.assertEqual((result["long"], result["short"]), (3, 2))

    def test_unknown_or_conflicting_nonzero_side_never_becomes_flat(self):
        for side in ({}, {"sll_buy_dvsn_name": "알수없음"}, {"sll_buy_dvsn_cd": "99"},
                     {"sll_buy_dvsn_cd": "01", "sll_buy_dvsn_name": "매수"}):
            with self.subTest(side=side), self.assertRaisesRegex(ExchangeRequestError, "INVALID_POSITION_SIDE"):
                self.account([self.row(**side)])

    def test_liquidatable_totals_by_side_day_and_night(self):
        for night in (False, True):
            result = self.account([
                self.row("2", sll_buy_dvsn_cd="02", lqd_psbl_qty="1"),
                self.row("3", sll_buy_dvsn_cd="02", lqd_psbl_qty="2"),
                self.row("1", sll_buy_dvsn_cd="01", lqd_psbl_qty="0"),
                {"shtn_pdno": "OTHER", "cblc_qty": "99", "lqd_psbl_qty": "99"},
            ], night)
            self.assertEqual((result["long"], result["longLiquidatable"]), (5, 3))
            self.assertEqual((result["short"], result["shortLiquidatable"]), (1, 0))

    def test_missing_or_invalid_liquidatable_is_unknown_not_zero(self):
        for value in (None, "", "bad", "NaN", "-1", "1.5"):
            with self.subTest(value=value):
                result = self.account([
                    self.row(sll_buy_dvsn_cd="02", lqd_psbl_qty=value),
                    self.row(sll_buy_dvsn_cd="02", lqd_psbl_qty="2"),
                ])
                self.assertEqual(result["long"], 4)
                self.assertIsNone(result["longLiquidatable"])
                self.assertEqual(result["shortLiquidatable"], 0)

    def test_no_positions_has_zero_liquidatable(self):
        result = self.account([])
        self.assertEqual((result["longLiquidatable"], result["shortLiquidatable"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
