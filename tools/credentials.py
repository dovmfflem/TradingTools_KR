from __future__ import annotations

import argparse
import getpass

from src.core.credentials import (
    SPECS,
    delete_credentials_from_keyring,
    get_credential_spec,
    keyring_credentials_exist,
    save_credentials_to_keyring,
)


CLI_EXCHANGES = sorted(SPECS)


def _set_credentials(
    exchange: str,
    *,
    keyring_service: str,
    keyring_primary: str | None,
    keyring_secret: str | None,
) -> None:
    spec = get_credential_spec(exchange)
    primary = getpass.getpass(f"{spec.exchange} {spec.primary_name}: ")
    secret = getpass.getpass(f"{spec.exchange} {spec.secret_name}: ")
    save_credentials_to_keyring(
        spec.exchange,
        primary_key=primary,
        secret_key=secret,
        keyring_service=keyring_service,
        keyring_primary=keyring_primary,
        keyring_secret=keyring_secret,
    )
    print(f"saved {spec.exchange} credentials to keyring service '{keyring_service}'")


def _delete_credentials(
    exchange: str,
    *,
    keyring_service: str,
    keyring_primary: str | None,
    keyring_secret: str | None,
) -> None:
    spec = get_credential_spec(exchange)
    delete_credentials_from_keyring(
        spec.exchange,
        keyring_service=keyring_service,
        keyring_primary=keyring_primary,
        keyring_secret=keyring_secret,
    )
    print(f"deleted {spec.exchange} credentials from keyring service '{keyring_service}'")


def _list_credentials(
    *,
    keyring_service: str,
    keyring_primary: str | None,
    keyring_secret: str | None,
) -> None:
    for exchange in CLI_EXCHANGES:
        state = (
            "set"
            if keyring_credentials_exist(
                exchange,
                keyring_service=keyring_service,
                keyring_primary=keyring_primary,
                keyring_secret=keyring_secret,
            )
            else "missing"
        )
        print(f"{exchange}: {state}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage TradingTools KR keyring credentials.")
    parser.add_argument(
        "--keyring-service",
        default="TradingTools_KR",
        help="OS keyring service namespace",
    )
    parser.add_argument("--keyring-primary", default=None, help="override keyring primary item name")
    parser.add_argument("--keyring-secret", default=None, help="override keyring secret item name")
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser("set", help="save credentials to keyring")
    set_parser.add_argument("exchange", choices=CLI_EXCHANGES)

    delete_parser = subparsers.add_parser("delete", help="delete credentials from keyring")
    delete_parser.add_argument("exchange", choices=CLI_EXCHANGES)

    subparsers.add_parser("list", help="show which keyring credentials exist")

    args = parser.parse_args()
    if args.command == "set":
        _set_credentials(
            args.exchange,
            keyring_service=args.keyring_service,
            keyring_primary=args.keyring_primary,
            keyring_secret=args.keyring_secret,
        )
    elif args.command == "delete":
        _delete_credentials(
            args.exchange,
            keyring_service=args.keyring_service,
            keyring_primary=args.keyring_primary,
            keyring_secret=args.keyring_secret,
        )
    elif args.command == "list":
        _list_credentials(
            keyring_service=args.keyring_service,
            keyring_primary=args.keyring_primary,
            keyring_secret=args.keyring_secret,
        )


if __name__ == "__main__":
    main()
