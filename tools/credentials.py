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


CLI_EXCHANGES = sorted(name for name in SPECS if name != "upbit_pocket")


def _set_credentials(exchange: str) -> None:
    spec = get_credential_spec(exchange)
    primary = getpass.getpass(f"{spec.exchange} {spec.primary_name}: ")
    secret = getpass.getpass(f"{spec.exchange} {spec.secret_name}: ")
    save_credentials_to_keyring(
        spec.exchange,
        primary_key=primary,
        secret_key=secret,
    )
    print(f"saved {spec.exchange} credentials to keyring")


def _delete_credentials(exchange: str) -> None:
    spec = get_credential_spec(exchange)
    delete_credentials_from_keyring(spec.exchange)
    print(f"deleted {spec.exchange} credentials from keyring")


def _list_credentials() -> None:
    for exchange in CLI_EXCHANGES:
        state = "set" if keyring_credentials_exist(exchange) else "missing"
        print(f"{exchange}: {state}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage TradingTools KR keyring credentials.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser("set", help="save credentials to keyring")
    set_parser.add_argument("exchange", choices=CLI_EXCHANGES)

    delete_parser = subparsers.add_parser("delete", help="delete credentials from keyring")
    delete_parser.add_argument("exchange", choices=CLI_EXCHANGES)

    subparsers.add_parser("list", help="show which keyring credentials exist")

    args = parser.parse_args()
    if args.command == "set":
        _set_credentials(args.exchange)
    elif args.command == "delete":
        _delete_credentials(args.exchange)
    elif args.command == "list":
        _list_credentials()


if __name__ == "__main__":
    main()
