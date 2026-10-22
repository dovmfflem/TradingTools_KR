"""Official-shaped synthetic transfer contracts. Socket connections are forbidden."""
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import requests
from requests.adapters import HTTPAdapter

from src.exchanges.api_error import ExchangeRequestError
from src.exchanges.bithumb.bithumb_rest import BithumbRest
from src.exchanges.bithumb.bithumb_wallet import (
    BithumbSpotWallet, withdrawal_fee, withdrawal_fee_upper_bound, withdrawal_beneficiary,
)


class BithumbWalletTests(unittest.TestCase):
    evidence = []

    @classmethod
    def tearDownClass(cls):
        root = Path(__file__).parent / "results" / "arbitrage-spot"
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        (root / f"wallet-http-{stamp}.json").write_text(json.dumps({
            "transport": "synthetic HTTP only", "requests": cls.evidence,
        }, indent=2), encoding="utf-8")

    def setUp(self):
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.raw = BithumbRest("synthetic-key", "synthetic-secret-32-characters-long", timeout_seconds=3)
        self.addCleanup(self.raw.close)
        self.wallet = BithumbSpotWallet(self.raw)
        self.payload = self.transfer()
        self.status = 200
        self.transport_error = None
        self.raw._session.request = Mock(side_effect=self.http)

    @staticmethod
    def transfer(state="processing"):
        return {"type": "withdraw", "uuid": "synthetic-uuid", "currency": "USDT",
                "net_type": None, "state": state, "amount": "10.00000000", "fee": "1",
                "txid": None, "created_at": "2026-10-22T00:00:00+09:00", "done_at": None,
                "transaction_type": None}

    def http(self, **request):
        self.evidence.append({
            "exchange": "bithumb", "api_category": "public" if request["url"].endswith("/v1/ticker") else "private",
            "api_name": self._testMethodName,
            "http_method": request["method"], "endpoint": request["url"].split(".com", 1)[-1],
            "request_parameters": {"params": request.get("params"), "json": request.get("json")},
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "state": "FAILED" if self.transport_error or self.status >= 300 else "HEALTHY",
            "response_or_error": type(self.transport_error).__name__ if self.transport_error else self.payload,
            "status_code": None if self.transport_error else self.status,
        })
        self.assertFalse(request["allow_redirects"])
        self.assertEqual(request["timeout"], self.raw.timeout_seconds)
        if self.transport_error:
            raise self.transport_error
        response = requests.Response()
        response.status_code = self.status
        response.headers["Retry-After"] = "2"
        response._content = json.dumps(self.payload).encode()
        return response

    def submit(self, **kwargs):
        values = dict(currency="USDT", net_type="SYNTHETIC_NATIVE_NETWORK", amount="10.00000000",
                      address="synthetic-registered-address", exchange_name="Binance")
        values.update(kwargs)
        return self.wallet.create_coin_withdraw(**values)

    def test_import_lifecycle_and_explicit_surface(self):
        from src.exchanges.arbitrage_spot import BithumbSpotWallet as Export
        self.assertIs(Export, BithumbSpotWallet)
        self.wallet.timeout_seconds = 1.25
        self.assertEqual(self.raw.timeout_seconds, 1.25)
        self.assertEqual(self.wallet.api_url, "https://api.bithumb.com")
        for value in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                self.wallet.timeout_seconds = value
        for name in ("_request", "create_krw_withdraw", "register_address", "verify_travel_rule"):
            self.assertFalse(hasattr(self.wallet, name))
        self.assertFalse(self.wallet.capabilities["withdrawal_idempotency"])
        with patch.object(self.raw, "close") as close:
            self.wallet.close()
            close.assert_called_once_with()

    def test_chance_retains_bridge_fields_and_nullable_rate(self):
        self.payload = {"currency": {"code": "USDT", "wallet_state": "working", "withdraw_fee": None,
                                     "withdraw_rate": "0.01", "withdraw_fee_min": "1", "withdraw_fee_max": "5"},
                        "account": {"currency": "USDT", "balance": "100", "locked": "0"},
                        "withdraw_limit": {"can_withdraw": True, "remaining_daily": "99", "minimum": "2"},
                        "member_level": {"locked": False}}
        result = self.wallet.get_withdraw_chance(currency="USDT", net_type="SYNTHETIC_NATIVE_NETWORK")
        self.assertEqual(result, self.payload)
        self.assertIsNone(result["currency"]["withdraw_fee"])
        self.assertEqual(self.raw._session.request.call_args.kwargs["params"],
                         {"currency": "USDT", "net_type": "SYNTHETIC_NATIVE_NETWORK"})

    def test_registered_addresses_filter_locally_and_keep_owner_metadata(self):
        row = {"currency": "USDT", "net_type": "SYNTHETIC_NATIVE_NETWORK", "network_name": "Synthetic",
               "withdraw_address": "synthetic-address", "secondary_address": None, "exchange_name": "Binance",
               "owner_type": "personal", "owner_en_name": "SYNTHETIC NAME", "owner_ko_name": None}
        self.payload = [row, {**row, "net_type": "OTHER_NETWORK"}]
        self.assertEqual(self.wallet.list_withdraw_addresses(currency="USDT", net_type=row["net_type"]), [row])
        self.assertFalse(self.raw._session.request.call_args.kwargs.get("params"))

    def test_get_withdraw_uuid_alias_and_required_currency(self):
        for kwargs in ({"uuid_value": "synthetic-uuid"}, {"uuid": "synthetic-uuid"}, {"txid": "synthetic-txid"}):
            result = self.wallet.get_withdraw(currency="USDT", **kwargs)
            self.assertEqual(result["state"], "PROCESSING")
            self.assertEqual(result["raw_state"], "processing")
            self.assertIsNone(result["net_type"])
        self.assertEqual(self.wallet.get_withdraw("synthetic-uuid", currency="USDT")["uuid"], "synthetic-uuid")
        with self.assertRaises(TypeError):
            self.wallet.get_withdraw(uuid_value="synthetic-uuid")
        with self.assertRaises(ValueError):
            self.wallet.get_withdraw("a", currency="USDT", uuid="b")
        with self.assertRaises(ValueError):
            self.raw.get_withdraw(uuid_value="synthetic-uuid")

    def test_history_aliases_and_default_list_filters(self):
        for name in ("list_withdraws", "list_coin_withdraws", "list_deposits", "list_coin_deposits"):
            with self.subTest(name=name):
                self.payload = [self.transfer("DONE")]
                result = getattr(self.wallet, name)(limit=100, order_by="desc")
                self.assertEqual(result[0]["uuid"], "synthetic-uuid")
                params = self.raw._session.request.call_args.kwargs["params"]
                self.assertEqual(params, {"limit": 100, "order_by": "desc"})

    def test_all_four_raw_histories_reject_invalid_payloads(self):
        for name in ("list_coin_deposits", "list_krw_deposits", "list_coin_withdraws", "list_krw_withdraws"):
            for payload in (None, {}, {"data": []}, "bad", [None], ["bad"]):
                with self.subTest(name=name, payload=payload):
                    self.payload = payload
                    with self.assertRaises(ExchangeRequestError) as error:
                        getattr(self.raw, name)(limit=1)
                    self.assertEqual(error.exception.exchange, "bithumb")
                    self.assertEqual(error.exception.code, "INVALID_RESPONSE")
            self.payload = []
            self.assertEqual(getattr(self.raw, name)(limit=1), [])

    def test_history_bounds_rejected_without_requests(self):
        for name in ("list_coin_deposits", "list_krw_deposits", "list_coin_withdraws", "list_krw_withdraws"):
            for kwargs in ({"limit": 101}, {"limit": 0}, {"limit": True}, {"page": 0}, {"order_by": "bad"}):
                with self.assertRaises(ValueError):
                    getattr(self.raw, name)(**kwargs)
        self.raw._session.request.assert_not_called()

    def test_deposit_state_normalization_and_native_filter(self):
        self.payload = [self.transfer("DEPOSIT_ACCEPTED"), self.transfer("REQUESTED_PROCESSING"),
                        self.transfer("REFUNDED_ACCEPTED")]
        result = self.wallet.list_deposits(currency="USDT", state="ACCEPTED", txids=["synthetic-txid"])
        self.assertEqual([r["state"] for r in result], ["ACCEPTED", "REQUESTED_PROCESSING", "REFUNDED"])
        self.assertEqual(result[0]["raw_state"], "DEPOSIT_ACCEPTED")
        self.assertEqual(self.raw._session.request.call_args.kwargs["params"],
                         {"currency": "USDT", "state": "DEPOSIT_ACCEPTED", "txids[]": ["synthetic-txid"]})
        self.payload = self.transfer("DEPOSIT_ACCEPTED")
        self.assertEqual(self.wallet.get_deposit(txid="synthetic-txid", currency="USDT")["state"], "ACCEPTED")

    def test_deposit_address_generation_lookup_and_list(self):
        self.payload = {"currency": "USDT", "net_type": "SYNTHETIC_NATIVE_NETWORK",
                        "deposit_address": "synthetic-address", "secondary_address": None}
        for name in ("get_deposit_address", "create_deposit_address"):
            self.assertEqual(getattr(self.wallet, name)(currency="USDT", net_type="SYNTHETIC_NATIVE_NETWORK"), self.payload)
        self.payload = [self.payload]
        self.assertEqual(self.wallet.list_deposit_addresses(currency="USDT"), self.payload)
        self.assertFalse(self.raw._session.request.call_args.kwargs.get("params"))

    def test_required_network_is_never_inferred(self):
        for name in ("get_deposit_address", "create_deposit_address", "get_withdraw_chance"):
            with self.assertRaises(ValueError):
                getattr(self.raw, name)(currency="USDT")
        self.raw._session.request.assert_not_called()

    def test_id_connect_submission_keeps_decimal_and_omits_legacy_flag(self):
        result = self.submit(transaction_type="default")
        body = self.raw._session.request.call_args.kwargs["json"]
        self.assertEqual(body, {"currency": "USDT", "net_type": "SYNTHETIC_NATIVE_NETWORK", "amount": "10.00000000",
                                "address": "synthetic-registered-address", "exchange_name": "Binance"})
        self.assertEqual(result["state"], "PROCESSING")
        self.assertEqual(result["fee"], "1")
        self.raw._session.request.assert_called_once()

    def test_code_personal_and_corporation_fields(self):
        fields = dict(receiver_type="personal", receiver_ko_name="synthetic-ko-name", receiver_en_name="SYNTHETIC NAME")
        self.submit(**fields, secondary_address="synthetic-tag")
        self.assertEqual(self.raw._session.request.call_args.kwargs["json"]["secondary_address"], "synthetic-tag")
        fields.update(receiver_type="corporation", receiver_corp_ko_name="synthetic-ko-corp", receiver_corp_en_name="SYNTHETIC CORP")
        self.submit(**fields)
        body = self.raw._session.request.call_args.kwargs["json"]
        for key, value in fields.items():
            self.assertEqual(body[key], value)

    def test_invalid_submit_rejected_without_http(self):
        cases = ({"amount": "NaN"}, {"amount": "Infinity"}, {"amount": "0"}, {"amount": "-1"},
                 {"transaction_type": "internal"}, {"receiver_type": "person"}, {"receiver_type": "personal"},
                 {"receiver_en_name": "SYNTHETIC"}, {"address": ""}, {"net_type": ""},
                 {"receiver_type": "corporation", "receiver_ko_name": "KO", "receiver_en_name": "EN"})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.submit(**kwargs)
        self.raw._session.request.assert_not_called()

    def test_mutation_timeout_and_invalid_ack_are_uncertain_never_retried(self):
        self.transport_error = requests.Timeout("synthetic timeout")
        with self.assertRaises(ExchangeRequestError) as error:
            self.submit()
        self.assertTrue(error.exception.outcome_unknown)
        self.assertEqual(error.exception.exchange, "bithumb")
        self.raw._session.request.assert_called_once()
        self.transport_error = None
        for payload in (None, [], {}, {"status": "ok"}):
            self.payload = payload
            self.raw._session.request.reset_mock()
            with self.assertRaises(ExchangeRequestError) as error:
                self.submit()
            self.assertTrue(error.exception.outcome_unknown)
            self.raw._session.request.assert_called_once()

    def test_retrying_transport_rejected(self):
        self.raw._session.mount("https://", HTTPAdapter(max_retries=1))
        with self.assertRaises(ValueError):
            self.submit()
        self.raw._session.request.assert_not_called()

    def test_consent_and_rate_limits_propagate_without_retry(self):
        for status, code in ((422, "travel_rule_consent_required"), (429, "too_many_requests"), (503, "server_error")):
            self.status = status
            self.payload = {"error": {"name": code, "message": "synthetic"}}
            self.raw._session.request.reset_mock()
            with self.assertRaises(ExchangeRequestError) as error:
                self.submit()
            self.assertEqual(error.exception.exchange, "bithumb")
            if status == 422:
                self.assertEqual(error.exception.code, code)
            self.assertEqual(error.exception.retry_after_seconds, 2)
            self.raw._session.request.assert_called_once()

    def test_fee_fixed_rate_bounds_unknown_and_ambiguity(self):
        self.assertEqual(withdrawal_fee({"currency": {"withdraw_fee": "0"}}, "10"), Decimal("0"))
        chance = {"currency": {"withdraw_fee": None, "withdraw_rate": "0.01", "withdraw_fee_min": "1", "withdraw_fee_max": "5"}}
        for amount, expected in (("10", "1"), ("200", "2"), ("1000", "5")):
            self.assertEqual(withdrawal_fee(chance, amount), Decimal(expected))
        for fields in ({"withdraw_fee": None}, {"withdraw_fee": "NaN"}, {"withdraw_fee": "-1"},
                       {"withdraw_fee": "1", "withdraw_rate": "0.01"}):
            with self.assertRaises(ValueError):
                withdrawal_fee({"currency": fields}, "10")

    def test_beneficiary_maps_only_verified_registry_fields(self):
        row = {"exchange_name": "Binance", "owner_type": "personal",
               "owner_ko_name": "synthetic-ko-name", "owner_en_name": "SYNTHETIC NAME",
               "receiver_en_name": "UNTRUSTED ALIAS", "amount": "999", "address": "not-a-field"}
        expected = {"exchange_name": "Binance", "receiver_type": "personal",
                    "receiver_ko_name": "synthetic-ko-name", "receiver_en_name": "SYNTHETIC NAME"}
        self.assertEqual(withdrawal_beneficiary(row), expected)
        self.assertEqual(withdrawal_beneficiary(row, travel_rule_type="code"), expected)
        self.assertEqual(withdrawal_beneficiary(row, travel_rule_type="id_connect"), {"exchange_name": "Binance"})
        self.assertEqual(withdrawal_beneficiary(row, travel_rule_type="whitelist"), {"exchange_name": "Binance"})
        self.assertEqual(withdrawal_beneficiary(row, travel_rule_type="internal"), {})
        row.update(owner_type="corporation", owner_corp_ko_name="synthetic-ko-corp", owner_corp_en_name="SYNTHETIC CORP")
        self.assertEqual(withdrawal_beneficiary(row, travel_rule_type="code")["receiver_corp_en_name"], "SYNTHETIC CORP")
        with self.assertRaises(ValueError):
            withdrawal_beneficiary(row, travel_rule_type="id_connect")

    def test_fee_upper_bound_is_explicit_budget_rounding_not_actual_fee(self):
        chance = {"currency": {"withdraw_fee": None, "withdraw_rate": "0.0012345"}, "withdraw_limit": {"fixed": 4}}
        self.assertEqual(withdrawal_fee(chance, "1"), Decimal("0.0012345"))
        self.assertEqual(withdrawal_fee_upper_bound(chance, "1"), Decimal("0.0013"))
        chance["currency"].update(withdraw_fee_max="0.00121")
        self.assertEqual(withdrawal_fee_upper_bound(chance, "1"), Decimal("0.0013"))
        chance["currency"] = {"withdraw_fee": "1.0000"}
        self.assertEqual(withdrawal_fee_upper_bound(chance, "10"), Decimal("1.0000"))
        chance["currency"] = {"withdraw_fee": "0"}
        self.assertEqual(withdrawal_fee_upper_bound(chance, "10"), Decimal("0"))

    def test_fee_upper_bound_precision_validation_and_context_independence(self):
        chance = {"currency": {"withdraw_rate": "0.123456789"}, "withdraw_limit": {"fixed": 8}}
        with localcontext() as context:
            context.prec = 3
            self.assertEqual(withdrawal_fee_upper_bound(chance, "1.23456789"), Decimal("0.15241579"))
        for fixed in (None, True, -1, 19, "8", 8.0):
            chance["withdraw_limit"]["fixed"] = fixed
            with self.assertRaises(ValueError):
                withdrawal_fee_upper_bound(chance, "10")

    def test_krw_price_uses_one_public_ticker_and_native_mapping(self):
        self.payload = [{"market": "KRW-USDT", "trade_price": "1450.1234", "timestamp": 100000,
                         "trade_timestamp": 1}]
        with patch("src.exchanges.bithumb.bithumb_wallet.time.time", return_value=100):
            self.assertEqual(self.wallet.get_krw_price("usdt"), Decimal("1450.1234"))
        request = self.raw._session.request.call_args.kwargs
        self.assertEqual(request["params"], {"markets": "KRW-USDT"})
        self.assertNotIn("Authorization", request["headers"])
        self.raw._session.request.assert_called_once()
        self.assertEqual(self.wallet.get_krw_price("KRW"), Decimal("1"))
        self.raw._session.request.assert_called_once()

    def test_krw_price_fails_closed_on_wrong_missing_invalid_or_stale_data(self):
        valid = {"market": "KRW-USDT", "trade_price": "1450", "timestamp": 100000}
        cases = ([], [valid, valid], [{**valid, "market": "BTC-USDT"}],
                 [{**valid, "trade_price": None}], [{**valid, "trade_price": "NaN"}],
                 [{**valid, "trade_price": "Infinity"}], [{**valid, "trade_price": "0"}],
                 [{**valid, "timestamp": None}], [{**valid, "timestamp": 94000}],
                 [{**valid, "timestamp": 106000}])
        with patch("src.exchanges.bithumb.bithumb_wallet.time.time", return_value=100):
            for payload in cases:
                self.payload = payload
                with self.subTest(payload=payload), self.assertRaises(ExchangeRequestError) as error:
                    self.wallet.get_krw_price("USDT")
                self.assertEqual(error.exception.exchange, "bithumb")
        self.raw._session.request.reset_mock()
        for currency in ("", "KRW-USDT", "usdt/krw", None):
            with self.assertRaises(ValueError):
                self.wallet.get_krw_price(currency)
        self.raw._session.request.assert_not_called()

    def test_incomplete_beneficiary_never_fabricates_names(self):
        row = {"exchange_name": "Binance", "owner_type": "personal", "owner_ko_name": None, "owner_en_name": "SYNTHETIC NAME"}
        result = withdrawal_beneficiary(row)
        self.assertNotIn("receiver_ko_name", result)
        with self.assertRaises(ValueError):
            self.submit(**result)
        with self.assertRaisesRegex(ValueError, "receiver_ko_name"):
            withdrawal_beneficiary(row, travel_rule_type="code")
        for invalid in ({}, {"exchange_name": " "}, {"exchange_name": "Binance", "owner_type": "unknown"}):
            with self.assertRaises(ValueError):
                withdrawal_beneficiary(invalid)
        with self.assertRaises(ValueError):
            withdrawal_beneficiary(row, travel_rule_type="guess")
        self.raw._session.request.assert_not_called()

    def test_wallet_status_and_accounts_no_silent_empty(self):
        self.payload = [{"currency": "USDT", "net_type": "SYNTHETIC_NATIVE_NETWORK", "wallet_state": "working"}]
        self.assertEqual(self.wallet.list_wallet_statuses(), self.payload)
        self.payload = [{"currency": "USDT", "balance": "100", "locked": "0"}]
        self.assertEqual(self.wallet.get_accounts(), self.payload)
        self.payload = {"unexpected": []}
        with self.assertRaises(ExchangeRequestError):
            self.wallet.get_accounts()


if __name__ == "__main__":
    unittest.main()
