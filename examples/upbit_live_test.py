from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from src.exchanges.upbit.upbit_rest import UpbitRest


def _json(data: Any) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        return repr(data)


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _account_balance(accounts: list[dict[str, Any]], currency: str) -> Decimal:
    currency_upper = currency.upper()
    for account in accounts:
        if str(account.get("currency", "")).upper() == currency_upper:
            return _decimal(account.get("balance"))
    return Decimal("0")


def _run_step(name: str, func: Callable[[], Any]) -> tuple[bool, Any]:
    print(f"\n[RUN] {name}")
    try:
        result = func()
    except Exception as exc:
        print(f"[FAIL] {name}: {exc}")
        return False, exc

    print(f"[PASS] {name}")
    if result is not None:
        print(_json(result))
    return True, result


def _run_public_tests(client: UpbitRest, ticker: str, quote_currency: str) -> None:
    _run_step("trading pairs", lambda: client.list_trading_pairs(is_details=True)[:3])
    _run_step("second candles", lambda: client.get_second_candles(ticker, count=3))
    _run_step("minute candles", lambda: client.get_minute_candles(ticker, unit=1, count=3))
    _run_step("day candles", lambda: client.get_day_candles(ticker, count=3))
    _run_step("week candles", lambda: client.get_week_candles(ticker, count=3))
    _run_step("month candles", lambda: client.get_month_candles(ticker, count=3))
    _run_step("year candles", lambda: client.get_year_candles(ticker, count=3))
    _run_step("recent trades", lambda: client.get_trades(ticker, count=3))
    _run_step("ticker by pair", lambda: client.get_ticker(ticker))
    _run_step("ticker by quote currency", lambda: client.get_quote_tickers(quote_currency))
    _run_step("orderbook instruments", lambda: client.get_orderbook_policy(ticker))
    _run_step("orderbook raw", lambda: client.get_orderbook(ticker, count=5))
    _run_step("orderbook parsed", lambda: client.get_orderbook_parse(ticker, count=5))


def _run_private_read_tests(
    client: UpbitRest,
    ticker: str,
    base_currency: str,
    net_type: str | None,
) -> None:
    _run_step("accounts", client.get_accounts)
    _run_step("order chance", lambda: client.get_order_chance(ticker))
    _run_step("open orders", lambda: client.list_open_orders(ticker=ticker, limit=3))
    _run_step("closed orders", lambda: client.list_closed_orders(ticker=ticker, limit=3))
    _run_step("wallet statuses", client.list_wallet_statuses)
    _run_step("api keys", client.list_api_keys)
    _run_step("withdraw chance", lambda: client.get_withdraw_chance(currency=base_currency, net_type=net_type))
    _run_step(
        "withdraw addresses",
        lambda: client.list_withdraw_addresses(currency=base_currency, net_type=net_type),
    )
    _run_step(
        "deposit addresses",
        lambda: client.list_deposit_addresses(currency=base_currency, net_type=net_type),
    )
    if net_type:
        _run_step(
            "deposit chance",
            lambda: client.get_deposit_chance(currency=base_currency, net_type=net_type),
        )
        _run_step(
            "deposit address",
            lambda: client.get_deposit_address(currency=base_currency, net_type=net_type),
        )
    else:
        print("\n[SKIP] deposit chance/address: --net-type is required")


def _run_pocket_read_tests(client: UpbitRest, subpocket_uuid: str | None) -> None:
    _run_step("pocket information", client.get_pocket)
    _run_step("pocket api keys", client.list_pocket_api_keys)
    _run_step("main pocket transfers", lambda: client.list_main_pocket_transfers(limit=3))
    _run_step("subpocket transfers", lambda: client.list_subpocket_transfers(limit=3))
    if subpocket_uuid:
        _run_step(
            "subpocket accounts",
            lambda: client.get_subpocket_accounts(uuid_value=subpocket_uuid),
        )
    else:
        print("\n[SKIP] subpocket accounts: --subpocket-uuid is required")


def _run_trade_round_trip(
    client: UpbitRest,
    ticker: str,
    base_currency: str,
    krw_amount: Decimal,
    settle_seconds: float,
) -> None:
    print("\n[TRADE] market buy/sell round trip")
    _, price_before = _run_step("price before buy", lambda: client.get_ticker(ticker))
    _, accounts_before = _run_step("accounts before buy", client.get_accounts)

    if not isinstance(accounts_before, list):
        print("[FAIL] accounts before buy: unexpected response")
        return

    base_before = _account_balance(accounts_before, base_currency)
    krw_before = _account_balance(accounts_before, "KRW")
    print(f"[INFO] before KRW={krw_before} {base_currency}={base_before}")

    buy_ok, buy_result = _run_step(
        f"market buy {krw_amount} KRW",
        lambda: client.place_order(
            ticker=ticker,
            side="bid",
            order_type="price",
            price=str(krw_amount),
        ),
    )
    if not buy_ok:
        return

    print(f"[INFO] waiting {settle_seconds} seconds before balance check")
    time.sleep(settle_seconds)

    _, accounts_after_buy = _run_step("accounts before sell", client.get_accounts)
    if not isinstance(accounts_after_buy, list):
        print("[FAIL] accounts before sell: unexpected response")
        return

    base_after_buy = _account_balance(accounts_after_buy, base_currency)
    bought_volume = base_after_buy - base_before
    print(f"[INFO] bought delta {base_currency}={bought_volume}")

    if bought_volume <= 0:
        print("[FAIL] sell skipped: bought balance delta is not positive")
        return

    _run_step("price before sell", lambda: client.get_ticker(ticker))
    sell_ok, sell_result = _run_step(
        f"market sell {bought_volume} {base_currency}",
        lambda: client.place_order(
            ticker=ticker,
            side="ask",
            order_type="market",
            volume=str(bought_volume),
        ),
    )
    if not sell_ok:
        print(f"[WARN] buy succeeded but sell failed. buy_result={_json(buy_result)}")
        return

    print(f"[INFO] waiting {settle_seconds} seconds after sell")
    time.sleep(settle_seconds)
    _run_step("accounts after sell", client.get_accounts)
    print(f"[INFO] buy_result={_json(buy_result)}")
    print(f"[INFO] sell_result={_json(sell_result)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Upbit API smoke tester.")
    parser.add_argument("--info", default="info.yaml")
    parser.add_argument("--ticker", default="btc-krw")
    parser.add_argument("--quote-currency", default="KRW")
    parser.add_argument("--base-currency", default="BTC")
    parser.add_argument("--net-type", default="BTC")
    parser.add_argument("--subpocket-uuid", default=None)
    parser.add_argument("--trade-krw", default="6000")
    parser.add_argument("--settle-seconds", type=float, default=2.0)
    parser.add_argument("--private-read", action="store_true")
    parser.add_argument("--pocket-read", action="store_true")
    parser.add_argument("--use-pocket-key", action="store_true")
    parser.add_argument("--trade", action="store_true")
    args = parser.parse_args()

    client = (
        UpbitRest.from_pocket_info_yaml(args.info)
        if args.use_pocket_key
        else UpbitRest.from_info_yaml(args.info)
    )

    print("[INFO] Upbit live test started")
    print(f"[INFO] ticker={args.ticker} quote={args.quote_currency} base={args.base_currency}")

    _run_public_tests(client, args.ticker, args.quote_currency)

    if args.private_read:
        _run_private_read_tests(client, args.ticker, args.base_currency, args.net_type)
    else:
        print("\n[SKIP] private read tests: pass --private-read")

    if args.pocket_read:
        _run_pocket_read_tests(client, args.subpocket_uuid)
    else:
        print("\n[SKIP] pocket read tests: pass --pocket-read")

    if args.trade:
        _run_trade_round_trip(
            client=client,
            ticker=args.ticker,
            base_currency=args.base_currency,
            krw_amount=Decimal(args.trade_krw),
            settle_seconds=args.settle_seconds,
        )
    else:
        print("\n[SKIP] trade round trip: pass --trade")

    print("\n[DONE] Upbit live test finished")


if __name__ == "__main__":
    main()
