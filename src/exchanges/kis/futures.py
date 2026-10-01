"""Normalized KIS domestic currency futures operations (no mutation retries).

Wire contract: koreainvestment/open-trading-api/examples_llm/domestic_futureoption.
All reads are bounded; incomplete pagination fails closed rather than returning a
misleading empty account. Application code supplies strategy and session policy.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re

from ..api_error import ExchangeRequestError


def amount(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError()
        return result
    except (ValueError, TypeError, InvalidOperation):
        raise ExchangeRequestError("kis", "INVALID_NUMERIC_RESPONSE") from None


class KisFuturesMixin:
    def _future_call(self, method, endpoint, tr_id, params, continuation=""):
        headers = {"authorization": f"Bearer {self.get_access_token()}", "appkey": self.api_key,
                   "appsecret": self.secret_key, "tr_id": tr_id, "custtype": "P",
                   "content-type": "application/json; charset=utf-8", "tr_cont": continuation}
        try:
            payload = self._request(method, "/uapi/domestic-futureoption/v1/" + endpoint,
                                    headers=headers, **({"json": params} if method == "POST" else {"params": params}))
        except ExchangeRequestError as error:
            if method == "POST":
                error.outcome_unknown = True
            raise
        if str(payload.get("rt_cd")) != "0":
            code = str(payload.get("msg_cd", ""))
            if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", code):
                code = "FUTURES_REQUEST_REJECTED"
            raise ExchangeRequestError("kis", code)
        return payload

    def _future_pages(self, read, category):
        """At most 10 pages/20 seconds plus one bounded request; never return partial data."""
        rows, summary, seen = [], None, set()
        fk = nk = continuation = ""
        started = self.clock()
        for _ in range(10):
            if self.clock() - started >= 20:
                break
            payload = read(fk, nk, continuation)
            page = payload.get("output1")
            if not isinstance(page, list):
                raise ExchangeRequestError("kis", "INVALID_" + category)
            rows.extend(page)
            if len(rows) > 1000:
                break
            if summary is None:
                summary = payload.get("output2")
            fk, nk = (str(payload.get(key, "")).strip() for key in ("ctx_area_fk200", "ctx_area_nk200"))
            header = getattr(self, "_last_tr_cont", "")
            if header in {"D", "E"} or (header not in {"F", "M"} and not nk):
                return rows, summary
            if not nk or (fk, nk) in seen:
                break
            seen.add((fk, nk))
            continuation = "N"
        raise ExchangeRequestError("kis", category + "_PAGINATED")

    @staticmethod
    def _future_symbol(symbol):
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{6,9}", symbol):
            raise ValueError("INVALID_FUTURES_SYMBOL")
        return symbol

    def future_quote(self, symbol, *, night=False):
        symbol = self._future_symbol(symbol)
        payload = self._future_call("GET", "quotations/inquire-asking-price", "FHMIF10010000", {
            "FID_COND_MRKT_DIV_CODE": "CM" if night else "CF", "FID_INPUT_ISCD": symbol})
        info, book = payload.get("output1", {}), payload.get("output2", {})
        bid, ask = amount(book.get("futs_bidp1")), amount(book.get("futs_askp1"))
        stamp = str(book.get("aspr_acpt_hour", ""))
        if not 0 < bid <= ask or not re.fullmatch(r"\d{6}", stamp):
            raise ExchangeRequestError("kis", "INVALID_QUOTE")
        return {"bid": str(bid), "ask": str(ask), "time": stamp,
                "name": str(info.get("hts_kor_isnm", "")), "symbol": symbol}

    def future_orderbook(self, symbol, *, night=False, product_kind="commodity"):
        """REST five-level book. No WebSocket approval or mutation is performed."""
        if product_kind not in {"commodity", "index", "stock"}:
            raise ValueError("INVALID_FUTURES_KIND")
        symbol = self._future_symbol(symbol)
        payload = self._future_call("GET", "quotations/inquire-asking-price", "FHMIF10010000", {
            "FID_COND_MRKT_DIV_CODE": "CM" if night else {"commodity": "CF", "index": "F", "stock": "JF"}[product_kind],
            "FID_INPUT_ISCD": symbol})
        book = payload.get("output2")
        if not isinstance(book, dict):
            raise ExchangeRequestError("kis", "INVALID_ORDERBOOK")
        stamp = str(book.get("aspr_acpt_hour", ""))
        if not re.fullmatch(r"\d{6}", stamp):
            raise ExchangeRequestError("kis", "INVALID_ORDERBOOK_TIME")
        def levels(side):
            result = []
            for i in range(1, 6):
                price = amount(book.get(f"futs_{side}p{i}"))
                qty = amount(book.get(f"{side}p_rsqn{i}"))
                if price < 0 or qty < 0 or qty != int(qty) or (price == 0 and qty != 0):
                    raise ExchangeRequestError("kis", "INVALID_ORDERBOOK_LEVEL")
                if price > 0:
                    result.append({"price": str(price), "quantity": str(qty)})
            return result
        return {"symbol": symbol, "asks": levels("ask"), "bids": levels("bid"), "time": stamp}

    def future_contract(self, symbol, *, night=False, product_kind="commodity"):
        if product_kind not in {"commodity", "index", "stock"}:
            raise ValueError("INVALID_FUTURES_KIND")
        payload = self._future_call("GET", "quotations/inquire-price", "FHMIF10000000", {
            "FID_COND_MRKT_DIV_CODE": "CM" if night else {"commodity": "CF", "index": "F", "stock": "JF"}[product_kind],
            "FID_INPUT_ISCD": self._future_symbol(symbol)})
        row = payload.get("output1", {})
        expiry = str(row.get("futs_last_tr_date", ""))
        if not re.fullmatch(r"\d{8}", expiry):
            raise ExchangeRequestError("kis", "CONTRACT_EXPIRY_MISSING")
        try:
            datetime.strptime(expiry, "%Y%m%d")
        except ValueError:
            raise ExchangeRequestError("kis", "INVALID_CONTRACT_EXPIRY") from None
        return {"name": str(row.get("hts_kor_isnm", "")), "expiry": expiry, "symbol": symbol}

    def future_capacity(self, symbol, side, *, night=False, price=None):
        if side not in {"buy", "sell"}:
            raise ValueError("INVALID_SIDE")
        payload = self._future_call("GET", "trading/" + ("inquire-psbl-ngt-order" if night else "inquire-psbl-order"),
            "STTN5105R" if night else "TTTO5105R", {"CANO": self.cano, "ACNT_PRDT_CD": self.product_code,
            "PDNO": self._future_symbol(symbol), "SLL_BUY_DVSN_CD": "01" if side == "sell" else "02",
            "UNIT_PRICE": self._future_price(price), "ORD_DVSN_CD": "02" if price is None else "01"})
        qty = amount(payload.get("output", {}).get("tot_psbl_qty"))
        if qty < 0 or qty != int(qty):
            raise ExchangeRequestError("kis", "INVALID_CAPACITY")
        return int(qty)

    def future_order(self, symbol, side, *, night=False, quantity=1, price=None):
        if side not in {"buy", "sell"}:
            raise ValueError("INVALID_SIDE")
        if type(quantity) is not int or not 1 <= quantity <= 10000:
            raise ValueError("INVALID_CONTRACT_QUANTITY")
        payload = self._future_call("POST", "trading/order", "STTN1101U" if night else "TTTO1101U", {
            "ORD_PRCS_DVSN_CD": "02", "CANO": self.cano, "ACNT_PRDT_CD": self.product_code,
            "SLL_BUY_DVSN_CD": "01" if side == "sell" else "02", "SHTN_PDNO": self._future_symbol(symbol),
            "ORD_QTY": str(quantity), "UNIT_PRICE": self._future_price(price),
            "NMPR_TYPE_CD": "02" if price is None else "01", "KRX_NMPR_CNDT_CD": "0",
            "ORD_DVSN_CD": "02" if price is None else "01", "CTAC_TLNO": "", "FUOP_ITEM_DVSN_CD": ""})
        output = payload.get("output", {})
        if not isinstance(output, dict):
            raise ExchangeRequestError("kis", "ORDER_RESULT_UNKNOWN", outcome_unknown=True)
        order_id = str(output.get("ODNO") or output.get("odno") or "")
        if not re.fullmatch(r"\d{1,20}", order_id):
            raise ExchangeRequestError("kis", "ORDER_RESULT_UNKNOWN", outcome_unknown=True)
        return order_id

    @staticmethod
    def _future_price(price):
        if price is None:
            return "0"
        value = amount(price)
        if value <= 0:
            raise ValueError("INVALID_LIMIT_PRICE")
        return format(value, "f")

    def future_cancel(self, order_id, *, night=False, quantity=None):
        """Cancel all remaining contracts, or an explicit positive partial quantity.

        Acknowledgement is NOT terminal confirmation. Reconcile via WS/history.
        """
        return self._future_change(order_id, night=night, quantity=quantity, cancel=True)

    def future_modify(self, order_id, price, *, night=False, quantity=None):
        """Amend a limit order; returned ID is the amendment request order ID."""
        if price is None:
            raise ValueError("LIMIT_PRICE_REQUIRED")
        return self._future_change(order_id, night=night, quantity=quantity, price=price)

    def _future_change(self, order_id, *, night, quantity, cancel=False, price=None):
        if not isinstance(order_id, str) or not re.fullmatch(r"[0-9]{1,20}", order_id):
            raise ValueError("INVALID_ORDER_ID")
        if quantity is not None and (type(quantity) is not int or not 1 <= quantity <= 10000):
            raise ValueError("INVALID_CONTRACT_QUANTITY")
        payload = self._future_call("POST", "trading/order-rvsecncl", "TTTN1103U" if night else "TTTO1103U", {
            "ORD_PRCS_DVSN_CD": "02", "CANO": self.cano, "ACNT_PRDT_CD": self.product_code,
            "RVSE_CNCL_DVSN_CD": "02" if cancel else "01", "ORGN_ODNO": order_id,
            "ORD_QTY": "0" if quantity is None else str(quantity),
            "UNIT_PRICE": "0" if cancel else self._future_price(price),
            "NMPR_TYPE_CD": "01", "KRX_NMPR_CNDT_CD": "0",
            "RMN_QTY_YN": "Y" if quantity is None else "N", "ORD_DVSN_CD": "01", "FUOP_ITEM_DVSN_CD": ""})
        output = payload.get("output", {})
        if not isinstance(output, dict):
            raise ExchangeRequestError("kis", "ORDER_RESULT_UNKNOWN", outcome_unknown=True)
        order_id = str(output.get("ODNO") or output.get("odno") or "")
        if not re.fullmatch(r"[0-9]{1,20}", order_id):
            raise ExchangeRequestError("kis", "ORDER_RESULT_UNKNOWN", outcome_unknown=True)
        return order_id

    def future_orders(self, symbol, *, night=False, since=None):
        now = datetime.fromtimestamp(self.clock(), timezone(timedelta(hours=9)))
        start = since or (now - timedelta(days=1)).strftime("%Y%m%d")
        if not re.fullmatch(r"\d{8}", start):
            raise ValueError("INVALID_ORDER_DATE")
        params = {"CANO": self.cano, "ACNT_PRDT_CD": self.product_code, "STRT_ORD_DT": start,
                  "END_ORD_DT": (now + timedelta(days=1 if night else 0)).strftime("%Y%m%d"),
                  "SLL_BUY_DVSN_CD": "00", "CCLD_NCCS_DVSN": "00", "SORT_SQN": "DS",
                  "PDNO": self._future_symbol(symbol), "STRT_ODNO": "", "MKET_ID_CD": "",
                  "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""}
        if night:
            params.update(FUOP_DVSN_CD="", SCRN_DVSN="02")
        def read(fk, nk, continuation):
            return self._future_call("GET", "trading/" + ("inquire-ngt-ccnl" if night else "inquire-ccnl"),
                "STTN5201R" if night else "TTTO5201R", {**params, "CTX_AREA_FK200": fk, "CTX_AREA_NK200": nk}, continuation)
        rows, _ = self._future_pages(read, "ORDER_HISTORY")
        result = []
        for row in rows:
            if str(row.get("shtn_pdno") or row.get("pdno", "")).strip() != symbol:
                raise ExchangeRequestError("kis", "ORDER_SYMBOL_MISMATCH")
            qty, filled, remaining = (amount(row.get(key)) for key in ("ord_qty", "tot_ccld_qty", "qty"))
            if not 0 <= filled <= qty or not 0 <= remaining <= qty - filled or any(v != int(v) for v in (qty, filled, remaining)):
                raise ExchangeRequestError("kis", "INVALID_ORDER_HISTORY")
            result.append({"id": str(row["odno"]), "symbol": symbol,
                "side": {"01": "sell", "02": "buy"}[row["sll_buy_dvsn_cd"]], "quantity": str(qty),
                "filled": str(filled), "remaining": str(remaining), "date": str(row.get("ord_dt", "")),
                "price": str(amount(row.get("avg_idx", "0"))),
                "orderPrice": str(amount(row.get("ord_idx4" if night else "ord_idx", "0"))),
                "orderType": "market" if str(row.get("nmpr_type_cd")) == "02" or "시장가" in str(row.get("nmpr_type_name", "")) else "limit",
                "time": str(row.get("ord_tmd", ""))})
        return result

    def future_account(self, symbol, *, night=False):
        positions, summary = self._future_pages(lambda fk, nk, continuation: self.get_future_balance(
            night=night, context_fk=fk, context_nk=nk, continuation=continuation), "ACCOUNT")
        if not isinstance(summary, dict):
            raise ExchangeRequestError("kis", "INVALID_ACCOUNT_SUMMARY")
        def optional(*keys):
            for key in keys:
                value = summary.get(key)
                if value is not None and str(value).strip():
                    return str(amount(value))
            return None
        long_qty = short_qty = Decimal(0)
        for row in positions:
            if str(row.get("shtn_pdno") or row.get("pdno", "")).strip() != symbol:
                continue
            qty = amount(row.get("cblc_qty"))
            if qty < 0 or qty != int(qty):
                raise ExchangeRequestError("kis", "INVALID_POSITION")
            # A flat row carries no exposure, even when its side is blank.
            if qty == 0:
                continue
            # Normalize each field BEFORE fallback: padded blank codes are truthy
            # and used to hide the valid day/night Korean side name.
            code = str(row.get("sll_buy_dvsn_cd") or "").strip()
            name = str(row.get("sll_buy_dvsn_name") or "").strip()
            code_side = {"01": "sell", "02": "buy"}.get(code)
            name_side = {"매도": "sell", "매수": "buy"}.get(name)
            if (code and not code_side) or (code_side and name_side and code_side != name_side):
                raise ExchangeRequestError("kis", "INVALID_POSITION_SIDE")
            side = code_side or name_side
            if side == "sell":
                short_qty += qty
            elif side == "buy":
                long_qty += qty
            else:
                raise ExchangeRequestError("kis", "INVALID_POSITION_SIDE")
        return {"short": int(short_qty), "long": int(long_qty),
                "deposit": str(amount(summary.get("dnca_cash"))),
                "buyingPower": str(amount(summary.get("ord_psbl_cash"))),
                "evaluation": str(amount(summary.get("evlu_amt_smtl"))),
                "margin": optional("mgna_tota"),
                "cashMargin": optional("cash_mgna"),
                "estimatedAssets": optional("prsm_dpast_amt", "prsm_dpast"),
                "evaluationPnl": optional("evlu_pfls_amt_smtl"),
                "maintenanceMargin": optional("mmga_tot_amt")}
