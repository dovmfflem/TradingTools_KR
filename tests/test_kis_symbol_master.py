"""Offline public master contracts; app run-symbol-checks.py saves test evidence."""
from io import BytesIO
import unittest
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

import requests
from src.exchanges.kis import symbol_master as master


def archive_data(lines=None):
    lines = lines if lines is not None else [
        ("A75611", "KR4A756B0009", "미국달러 F 202611"),
        ("A75610", "KR4A756A0000", "미국달러 F 202610"),
        ("A76610", "KR4A766A0000", "유로 F 202610"),
        ("A75000", "KR4A75000000", "미국달러 SP 202610"),
    ]
    content = "\n".join("AA" + f"{code:<9}{standard:<12}{name:<32}" for code, standard, name in lines)
    out = BytesIO()
    with ZipFile(out, "w") as archive:
        archive.writestr("fo_com_code.mst", content.encode("cp949"))
    return out.getvalue()


class SymbolMasterTests(unittest.TestCase):
    def response(self):
        response = MagicMock(status_code=200)
        response.__enter__.return_value = response
        response.iter_content.return_value = [archive_data()]
        return response

    def test_fixed_width_filter_and_month_sort(self):
        rows = master.parse_dollar_futures(archive_data())
        self.assertEqual([s["short_code"] for s in rows], ["A75610", "A75611"])
        self.assertEqual(rows[0], {"month": "202610", "short_code": "A75610", "name": "미국달러 F 202610", "code": "KR4A756A0000"})

    def test_empty_and_malformed_archive_fail(self):
        for data in (b"not a zip", archive_data([])):
            with self.subTest(data=data[:10]), self.assertRaises(Exception):
                master.parse_dollar_futures(data)

    def test_size_limits(self):
        with patch.object(master, "MAX_BYTES", 10), self.assertRaisesRegex(ValueError, "TOO_LARGE"):
            master.parse_dollar_futures(archive_data())
        response = self.response()
        with patch.object(master.requests, "get", return_value=response), patch.object(master, "MAX_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "TOO_LARGE"):
                master.fetch_dollar_futures()
        response.__exit__.assert_called_once()

    def test_public_request_bounded_without_credentials(self):
        response = self.response()
        with patch.object(master.requests, "get", return_value=response) as get:
            self.assertEqual(len(master.fetch_dollar_futures()), 2)
        get.assert_called_once_with(master.MASTER_URL, timeout=(5, 10), stream=True, allow_redirects=False)
        response.raise_for_status.assert_called_once()
        response.__exit__.assert_called_once()

    def test_deadline_terminates_without_retry(self):
        response = self.response()
        with patch.object(master.requests, "get", return_value=response) as get, patch.object(master, "monotonic", side_effect=[0, 21]):
            with self.assertRaisesRegex(TimeoutError, "TIMED_OUT"):
                master.fetch_dollar_futures()
        get.assert_called_once()
        response.__exit__.assert_called_once()

    def test_http_error_and_redirect_do_not_retry(self):
        for status in (429, 503, 302):
            response = self.response()
            response.status_code = status
            if status >= 400:
                response.raise_for_status.side_effect = requests.HTTPError("fixture")
            with patch.object(master.requests, "get", return_value=response) as get, self.assertRaises((ValueError, requests.HTTPError)):
                master.fetch_dollar_futures()
            get.assert_called_once()
            response.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
