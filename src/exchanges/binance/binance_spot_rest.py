from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from src.core.credentials import CredentialError, CredentialSource, load_credentials
from src.exchanges.binance.binance_futures_rest import _parse_simple_yaml_mapping


class BinanceSpotRestError(Exception):
    """Raised when a Binance Spot REST API request fails."""


class BinanceSpotRest:
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        *,
        api_url: str = "https://api.binance.com",
        timeout_seconds: float = 10.0,
        recv_window_ms: int = 5000,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        if not secret_key:
            raise ValueError("secret_key is required")
        if recv_window_ms <= 0:
            raise ValueError("recv_window_ms must be greater than 0")

        self.api_key = api_key
        self.secret_key = secret_key
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.recv_window_ms = recv_window_ms
        self._session = requests.Session()
        self._server_time_offset_ms = 0

    @classmethod
    def from_info_yaml(cls, file_path: str = "info.yaml") -> "BinanceSpotRest":
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"{file_path} not found")

        config = _parse_simple_yaml_mapping(path.read_text(encoding="utf-8"))
        api_key = config.get("binance_api_key") or config.get("binance_futures_api_key")
        secret_key = config.get("binance_secret_key") or config.get("binance_futures_secret_key")
        if not api_key or not secret_key:
            raise ValueError(
                "info.yaml must include 'binance_api_key' and 'binance_secret_key' "
                "(or binance_futures_api_key/binance_futures_secret_key)"
            )
        return cls(api_key=str(api_key), secret_key=str(secret_key))

    @classmethod
    def from_config(
        cls,
        *,
        source: CredentialSource = "auto",
        file_path: str = "info.yaml",
        env_prefix: str | None = None,
        env_primary: str | None = None,
        env_secret: str | None = None,
        yaml_primary: str | None = None,
        yaml_secret: str | None = None,
        keyring_primary: str | None = None,
        keyring_secret: str | None = None,
        keyring_service: str = "TradingTools_KR",
    ) -> "BinanceSpotRest":
        try:
            credentials = load_credentials(
                "binance",
                source=source,
                file_path=file_path,
                env_prefix=env_prefix,
                env_primary=env_primary,
                env_secret=env_secret,
                yaml_primary=yaml_primary,
                yaml_secret=yaml_secret,
                keyring_primary=keyring_primary,
                keyring_secret=keyring_secret,
                keyring_service=keyring_service,
            )
        except CredentialError:
            if source != "auto":
                raise
            credentials = load_credentials(
                "binance_futures",
                source=source,
                file_path=file_path,
                env_prefix=env_prefix,
                env_primary=env_primary,
                env_secret=env_secret,
                yaml_primary=yaml_primary,
                yaml_secret=yaml_secret,
                keyring_primary=keyring_primary,
                keyring_secret=keyring_secret,
                keyring_service=keyring_service,
            )
        return cls(api_key=credentials.api_key, secret_key=credentials.secret_key)

    @staticmethod
    def _build_query_string(payload: dict[str, Any]) -> str:
        filtered: dict[str, Any] = {}
        for key, value in payload.items():
            if value is None:
                continue
            filtered[key] = value
        return urlencode(filtered, doseq=True)

    def _current_timestamp_ms(self) -> int:
        return int(time.time() * 1000) + int(self._server_time_offset_ms)

    def _signed_params(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if params:
            payload.update(params)
        payload["timestamp"] = self._current_timestamp_ms()
        payload["recvWindow"] = self.recv_window_ms

        query_string = self._build_query_string(payload)
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        payload["signature"] = signature
        return payload

    def _request_public(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = self._session.request(
            method=method,
            url=f"{self.api_url}{path}",
            params=params,
            timeout=self.timeout_seconds,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}
        if not response.ok:
            raise BinanceSpotRestError(
                f"{method} {path} failed (status={response.status_code}): {payload}"
            )
        return payload

    def _sync_server_time_offset(self) -> None:
        data = self._request_public("GET", "/api/v3/time")
        if not isinstance(data, dict):
            raise BinanceSpotRestError(f"invalid server time response: {data}")
        server_time = data.get("serverTime")
        if server_time is None:
            raise BinanceSpotRestError(f"serverTime missing in response: {data}")
        try:
            server_ms = int(str(server_time))
        except (TypeError, ValueError):
            raise BinanceSpotRestError(f"serverTime missing in response: {data}")
        self._server_time_offset_ms = server_ms - int(time.time() * 1000)

    def _request_signed(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        for attempt in range(2):
            response = self._session.request(
                method=method,
                url=f"{self.api_url}{path}",
                headers={"X-MBX-APIKEY": self.api_key},
                params=self._signed_params(params),
                timeout=self.timeout_seconds,
            )
            try:
                payload = response.json()
            except ValueError:
                payload = {"raw": response.text}
            if response.ok:
                return payload

            error_code: int | None = None
            if isinstance(payload, dict):
                raw_code = payload.get("code")
                try:
                    error_code = int(raw_code) if raw_code is not None else None
                except (TypeError, ValueError):
                    error_code = None
            if error_code == -1021 and attempt == 0:
                self._sync_server_time_offset()
                continue
            raise BinanceSpotRestError(
                f"{method} {path} failed (status={response.status_code}): {payload}"
            )

        raise BinanceSpotRestError(f"{method} {path} failed: unknown retry state")

    def get_account(self, *, omit_zero_balances: bool | None = None) -> dict[str, Any]:
        data = self._request_signed(
            "GET",
            "/api/v3/account",
            params={"omitZeroBalances": "true" if omit_zero_balances else None},
        )
        return data if isinstance(data, dict) else {"data": data}

    def get_balances(self, *, omit_zero_balances: bool = True) -> list[dict[str, Any]]:
        data = self.get_account(omit_zero_balances=omit_zero_balances)
        balances = data.get("balances")
        if not isinstance(balances, list):
            return []
        return [item for item in balances if isinstance(item, dict)]
