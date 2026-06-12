from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


CredentialSource = Literal["auto", "env", "keyring", "info_yaml"]

KEYRING_SERVICE = "TradingTools_KR"


class CredentialError(Exception):
    """Base error for credential loading and storage failures."""


class CredentialNotFoundError(CredentialError):
    """Raised when credentials cannot be found in the requested source."""


@dataclass(frozen=True)
class CredentialSpec:
    exchange: str
    primary_name: str
    secret_name: str
    env_primary: str
    env_secret: str
    yaml_primary: str
    yaml_secret: str
    keyring_primary: str
    keyring_secret: str


@dataclass(frozen=True)
class ExchangeCredentials:
    exchange: str
    primary_key: str
    secret_key: str
    source: str
    primary_name: str

    @property
    def api_key(self) -> str:
        return self.primary_key

    @property
    def access_token(self) -> str:
        return self.primary_key


SPECS: dict[str, CredentialSpec] = {
    "upbit": CredentialSpec(
        exchange="upbit",
        primary_name="api_key",
        secret_name="secret_key",
        env_primary="TRADINGTOOLS_UPBIT_API_KEY",
        env_secret="TRADINGTOOLS_UPBIT_SECRET_KEY",
        yaml_primary="upbit_api_key",
        yaml_secret="upbit_secret_key",
        keyring_primary="upbit.api_key",
        keyring_secret="upbit.secret_key",
    ),
    "upbit_pocket": CredentialSpec(
        exchange="upbit_pocket",
        primary_name="api_key",
        secret_name="secret_key",
        env_primary="TRADINGTOOLS_UPBIT_POCKET_API_KEY",
        env_secret="TRADINGTOOLS_UPBIT_POCKET_SECRET_KEY",
        yaml_primary="upbit_pocket_api_key",
        yaml_secret="upbit_pocket_secret_key",
        keyring_primary="upbit_pocket.api_key",
        keyring_secret="upbit_pocket.secret_key",
    ),
    "upbit_pocket_1": CredentialSpec(
        exchange="upbit_pocket_1",
        primary_name="api_key",
        secret_name="secret_key",
        env_primary="TRADINGTOOLS_UPBIT_POCKET_API_KEY",
        env_secret="TRADINGTOOLS_UPBIT_POCKET_SECRET_KEY",
        yaml_primary="upbit_pocket_api_key",
        yaml_secret="upbit_pocket_secret_key",
        keyring_primary="upbit_pocket_1.api_key",
        keyring_secret="upbit_pocket_1.secret_key",
    ),
    "upbit_pocket_2": CredentialSpec(
        exchange="upbit_pocket_2",
        primary_name="api_key",
        secret_name="secret_key",
        env_primary="TRADINGTOOLS_UPBIT_POCKET_2_API_KEY",
        env_secret="TRADINGTOOLS_UPBIT_POCKET_2_SECRET_KEY",
        yaml_primary="upbit_pocket_2_api_key",
        yaml_secret="upbit_pocket_2_secret_key",
        keyring_primary="upbit_pocket_2.api_key",
        keyring_secret="upbit_pocket_2.secret_key",
    ),
    "upbit_pocket_3": CredentialSpec(
        exchange="upbit_pocket_3",
        primary_name="api_key",
        secret_name="secret_key",
        env_primary="TRADINGTOOLS_UPBIT_POCKET_3_API_KEY",
        env_secret="TRADINGTOOLS_UPBIT_POCKET_3_SECRET_KEY",
        yaml_primary="upbit_pocket_3_api_key",
        yaml_secret="upbit_pocket_3_secret_key",
        keyring_primary="upbit_pocket_3.api_key",
        keyring_secret="upbit_pocket_3.secret_key",
    ),
    "upbit_pocket_4": CredentialSpec(
        exchange="upbit_pocket_4",
        primary_name="api_key",
        secret_name="secret_key",
        env_primary="TRADINGTOOLS_UPBIT_POCKET_4_API_KEY",
        env_secret="TRADINGTOOLS_UPBIT_POCKET_4_SECRET_KEY",
        yaml_primary="upbit_pocket_4_api_key",
        yaml_secret="upbit_pocket_4_secret_key",
        keyring_primary="upbit_pocket_4.api_key",
        keyring_secret="upbit_pocket_4.secret_key",
    ),
    "upbit_pocket_5": CredentialSpec(
        exchange="upbit_pocket_5",
        primary_name="api_key",
        secret_name="secret_key",
        env_primary="TRADINGTOOLS_UPBIT_POCKET_5_API_KEY",
        env_secret="TRADINGTOOLS_UPBIT_POCKET_5_SECRET_KEY",
        yaml_primary="upbit_pocket_5_api_key",
        yaml_secret="upbit_pocket_5_secret_key",
        keyring_primary="upbit_pocket_5.api_key",
        keyring_secret="upbit_pocket_5.secret_key",
    ),
    "bithumb": CredentialSpec(
        exchange="bithumb",
        primary_name="api_key",
        secret_name="secret_key",
        env_primary="TRADINGTOOLS_BITHUMB_API_KEY",
        env_secret="TRADINGTOOLS_BITHUMB_SECRET_KEY",
        yaml_primary="bithumb_api_key",
        yaml_secret="bithumb_secret_key",
        keyring_primary="bithumb.api_key",
        keyring_secret="bithumb.secret_key",
    ),
    "coinone": CredentialSpec(
        exchange="coinone",
        primary_name="access_token",
        secret_name="secret_key",
        env_primary="TRADINGTOOLS_COINONE_ACCESS_TOKEN",
        env_secret="TRADINGTOOLS_COINONE_SECRET_KEY",
        yaml_primary="coinone_access_token",
        yaml_secret="coinone_secret_key",
        keyring_primary="coinone.access_token",
        keyring_secret="coinone.secret_key",
    ),
}


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    result: list[str] = []

    for ch in line:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            break
        result.append(ch)

    return "".join(result).rstrip()


def _parse_simple_yaml_mapping(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue

        line = _strip_comment(raw_line)
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if ":" not in content:
            continue

        key, value = content.split(":", 1)
        key = key.strip()
        value = value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError("invalid YAML indentation")

        current = stack[-1][1]
        if value == "":
            child: dict[str, Any] = {}
            current[key] = child
            stack.append((indent, child))
        else:
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            current[key] = value

    return root


def get_credential_spec(exchange: str) -> CredentialSpec:
    key = exchange.strip().lower()
    try:
        return SPECS[key]
    except KeyError as error:
        supported = ", ".join(sorted(SPECS))
        raise CredentialError(f"unsupported exchange '{exchange}'. supported: {supported}") from error


def load_credentials(
    exchange: str,
    *,
    source: CredentialSource = "auto",
    file_path: str = "info.yaml",
) -> ExchangeCredentials:
    spec = get_credential_spec(exchange)

    if source == "auto":
        errors: list[str] = []
        for candidate in ("env", "keyring", "info_yaml"):
            try:
                return load_credentials(exchange, source=candidate, file_path=file_path)
            except CredentialNotFoundError as error:
                errors.append(str(error))
                continue
            except CredentialError as error:
                if candidate == "keyring":
                    errors.append(str(error))
                    continue
                raise
        raise CredentialNotFoundError("; ".join(errors))

    if source == "env":
        return _load_env(spec)
    if source == "keyring":
        return _load_keyring(spec)
    if source == "info_yaml":
        return _load_info_yaml(spec, file_path=file_path)

    raise CredentialError(f"unsupported credential source '{source}'")


def _load_env(spec: CredentialSpec) -> ExchangeCredentials:
    primary = os.environ.get(spec.env_primary)
    secret = os.environ.get(spec.env_secret)
    if not primary or not secret:
        raise CredentialNotFoundError(
            f"env credential missing: {spec.env_primary} or {spec.env_secret}"
        )
    return ExchangeCredentials(
        exchange=spec.exchange,
        primary_key=primary,
        secret_key=secret,
        source="env",
        primary_name=spec.primary_name,
    )


def _load_info_yaml(spec: CredentialSpec, *, file_path: str) -> ExchangeCredentials:
    path = Path(file_path)
    if not path.exists():
        raise CredentialNotFoundError(f"{file_path} not found")

    config = _parse_simple_yaml_mapping(path.read_text(encoding="utf-8"))
    primary = config.get(spec.yaml_primary)
    secret = config.get(spec.yaml_secret)
    if not primary or not secret:
        raise CredentialNotFoundError(
            f"info.yaml credential missing: {spec.yaml_primary} or {spec.yaml_secret}"
        )
    return ExchangeCredentials(
        exchange=spec.exchange,
        primary_key=str(primary),
        secret_key=str(secret),
        source="info_yaml",
        primary_name=spec.primary_name,
    )


def _load_keyring(spec: CredentialSpec) -> ExchangeCredentials:
    keyring = _import_keyring()
    primary = keyring.get_password(KEYRING_SERVICE, spec.keyring_primary)
    secret = keyring.get_password(KEYRING_SERVICE, spec.keyring_secret)
    if not primary or not secret:
        raise CredentialNotFoundError(
            f"keyring credential missing: {spec.keyring_primary} or {spec.keyring_secret}"
        )
    return ExchangeCredentials(
        exchange=spec.exchange,
        primary_key=primary,
        secret_key=secret,
        source="keyring",
        primary_name=spec.primary_name,
    )


def save_credentials_to_keyring(
    exchange: str,
    *,
    primary_key: str,
    secret_key: str,
) -> None:
    if not primary_key:
        raise ValueError("primary_key is required")
    if not secret_key:
        raise ValueError("secret_key is required")

    spec = get_credential_spec(exchange)
    keyring = _import_keyring()
    keyring.set_password(KEYRING_SERVICE, spec.keyring_primary, primary_key)
    keyring.set_password(KEYRING_SERVICE, spec.keyring_secret, secret_key)


def delete_credentials_from_keyring(exchange: str) -> None:
    spec = get_credential_spec(exchange)
    keyring = _import_keyring()
    for key in (spec.keyring_primary, spec.keyring_secret):
        try:
            keyring.delete_password(KEYRING_SERVICE, key)
        except Exception as error:
            if error.__class__.__name__ == "PasswordDeleteError":
                continue
            raise


def keyring_credentials_exist(exchange: str) -> bool:
    try:
        load_credentials(exchange, source="keyring")
    except CredentialError:
        return False
    return True


def _import_keyring() -> Any:
    try:
        import keyring  # type: ignore[import-not-found]
    except ModuleNotFoundError as error:
        raise CredentialError(
            "keyring is not installed. install it with: python -m pip install keyring"
        ) from error
    return keyring


__all__ = [
    "CredentialError",
    "CredentialNotFoundError",
    "CredentialSource",
    "ExchangeCredentials",
    "KEYRING_SERVICE",
    "SPECS",
    "delete_credentials_from_keyring",
    "get_credential_spec",
    "keyring_credentials_exist",
    "load_credentials",
    "save_credentials_to_keyring",
]
