import unittest
from contextlib import contextmanager
from datetime import datetime, timezone

import requests

from src.exchanges.api_error import ExchangeRequestError
from src.exchanges.kis.kis_rest import KisRest


class MemoryCache:
    def __init__(self):
        self.values = {}
        self.in_lock = False

    @contextmanager
    def locked(self, identity):
        self.in_lock = True
        try:
            yield
        finally:
            self.in_lock = False

    def load(self, identity):
        assert self.in_lock
        return self.values.get(identity)

    def save(self, identity, value):
        assert self.in_lock
        self.values[identity] = dict(value)


class Response:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        pass


class KisRestTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 24, tzinfo=timezone.utc).timestamp()
        self.cache = MemoryCache()

    def client(self, responses=(), **overrides):
        options = dict(api_key="fixture-api", secret_key="fixture-secret", account="12345678-03",
                       token_cache=self.cache, session=Session(responses), clock=lambda: self.now)
        options.update(overrides)
        return KisRest(**options)

    @staticmethod
    def token_response():
        return Response({"access_token": "fixture-token", "access_token_token_expired": "2026-09-25 09:00:00"})

    def test_new_clients_reuse_persisted_token_with_kst_expiry(self):
        first = self.client([self.token_response()])
        self.assertEqual(first.get_access_token(), "fixture-token")
        self.assertEqual(self.cache.values[first.cache_id]["expires_at"], self.now + 86400)
        second = self.client()
        self.assertEqual(second.get_access_token(), "fixture-token")
        self.assertEqual(second.session.calls, [])

    def test_near_expiry_refreshes_once_with_no_redirects_and_bounded_timeout(self):
        client = self.client([self.token_response()])
        self.cache.values[client.cache_id] = {"access_token": "old", "expires_at": self.now + 300}
        client.get_access_token()
        client.get_access_token()
        self.assertEqual(len(client.session.calls), 1)
        method, url, kwargs = client.session.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/oauth2/tokenP"))
        self.assertEqual(kwargs["timeout"], (3, 5))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["json"]["grant_type"], "client_credentials")

    def test_timeout_does_not_retry_or_expose_secrets_and_persists_cooldown(self):
        client = self.client([requests.Timeout("fixture-api fixture-secret")])
        with self.assertRaisesRegex(ExchangeRequestError, "KIS_TIMED_OUT") as failure:
            client.get_access_token()
        self.assertNotIn("fixture-", str(failure.exception))
        with self.assertRaisesRegex(ExchangeRequestError, "TOKEN_REFRESH_COOLDOWN"):
            self.client().get_access_token()
        self.assertEqual(len(client.session.calls), 1)
        self.now += 61
        self.assertEqual(self.client([self.token_response()]).get_access_token(), "fixture-token")

    def test_rate_limit_respects_retry_after_across_clients(self):
        with self.assertRaises(ExchangeRequestError) as failure:
            self.client([Response({"secret": "never-print"}, 429, {"Retry-After": "120"})]).get_access_token()
        self.assertEqual(failure.exception.status_code, 429)
        self.now += 61
        with self.assertRaisesRegex(ExchangeRequestError, "TOKEN_REFRESH_COOLDOWN"):
            self.client().get_access_token()

    def test_malformed_token_and_expiry_fail_closed(self):
        for payload in ({}, {"access_token": "token", "access_token_token_expired": "2020-01-01 00:00:00"}):
            with self.subTest(payload=payload):
                self.cache = MemoryCache()
                with self.assertRaises(ExchangeRequestError):
                    self.client([Response(payload)]).get_access_token()

    def test_key_rotation_isolates_cache_but_account_change_reuses_token(self):
        first = self.client([self.token_response()])
        first.get_access_token()
        self.assertEqual(self.client(account="87654321-03").get_access_token(), "fixture-token")
        self.assertNotEqual(first.cache_id, self.client(secret_key="rotated").cache_id)
        self.assertNotIn("fixture", first.cache_id)

    def test_futures_read_uses_account_and_does_not_issue_token_again(self):
        client = self.client([self.token_response(), Response({"rt_cd": "0", "output1": [], "output2": {}})])
        client.get_access_token()
        client.get_future_balance()
        method, url, kwargs = client.session.calls[-1]
        self.assertEqual(method, "GET")
        self.assertTrue(url.endswith("/inquire-balance"))
        self.assertEqual(kwargs["params"]["CANO"], "12345678")
        self.assertEqual(kwargs["params"]["ACNT_PRDT_CD"], "03")
        self.assertEqual(kwargs["headers"]["tr_id"], "CTFO6118R")
        self.assertEqual(kwargs["headers"]["authorization"], "Bearer fixture-token")

    def test_account_error_never_exposes_response_or_blindly_reissues_token(self):
        client = self.client([self.token_response(), Response({"rt_cd": "1", "msg1": "fixture-token fixture-secret"})])
        with self.assertRaisesRegex(ExchangeRequestError, "KIS_ACCOUNT_QUERY_FAILED") as failure:
            client.get_future_balance()
        self.assertNotIn("fixture", str(failure.exception))
        self.assertEqual(len(client.session.calls), 2)

    def test_invalid_account_rejected_before_transport(self):
        with self.assertRaisesRegex(ValueError, "Account"):
            self.client(account="bad-account")


if __name__ == "__main__":
    unittest.main()
