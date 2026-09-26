import unittest
from src.exchanges.api_error import ExchangeRequestError
from src.exchanges.coinone.coinone_rest import CoinoneRestError


class AuthorizationErrorTests(unittest.TestCase):
    def test_http_authentication_and_permission_failures(self):
        for exchange in ("upbit", "bithumb", "coinone", "korbit"):
            for status in (401, 403):
                self.assertTrue(ExchangeRequestError(exchange, "FORBIDDEN", status_code=status).authorization_failed)

    def test_coinone_http_200_permission_and_invalid_credentials(self):
        for code in ("4", "10", "11", "12", "21", "22", "23", "24", "27", "40", "123"):
            self.assertTrue(CoinoneRestError("coinone", code, status_code=200).authorization_failed)

    def test_symbolic_permission_codes_do_not_depend_on_status(self):
        for exchange, code in (("upbit", "out_of_scope"), ("upbit", "expired_access_key"),
                               ("bithumb", "out_of_scope"), ("bithumb", "NotAllowIP")):
            self.assertTrue(ExchangeRequestError(exchange, code).authorization_failed)

    def test_missing_orders_rate_limits_timeouts_and_bad_price_are_not_permissions(self):
        for exchange, code, status in (("coinone", "104", 200), ("coinone", "405", 200),
                ("coinone", "103", 200), ("coinone", "105", 200), ("upbit", "order_not_found", 404),
                ("bithumb", "RATE_LIMITED", 429), ("korbit", "UNAVAILABLE", 503)):
            self.assertFalse(ExchangeRequestError(exchange, code, status_code=status).authorization_failed)
