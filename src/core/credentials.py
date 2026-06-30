from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


CredentialSource = Literal["auto", "env", "keyring", "info_yaml"]

KEYRING_SERVICE = "TradingTools_KR"
ENV_PREFIX = "TRADINGTOOLS"


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
    "upbit_pocket_1": CredentialSpec(
        exchange="upbit_pocket_1",
        primary_name="api_key",
        secret_name="secret_key",
        env_primary="TRADINGTOOLS_UPBIT_POCKET_1_API_KEY",
        env_secret="TRADINGTOOLS_UPBIT_POCKET_1_SECRET_KEY",
        yaml_primary="upbit_pocket_1_api_key",
        yaml_secret="upbit_pocket_1_secret_key",
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
    "binance": CredentialSpec(
        exchange="binance",
        primary_name="api_key",
        secret_name="secret_key",
        env_primary="TRADINGTOOLS_BINANCE_API_KEY",
        env_secret="TRADINGTOOLS_BINANCE_SECRET_KEY",
        yaml_primary="binance_api_key",
        yaml_secret="binance_secret_key",
        keyring_primary="binance.api_key",
        keyring_secret="binance.secret_key",
    ),
    "binance_futures": CredentialSpec(
        exchange="binance_futures",
        primary_name="api_key",
        secret_name="secret_key",
        env_primary="TRADINGTOOLS_BINANCE_FUTURES_API_KEY",
        env_secret="TRADINGTOOLS_BINANCE_FUTURES_SECRET_KEY",
        yaml_primary="binance_futures_api_key",
        yaml_secret="binance_futures_secret_key",
        keyring_primary="binance_futures.api_key",
        keyring_secret="binance_futures.secret_key",
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


def _normalize_env_prefix(env_prefix: str | None) -> str | None:
    if env_prefix is None:
        return None
    normalized = env_prefix.strip().upper()
    if not normalized:
        raise ValueError("env_prefix must not be empty")
    return normalized.rstrip("_")


def _env_names(spec: CredentialSpec, *, env_prefix: str | None = None) -> tuple[str, str]:
    prefix = _normalize_env_prefix(env_prefix)
    if prefix is None:
        return spec.env_primary, spec.env_secret
    exchange_part = spec.exchange.upper()
    primary_part = spec.primary_name.upper()
    secret_part = spec.secret_name.upper()
    return f"{prefix}_{exchange_part}_{primary_part}", f"{prefix}_{exchange_part}_{secret_part}"


def _resolve_pair(
    default_primary: str,
    default_secret: str,
    *,
    primary_override: str | None = None,
    secret_override: str | None = None,
) -> tuple[str, str]:
    primary = primary_override.strip() if primary_override is not None else default_primary
    secret = secret_override.strip() if secret_override is not None else default_secret
    if not primary:
        raise ValueError("primary credential key name must not be empty")
    if not secret:
        raise ValueError("secret credential key name must not be empty")
    return primary, secret


def load_credentials(
    exchange: str,
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
    keyring_service: str = KEYRING_SERVICE,
) -> ExchangeCredentials:
    spec = get_credential_spec(exchange)

    if source == "auto":
        errors: list[str] = []
        for candidate in ("env", "keyring", "info_yaml"):
            try:
                return load_credentials(
                    exchange,
                    source=candidate,
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
        return _load_env(
            spec,
            env_prefix=env_prefix,
            env_primary=env_primary,
            env_secret=env_secret,
        )
    if source == "keyring":
        return _load_keyring(
            spec,
            keyring_service=keyring_service,
            keyring_primary=keyring_primary,
            keyring_secret=keyring_secret,
        )
    if source == "info_yaml":
        return _load_info_yaml(
            spec,
            file_path=file_path,
            yaml_primary=yaml_primary,
            yaml_secret=yaml_secret,
        )

    raise CredentialError(f"unsupported credential source '{source}'")


def _load_env(
    spec: CredentialSpec,
    *,
    env_prefix: str | None = None,
    env_primary: str | None = None,
    env_secret: str | None = None,
) -> ExchangeCredentials:
    default_primary, default_secret = _env_names(spec, env_prefix=env_prefix)
    resolved_primary, resolved_secret = _resolve_pair(
        default_primary,
        default_secret,
        primary_override=env_primary,
        secret_override=env_secret,
    )
    primary = os.environ.get(resolved_primary)
    secret = os.environ.get(resolved_secret)
    if not primary or not secret:
        raise CredentialNotFoundError(
            f"env credential missing: {resolved_primary} or {resolved_secret}"
        )
    return ExchangeCredentials(
        exchange=spec.exchange,
        primary_key=primary,
        secret_key=secret,
        source="env",
        primary_name=spec.primary_name,
    )


def _load_info_yaml(
    spec: CredentialSpec,
    *,
    file_path: str,
    yaml_primary: str | None = None,
    yaml_secret: str | None = None,
) -> ExchangeCredentials:
    path = Path(file_path)
    if not path.exists():
        raise CredentialNotFoundError(f"{file_path} not found")

    config = _parse_simple_yaml_mapping(path.read_text(encoding="utf-8"))
    resolved_primary, resolved_secret = _resolve_pair(
        spec.yaml_primary,
        spec.yaml_secret,
        primary_override=yaml_primary,
        secret_override=yaml_secret,
    )
    primary = config.get(resolved_primary)
    secret = config.get(resolved_secret)
    if not primary or not secret:
        raise CredentialNotFoundError(
            f"info.yaml credential missing: {resolved_primary} or {resolved_secret}"
        )
    return ExchangeCredentials(
        exchange=spec.exchange,
        primary_key=str(primary),
        secret_key=str(secret),
        source="info_yaml",
        primary_name=spec.primary_name,
    )


def _load_keyring(
    spec: CredentialSpec,
    *,
    keyring_service: str = KEYRING_SERVICE,
    keyring_primary: str | None = None,
    keyring_secret: str | None = None,
) -> ExchangeCredentials:
    keyring = _import_keyring()
    resolved_primary, resolved_secret = _resolve_pair(
        spec.keyring_primary,
        spec.keyring_secret,
        primary_override=keyring_primary,
        secret_override=keyring_secret,
    )
    primary = keyring.get_password(keyring_service, resolved_primary)
    secret = keyring.get_password(keyring_service, resolved_secret)
    if not primary or not secret:
        raise CredentialNotFoundError(
            f"keyring credential missing: {resolved_primary} or {resolved_secret}"
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
    keyring_service: str = KEYRING_SERVICE,
    keyring_primary: str | None = None,
    keyring_secret: str | None = None,
) -> None:
    if not primary_key:
        raise ValueError("primary_key is required")
    if not secret_key:
        raise ValueError("secret_key is required")

    spec = get_credential_spec(exchange)
    keyring = _import_keyring()
    resolved_primary, resolved_secret = _resolve_pair(
        spec.keyring_primary,
        spec.keyring_secret,
        primary_override=keyring_primary,
        secret_override=keyring_secret,
    )
    keyring.set_password(keyring_service, resolved_primary, primary_key)
    keyring.set_password(keyring_service, resolved_secret, secret_key)


def delete_credentials_from_keyring(
    exchange: str,
    *,
    keyring_service: str = KEYRING_SERVICE,
    keyring_primary: str | None = None,
    keyring_secret: str | None = None,
) -> None:
    spec = get_credential_spec(exchange)
    keyring = _import_keyring()
    resolved_primary, resolved_secret = _resolve_pair(
        spec.keyring_primary,
        spec.keyring_secret,
        primary_override=keyring_primary,
        secret_override=keyring_secret,
    )
    for key in (resolved_primary, resolved_secret):
        try:
            keyring.delete_password(keyring_service, key)
        except Exception as error:
            if error.__class__.__name__ == "PasswordDeleteError":
                continue
            raise


def keyring_credentials_exist(
    exchange: str,
    *,
    keyring_service: str = KEYRING_SERVICE,
    keyring_primary: str | None = None,
    keyring_secret: str | None = None,
) -> bool:
    try:
        load_credentials(
            exchange,
            source="keyring",
            keyring_service=keyring_service,
            keyring_primary=keyring_primary,
            keyring_secret=keyring_secret,
        )
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
    "ENV_PREFIX",
    "KEYRING_SERVICE",
    "SPECS",
    "delete_credentials_from_keyring",
    "get_credential_spec",
    "keyring_credentials_exist",
    "load_credentials",
    "save_credentials_to_keyring",
]
