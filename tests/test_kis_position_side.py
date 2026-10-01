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


if __name__ == "__main__":
    unittest.main()
