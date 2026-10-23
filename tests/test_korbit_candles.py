"""DigitalX official candle wire + exact wallet event-minute contract.

https://docs.digitalx.miraeasset.com/llms-full.txt#get-_v2_candles
Synthetic transport only; sanitized request/response evidence saved automatically.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

import requests
from urllib3.util import Timeout

from src.exchanges.api_error import ExchangeRequestError
from src.exchanges.global_spot_wallet import GlobalSpotWallet
from src.exchanges.korbit.korbit_rest import KorbitRest


MINUTE = 1792627200000
EVENT = "2026-10-22T00:00:23Z"


class KorbitCandleTests(TestCase):
    captures = []

    @classmethod
    def tearDownClass(cls):
        root = Path(__file__).parent / "results" / "native-wallet"
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        (root / f"digitalx-candles-{stamp}.json").write_text(json.dumps({
            "source": "https://docs.digitalx.miraeasset.com/llms-full.txt#get-_v2_candles",
            "mode": "synthetic only; network blocked", "requests": cls.captures}, indent=2), encoding="utf-8")

    def setUp(self):
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("NETWORK_FORBIDDEN")))
        self.enterContext(patch("src.exchanges.global_spot_wallet.time", Mock(time=Mock(return_value=MINUTE/1000+3600))))
        self.elapsed = 0
        self.session = Mock(spec=requests.Session)
        self.session.request.side_effect = self.http
        self.raw = KorbitRest("synthetic-key", "synthetic-secret", session=self.session,
                              timeout_seconds=Timeout(total=5, connect=2, read=3))
        self.raw.signed_params = Mock(side_effect=AssertionError("PUBLIC_REQUEST_MUST_NOT_SIGN"))
        self.wallet = GlobalSpotWallet("korbit", self.raw, clock=lambda: self.elapsed)
        self.payload = [{"timestamp": MINUTE, "open": "1399", "high": "1401", "low": "1398",
                         "close": "1400.12345678", "volume": "100"}]
        self.status, self.error = 200, None

    def http(self, method, endpoint, **kw):
        params = {k: v[0] for k, v in parse_qs(kw["params"]).items()}
        self.assertEqual(method, "GET")
        self.assertEqual(endpoint, "https://api.digitalx.miraeasset.com/v2/candles")
        self.assertEqual(kw["headers"], {})
        self.assertFalse(kw["allow_redirects"])
        self.assertFalse({"signature", "timestamp", "recvWindow"} & params.keys())
        timeout = kw["timeout"]
        if isinstance(timeout, Timeout):
            timeout = {"total": timeout.total, "connect": timeout.connect_timeout, "read": timeout.read_timeout}
        body = {"success": True, "data": self.payload} if self.status < 400 else {
            "success": False, "error": {"message": "RATE_LIMITED"}}
        self.captures.append({"exchange": "korbit", "api_category": "public", "api_name": "get_candles",
            "test": self._testMethodName, "http_method": method, "endpoint": endpoint,
            "request_parameters": params, "timeout": timeout, "captured_at": datetime.now(timezone.utc).isoformat(),
            "state": "FAILED" if self.error or self.status >= 400 else "HEALTHY",
            "response_or_error": type(self.error).__name__ if self.error else body})
        if self.error:
            raise self.error
        response = requests.Response()
        response.status_code = self.status
        response.headers["Retry-After"] = "2"
        response._content = json.dumps(body).encode()
        return response

    def test_wallet_exact_event_minute_one_public_request(self):
        self.assertEqual(self.wallet.historical_krw_price("usdt", EVENT), "1400.12345678")
        self.assertEqual(self.session.request.call_count, 1)
        params = self.captures[-1]["request_parameters"]
        self.assertEqual(params, {"symbol": "usdt_krw", "interval": "1", "start": str(MINUTE),
                                  "end": str(MINUTE+59999), "limit": "2"})
        self.raw.signed_params.assert_not_called()

    def test_timezone_offset_selects_same_utc_minute(self):
        self.assertEqual(self.wallet.historical_krw_price("USDT", "2026-10-22T09:00:59.999999+09:00"), "1400.12345678")
        self.assertEqual(self.captures[-1]["request_parameters"]["start"], str(MINUTE))

    def test_krw_identity_no_network(self):
        self.assertEqual(self.wallet.historical_krw_price("KRW", EVENT), "1")
        self.session.request.assert_not_called()

    def test_missing_other_minute_or_duplicate_never_falls_back(self):
        original = self.payload[0]
        for payload in ([], [{**original, "timestamp": MINUTE-60000}],
                        [{**original, "timestamp": MINUTE+60000}], [original, original]):
            self.payload = payload
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "UNAVAILABLE"):
                self.wallet.historical_krw_price("USDT", EVENT)
        self.assertEqual(self.session.request.call_count, 4)

    def test_order_of_rows_does_not_select_neighbor(self):
        self.payload.insert(0, {**self.payload[0], "timestamp": MINUTE-60000, "close": "9999"})
        self.assertEqual(self.wallet.historical_krw_price("USDT", EVENT), "1400.12345678")

    def test_invalid_response_times_values_fail_closed(self):
        original = self.payload[0]
        cases = [{**original, "timestamp": v} for v in (None, True, str(MINUTE), MINUTE+0.5)]
        cases += [{**original, "close": v} for v in ("0", "-1", "NaN", "Infinity", True, None)]
        for row in cases:
            self.payload = [row]
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.wallet.historical_krw_price("USDT", EVENT)
        for malformed in (None, {}, [None]):
            self.payload = malformed
            with self.assertRaises(ValueError):
                self.wallet.historical_krw_price("USDT", EVENT)

    def test_invalid_event_or_currency_before_network(self):
        for event in (None, "bad", "2026-10-22T00:00:00", "1960-01-01T00:00:00Z"):
            with self.assertRaisesRegex(ValueError, "TIMESTAMP_INVALID"):
                self.wallet.historical_krw_price("USDT", event)
        for currency in (None, "", "USDT/KRW", "a"*21):
            with self.assertRaisesRegex(ValueError, "CURRENCY_INVALID"):
                self.wallet.historical_krw_price(currency, EVENT)
        self.session.request.assert_not_called()

    def test_current_or_future_minute_is_not_final_price(self):
        with patch("src.exchanges.global_spot_wallet.time", Mock(time=Mock(return_value=MINUTE/1000+59.999))):
            with self.assertRaisesRegex(ValueError, "NOT_CLOSED"):
                self.wallet.historical_krw_price("USDT", EVENT)
        with self.assertRaisesRegex(ValueError, "NOT_CLOSED"):
            self.wallet.historical_krw_price("USDT", "2026-10-23T00:00:00Z")
        self.session.request.assert_not_called()

    def test_closed_boundary_is_allowed(self):
        with patch("src.exchanges.global_spot_wallet.time", Mock(time=Mock(return_value=MINUTE/1000+60))):
            self.assertEqual(self.wallet.historical_krw_price("USDT", EVENT), "1400.12345678")

    def test_deadline_exhausted_before_request_and_after_response(self):
        self.elapsed = 30
        with self.assertRaisesRegex(TimeoutError, "WALLET_DEADLINE_EXCEEDED"):
            self.wallet.historical_krw_price("USDT", EVENT)
        self.session.request.assert_not_called()
        self.elapsed = 29

        def late(*args, **kwargs):
            self.assertEqual(kwargs["timeout"], (0.5, 0.5))
            result = self.http(*args, **kwargs)
            self.elapsed = 31
            return result

        self.session.request.side_effect = late
        with self.assertRaisesRegex(TimeoutError, "WALLET_DEADLINE_EXCEEDED"):
            self.wallet.historical_krw_price("USDT", EVENT)
        self.assertEqual(self.session.request.call_count, 1)

    def test_raw_native_intervals_limit_bounds_and_public_timeout(self):
        self.raw._session = self.session
        for interval in ("1", "5", "15", "30", "60", "240", "1D", "1W"):
            self.assertEqual(self.raw.get_candles("USDT-KRW", interval=interval, start=MINUTE, end=MINUTE+60000, limit=200), self.payload)
            timeout = self.captures[-1]["timeout"]
            self.assertLessEqual(timeout["total"], 5)
            self.assertLessEqual(timeout["connect"], 2)
            self.assertLessEqual(timeout["read"], 3)

    def test_raw_invalid_params_do_not_send(self):
        for kwargs in ({"interval": "1m"}, {"interval": True}, {"limit": 0}, {"limit": 201},
                       {"limit": True}, {"limit": 2.5}, {"start": "123"}, {"start": -1},
                       {"end": True}, {"end": float("inf")}, {"start": 5, "end": 5}, {"start": 6, "end": 5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.raw.get_candles("usdt_krw", **kwargs)
        with self.assertRaisesRegex(ValueError, "INVALID_MARKET"):
            self.raw.get_candles("usdt")
        self.session.request.assert_not_called()

    def test_rate_limit_and_timeout_propagate_without_retries(self):
        self.status = 429
        with self.assertRaises(ExchangeRequestError) as caught:
            self.wallet.historical_krw_price("USDT", EVENT)
        self.assertEqual(caught.exception.code, "RATE_LIMITED")
        self.assertEqual(caught.exception.retry_after_seconds, 2)
        self.status, self.error = 200, requests.Timeout("synthetic")
        with self.assertRaises(ExchangeRequestError) as caught:
            self.wallet.historical_krw_price("USDT", EVENT)
        self.assertEqual(caught.exception.code, "TIMED_OUT")
        self.assertFalse(caught.exception.outcome_unknown)
        self.assertEqual(self.session.request.call_count, 2)
