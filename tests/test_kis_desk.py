"""Offline desk contracts. Evidence saved by parent tests/run_kis_desk_checks.py."""
import json
from io import BytesIO
from zipfile import ZipFile
from unittest import TestCase
from unittest.mock import Mock, patch

from src.exchanges.kis.symbol_master import parse_futures, fetch_futures, FUTURE_MASTERS
from src.exchanges.kis.futures_websocket import KisFuturesWebSocket
from test_kis_rest import KisRestTests, Response


def archive(kind, content):
    out = BytesIO()
    with ZipFile(out, "w") as z:
        z.writestr(FUTURE_MASTERS[kind] + ".mst", content.encode("cp949"))
    return out.getvalue()


class DeskMasterTests(TestCase):
    def test_index_stock_and_commodity_exclude_options_and_spreads(self):
        for kind in ("index", "stock"):
            content = "\n".join(["1|A01612|KR4A016C0004|F 202612| |0|1|2001|KOSPI200",
                                "2|B01612|KR4B016C0004|C 202612| |0|1|2001|KOSPI200",
                                "1|C01612|KR4C016C0004|SP 202612| |0|1|2001|KOSPI200"])
            rows = parse_futures(archive(kind, content), kind)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["underlying"], "KOSPI200")
            self.assertEqual(rows[0]["kind"], kind)
        row = "11" + f"{'A75610':<9}{'KR4A756A0000':<12}{'미국달러 F 202610':<32}"
        self.assertEqual(parse_futures(archive("commodity", row), "commodity")[0]["month"], "202610")

    def test_master_limits_and_malformed_rows(self):
        with self.assertRaises(ValueError):
            parse_futures(archive("index", "bad|row"), "index")
        with patch("src.exchanges.kis.symbol_master.MAX_BYTES", 5), self.assertRaises(ValueError):
            parse_futures(b"123456", "index")

    def test_index_mini_kosdaq_volatility_sector_and_krx_are_not_dropped(self):
        rows = parse_futures(archive("index", "\n".join(
            f"{kind}|A0{i}612|KR4A016C0004|{'미니' if kind == 'B' else ''}F 202612| |0|1|2001|KOSPI200"
            for i, kind in enumerate(["1", "3", "7", "9", "B", "H"]))), "index")
        self.assertEqual(len(rows), 6)
        self.assertEqual(len([row for row in rows if row["underlying"] == "미니 KOSPI200"]), 1)

    def test_shared_deadline_no_retry(self):
        with patch("src.exchanges.kis.symbol_master.monotonic", side_effect=[0, 21]), \
                patch("src.exchanges.kis.symbol_master.requests.get") as get, self.assertRaises(TimeoutError):
            fetch_futures()
        get.assert_not_called()


class DeskWsTests(TestCase):
    def test_product_channels_and_full_depth(self):
        for kind, night, tr, depth in [("index", False, "H0IFASP0", 5), ("commodity", False, "H0CFASP0", 5),
                                     ("stock", False, "H0ZFASP0", 10), ("stock", True, "H0MFASP0", 5)]:
            sock = Mock()
            ws = KisFuturesWebSocket(Mock(), ["A01612"], hts_id="", notices=False, product_kind=kind,
                                    night=night, connector=Mock(return_value=sock))
            ws.rest.get_ws_approval.return_value = "fixture"
            ws.connect()
            self.assertEqual(ws.subscriptions, {(tr, "A01612")})
            fields = ["0"] * (6 * depth + 8)
            fields[0:2] = ["A01612", "120000"]
            for i in range(depth):
                fields[2 + i], fields[2 + depth + i] = str(101 + i), str(100 - i)
                fields[2 + 4 * depth + i], fields[2 + 5 * depth + i] = str(10 + i), str(20 + i)
            event = ws.parse(f"0|{tr}|001|" + "^".join(fields))[0]
            self.assertEqual(event["asks"][0], {"price": "101", "quantity": "10"})
            self.assertEqual(len(event["bids"]), depth)
            ws.close(); sock.close.assert_called_once()

    def test_notices_still_require_id_by_default(self):
        with self.assertRaisesRegex(ValueError, "HTS_ID_REQUIRED"):
            KisFuturesWebSocket(Mock(), ["A01612"], hts_id="")


class DeskRestTests(KisRestTests):
    def test_contract_uses_product_market_code(self):
        for kind, night, code in [("index", False, "F"), ("stock", False, "JF"), ("commodity", False, "CF"), ("index", True, "CM")]:
            self.cache.values.clear()
            c = self.client([self.token_response(), Response({"rt_cd": "0", "output1": {"futs_last_tr_date": "20991231"}})])
            c.future_contract("A01612", night=night, product_kind=kind)
            self.assertEqual(c.session.calls[-1][2]["params"]["FID_COND_MRKT_DIV_CODE"], code)

    def test_order_price_and_fill_price_are_distinct(self):
        c = self.client([self.token_response(), Response({"rt_cd": "0", "output1": [
            {"pdno": "A01612", "odno": "1", "sll_buy_dvsn_cd": "02", "ord_qty": "3", "tot_ccld_qty": "1",
             "qty": "2", "ord_idx": "100", "avg_idx": "99", "ord_dt": "20261001", "ord_tmd": "123456", "nmpr_type_cd": "01"}]})])
        row = c.future_orders("A01612")[0]
        self.assertEqual((row["orderPrice"], row["price"], row["time"]), ("100", "99", "123456"))

    def test_night_history_uses_ord_idx4_and_named_market_type(self):
        c = self.client([self.token_response(), Response({"rt_cd": "0", "output1": [
            {"pdno": "A01612", "odno": "1", "sll_buy_dvsn_cd": "01", "ord_qty": "3", "tot_ccld_qty": "1",
             "qty": "2", "ord_idx4": "100.5", "avg_idx": "99.5", "ord_dt": "20261001", "ord_tmd": "203456", "nmpr_type_name": "시장가"}]})])
        row = c.future_orders("A01612", night=True)[0]
        self.assertEqual((row["orderPrice"], row["price"], row["orderType"]), ("100.5", "99.5", "market"))
