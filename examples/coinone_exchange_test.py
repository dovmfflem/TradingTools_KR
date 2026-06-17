from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.credentials import CredentialSource
from src.exchanges.coinone.coinone_rest import CoinoneRest, _to_pair


EXCHANGE = "coinone"
MAX_TRADE_KRW = Decimal("6000")
SELL_RATIO = Decimal("0.90")


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _new_result_dir(base_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    result_dir = base_dir / stamp
    result_dir.mkdir(parents=True, exist_ok=True)
    return result_dir


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json(payload) + "\n", encoding="utf-8")


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


def _account_balance(accounts: list[dict[str, Any]], currency: str) -> Decimal:
    currency_upper = currency.upper()
    for account in accounts:
        if str(account.get("currency", "")).upper() == currency_upper:
            return _decimal(account.get("balance"))
    return Decimal("0")


def _print_request(name: str, method: str, endpoint: str, params: dict[str, Any]) -> None:
    print("\n" + "=" * 80)
    print(f"[요청 API] {name}")
    print(f"[HTTP] {method} {endpoint}")
    print(f"[파라미터] {_json(params)}")


def _sleep_after_request(seconds: float) -> None:
    if seconds <= 0:
        return
    print(f"\n[대기] {seconds:.1f}초 후 다음 API를 호출합니다.")
    time.sleep(seconds)


def _run_step(
    *,
    name: str,
    slug: str,
    method: str,
    endpoint: str,
    params: dict[str, Any],
    call: Callable[[], Any],
    result_dir: Path,
    executed: bool = True,
    skip_reason: str | None = None,
    sleep_seconds: float = 0.0,
) -> tuple[dict[str, Any], Any]:
    artifact: dict[str, Any] = {
        "exchange": EXCHANGE,
        "category": "exchange",
        "api_name": name,
        "method": method,
        "endpoint": endpoint,
        "params": params,
        "captured_at": _now_iso(),
        "executed": executed,
    }

    if not executed:
        artifact.update({"success": None, "skipped": True, "skip_reason": skip_reason})
        print("\n" + "=" * 80)
        print(f"[건너뜀] {name}")
        print(f"[사유] {skip_reason}")
        _write_json(result_dir / f"{slug}.json", artifact)
        return artifact, None

    _print_request(name, method, endpoint, params)
    try:
        result = call()
    except Exception as error:
        artifact.update(
            {
                "success": False,
                "error_type": error.__class__.__name__,
                "error": str(error),
            }
        )
        print(f"[실패] {name}: {error}")
        _write_json(result_dir / f"{slug}.json", artifact)
        print(f"[저장] {result_dir / f'{slug}.json'}")
        _sleep_after_request(sleep_seconds)
        return artifact, error

    artifact.update({"success": True, "response": result})
    print(f"[성공] {name}")
    print("[결과]")
    print(_json(result))
    _write_json(result_dir / f"{slug}.json", artifact)
    print(f"[저장] {result_dir / f'{slug}.json'}")
    _sleep_after_request(sleep_seconds)
    return artifact, result


def _build_summary(
    *,
    result_dir: Path,
    ticker: str,
    quote_currency: str,
    target_currency: str,
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    successful = [
        {
            "api_name": item["api_name"],
            "method": item["method"],
            "endpoint": item["endpoint"],
        }
        for item in artifacts
        if item.get("success") is True
    ]
    failed = [
        {
            "api_name": item["api_name"],
            "method": item["method"],
            "endpoint": item["endpoint"],
            "error": item.get("error"),
        }
        for item in artifacts
        if item.get("success") is False
    ]
    skipped = [
        {
            "api_name": item["api_name"],
            "method": item["method"],
            "endpoint": item["endpoint"],
            "skip_reason": item.get("skip_reason"),
        }
        for item in artifacts
        if item.get("skipped")
    ]
    summary = {
        "exchange": EXCHANGE,
        "category": "exchange",
        "captured_at": _now_iso(),
        "ticker": ticker,
        "quote_currency": quote_currency,
        "target_currency": target_currency,
        "result_dir": str(result_dir),
        "executed_count": sum(1 for item in artifacts if item.get("executed")),
        "success_count": len(successful),
        "failure_count": len(failed),
        "skipped_count": len(skipped),
        "successful": successful,
        "failed": failed,
        "skipped": skipped,
    }
    _write_json(result_dir / "summary.json", summary)
    return summary


def _print_summary(summary: dict[str, Any]) -> None:
    print("\n" + "=" * 80)
    print("[요약] 코인원 Exchange API 테스트 결과")
    print(f"- 실행: {summary['executed_count']}개")
    print(f"- 성공: {summary['success_count']}개")
    print(f"- 실패: {summary['failure_count']}개")
    print(f"- 건너뜀: {summary['skipped_count']}개")

    for title, key in (
        ("성공한 API", "successful"),
        ("실패한 API", "failed"),
        ("건너뛴 API", "skipped"),
    ):
        print(f"\n[{title}]")
        items = summary[key]
        if not items:
            print("- 없음")
            continue
        for item in items:
            suffix = ""
            if item.get("error"):
                suffix = f" | {item['error']}"
            if item.get("skip_reason"):
                suffix = f" | {item['skip_reason']}"
            print(f"- {item['api_name']} ({item['method']} {item['endpoint']}){suffix}")


def _run_read_tests(
    *,
    client: CoinoneRest,
    ticker: str,
    quote_currency: str,
    target_currency: str,
    transaction_id: str | None,
    order_id: str | None,
    user_order_id: str | None,
    result_dir: Path,
    request_sleep_seconds: float,
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = [
        {
            "name": "전체 잔고 조회",
            "slug": "accounts",
            "method": "POST",
            "endpoint": "/v2.1/account/balance/all",
            "params": {},
            "call": client.get_accounts,
        },
        {
            "name": "특정 자산 잔고 조회",
            "slug": "balance",
            "method": "POST",
            "endpoint": "/v2.1/account/balance",
            "params": {"currencies": [quote_currency, target_currency]},
            "call": lambda: client.get_balance([quote_currency, target_currency]),
        },
        {
            "name": "전체 수수료 조회",
            "slug": "trade_fees",
            "method": "POST",
            "endpoint": "/v2.1/account/trade_fee",
            "params": {},
            "call": client.list_trade_fees,
        },
        {
            "name": "개별 종목 수수료 조회",
            "slug": "trade_fee_market",
            "method": "POST",
            "endpoint": "/v2.1/account/trade_fee/market",
            "params": {"quote_currency": quote_currency, "target_currency": target_currency},
            "call": lambda: client.get_trade_fee(ticker),
        },
        {
            "name": "가상자산 입금 주소 조회",
            "slug": "deposit_address",
            "method": "POST",
            "endpoint": "/v2.1/account/deposit_address",
            "params": {"currency": target_currency},
            "call": lambda: client.get_deposit_address(target_currency),
        },
        {
            "name": "미체결 주문 조회",
            "slug": "active_orders",
            "method": "POST",
            "endpoint": "/v2.1/order/active_orders",
            "params": {"quote_currency": quote_currency, "target_currency": target_currency},
            "call": lambda: client.get_open_orders(ticker=ticker, limit=100),
        },
        {
            "name": "전체 미체결 주문 조회",
            "slug": "active_orders_all",
            "method": "POST",
            "endpoint": "/v2.1/order/active_orders/all",
            "params": {},
            "call": client.list_all_open_orders,
        },
        {
            "name": "종목 별 미체결 주문 조회",
            "slug": "active_orders_market",
            "method": "POST",
            "endpoint": "/v2.1/order/active_orders/market",
            "params": {"quote_currency": quote_currency, "target_currency": target_currency},
            "call": lambda: client.list_open_orders_by_market(ticker=ticker),
        },
        {
            "name": "전체 체결 주문 조회",
            "slug": "completed_orders_all",
            "method": "POST",
            "endpoint": "/v2.1/order/completed_orders/all",
            "params": {"size": 3},
            "call": lambda: client.list_completed_orders(size=3),
        },
        {
            "name": "종목 별 체결 주문 조회",
            "slug": "completed_orders_market",
            "method": "POST",
            "endpoint": "/v2.1/order/completed_orders/market",
            "params": {"quote_currency": quote_currency, "target_currency": target_currency, "size": 3},
            "call": lambda: client.list_completed_orders(ticker=ticker, size=3),
        },
        {
            "name": "원화 입출금 내역 조회",
            "slug": "krw_transactions",
            "method": "POST",
            "endpoint": "/v2.1/transaction/krw/history",
            "params": {"size": 3},
            "call": lambda: client.list_krw_transactions(size=3),
        },
        {
            "name": "가상자산 입출금 내역 조회",
            "slug": "coin_transactions",
            "method": "POST",
            "endpoint": "/v2.1/transaction/coin/history",
            "params": {"currency": target_currency, "size": 3},
            "call": lambda: client.list_coin_transactions(currency=target_currency, size=3),
        },
        {
            "name": "가상자산 입출금 단건 조회",
            "slug": "coin_transaction",
            "method": "POST",
            "endpoint": "/v2.1/transaction/coin",
            "params": {"transaction_id": transaction_id},
            "call": lambda: client.get_coin_transaction(transaction_id=str(transaction_id)),
            "executed": bool(transaction_id),
            "skip_reason": "--transaction-id가 지정되지 않았습니다.",
        },
        {
            "name": "가상자산 출금 한도 조회",
            "slug": "withdraw_limit",
            "method": "POST",
            "endpoint": "/v2.1/transaction/coin/withdrawal/limit",
            "params": {"currency": target_currency},
            "call": lambda: client.get_withdraw_limit(currency=target_currency),
        },
        {
            "name": "출금 주소 목록 조회",
            "slug": "withdraw_addresses",
            "method": "POST",
            "endpoint": "/v2.1/transaction/coin/withdrawal/address",
            "params": {"currency": target_currency},
            "call": lambda: client.list_withdraw_addresses(currency=target_currency),
        },
        {
            "name": "주문 리워드 종목 정보 조회",
            "slug": "reward_markets",
            "method": "POST",
            "endpoint": "/v2.1/order/reward/markets",
            "params": {},
            "call": client.get_order_reward_markets,
        },
        {
            "name": "주문 리워드 내역 조회",
            "slug": "reward_history",
            "method": "POST",
            "endpoint": "/v2.1/order/reward/history",
            "params": {"quote_currency": quote_currency, "target_currency": target_currency, "size": 3},
            "call": lambda: client.list_order_rewards(ticker=ticker, size=3),
        },
        {
            "name": "주문 정보 조회",
            "slug": "order_info",
            "method": "POST",
            "endpoint": "/v2.1/order/order_info",
            "params": {"order_id": order_id, "user_order_id": user_order_id},
            "call": lambda: client.get_order(
                ticker=ticker,
                order_id=order_id,
                user_order_id=user_order_id,
            ),
            "executed": bool(order_id or user_order_id),
            "skip_reason": "--order-id 또는 --user-order-id가 지정되지 않았습니다.",
        },
        {
            "name": "특정 주문 정보 조회",
            "slug": "order_detail",
            "method": "POST",
            "endpoint": "/v2.1/order",
            "params": {"order_id": order_id},
            "call": lambda: client.get_order_detail(ticker=ticker, order_id=str(order_id)),
            "executed": bool(order_id),
            "skip_reason": "--order-id가 지정되지 않았습니다.",
        },
    ]

    artifacts: list[dict[str, Any]] = []
    for step in steps:
        artifact, _ = _run_step(
            name=step["name"],
            slug=step["slug"],
            method=step["method"],
            endpoint=step["endpoint"],
            params=step["params"],
            call=step["call"],
            result_dir=result_dir,
            executed=step.get("executed", True),
            skip_reason=step.get("skip_reason"),
            sleep_seconds=request_sleep_seconds,
        )
        artifacts.append(artifact)
    return artifacts


def _run_trade_test(
    *,
    client: CoinoneRest,
    ticker: str,
    quote_currency: str,
    target_currency: str,
    trade_krw: Decimal,
    settle_seconds: float,
    result_dir: Path,
    request_sleep_seconds: float,
) -> list[dict[str, Any]]:
    if trade_krw != MAX_TRADE_KRW:
        raise ValueError("trade_krw must be exactly 6000")

    artifacts: list[dict[str, Any]] = []
    print("\n[거래 테스트] 6000원 시장가 매수 후 매수 증가분의 90%만 시장가 매도합니다.")

    artifact, _ = _run_step(
        name="거래 전 현재가 조회",
        slug="price_before_trade",
        method="GET",
        endpoint=f"/public/v2/ticker_new/{quote_currency}/{target_currency}",
        params={},
        call=lambda: client.get_ticker(ticker),
        result_dir=result_dir,
        sleep_seconds=request_sleep_seconds,
    )
    artifacts.append(artifact)

    artifact, accounts_before = _run_step(
        name="거래 전 잔고 조회",
        slug="accounts_before_trade",
        method="POST",
        endpoint="/v2.1/account/balance/all",
        params={},
        call=client.get_accounts,
        result_dir=result_dir,
        sleep_seconds=request_sleep_seconds,
    )
    artifacts.append(artifact)
    if artifact.get("success") is not True or not isinstance(accounts_before, list):
        print("[중단] 거래 전 잔고 조회 실패")
        return artifacts

    base_before = _account_balance(accounts_before, target_currency)
    quote_before = _account_balance(accounts_before, quote_currency)
    print(f"[잔고] 거래 전 {quote_currency}={quote_before}, {target_currency}={base_before}")

    if quote_before < trade_krw:
        artifact, _ = _run_step(
            name="6000원 시장가 매수",
            slug="market_buy_6000",
            method="POST",
            endpoint="/v2.1/order",
            params={
                "quote_currency": quote_currency,
                "target_currency": target_currency,
                "side": "BUY",
                "type": "MARKET",
                "amount": str(trade_krw),
            },
            call=lambda: None,
            result_dir=result_dir,
            executed=False,
            skip_reason=f"{quote_currency} 잔고가 {trade_krw}보다 작습니다.",
            sleep_seconds=request_sleep_seconds,
        )
        artifacts.append(artifact)
        return artifacts

    artifact, _ = _run_step(
        name="6000원 시장가 매수",
        slug="market_buy_6000",
        method="POST",
        endpoint="/v2.1/order",
        params={
            "quote_currency": quote_currency,
            "target_currency": target_currency,
            "side": "BUY",
            "type": "MARKET",
            "amount": str(trade_krw),
        },
        call=lambda: client.place_order(
            ticker=ticker,
            side="buy",
            order_type="market",
            amount=str(trade_krw),
        ),
        result_dir=result_dir,
        sleep_seconds=request_sleep_seconds,
    )
    artifacts.append(artifact)
    if artifact.get("success") is not True:
        return artifacts

    print(f"[대기] {settle_seconds}초 후 매수 수량을 확인합니다.")
    time.sleep(settle_seconds)

    artifact, accounts_before_sell = _run_step(
        name="매도 전 잔고 조회",
        slug="accounts_before_sell",
        method="POST",
        endpoint="/v2.1/account/balance/all",
        params={},
        call=client.get_accounts,
        result_dir=result_dir,
        sleep_seconds=request_sleep_seconds,
    )
    artifacts.append(artifact)
    if artifact.get("success") is not True or not isinstance(accounts_before_sell, list):
        print("[중단] 매도 전 잔고 조회 실패")
        return artifacts

    base_after_buy = _account_balance(accounts_before_sell, target_currency)
    bought_delta = base_after_buy - base_before
    sell_volume = bought_delta * SELL_RATIO
    if sell_volume >= base_after_buy:
        sell_volume = base_after_buy * SELL_RATIO
    print(f"[매수 증가분] {target_currency}={bought_delta}")
    print(f"[매도 예정] 전량 매도 방지를 위해 증가분의 90%만 매도: {target_currency}={sell_volume}")

    if sell_volume <= 0:
        artifact, _ = _run_step(
            name="매수 증가분 일부 시장가 매도",
            slug="market_sell_partial_delta",
            method="POST",
            endpoint="/v2.1/order",
            params={
                "quote_currency": quote_currency,
                "target_currency": target_currency,
                "side": "SELL",
                "type": "MARKET",
                "qty": _decimal_text(sell_volume),
            },
            call=lambda: None,
            result_dir=result_dir,
            executed=False,
            skip_reason="매수 증가분이 없어 매도하지 않습니다.",
            sleep_seconds=request_sleep_seconds,
        )
        artifacts.append(artifact)
        return artifacts

    artifact, _ = _run_step(
        name="매도 전 현재가 조회",
        slug="price_before_sell",
        method="GET",
        endpoint=f"/public/v2/ticker_new/{quote_currency}/{target_currency}",
        params={},
        call=lambda: client.get_ticker(ticker),
        result_dir=result_dir,
        sleep_seconds=request_sleep_seconds,
    )
    artifacts.append(artifact)

    artifact, _ = _run_step(
        name="매수 증가분 일부 시장가 매도",
        slug="market_sell_partial_delta",
        method="POST",
        endpoint="/v2.1/order",
        params={
            "quote_currency": quote_currency,
            "target_currency": target_currency,
            "side": "SELL",
            "type": "MARKET",
            "qty": _decimal_text(sell_volume),
        },
        call=lambda: client.place_order(
            ticker=ticker,
            side="sell",
            order_type="market",
            volume=_decimal_text(sell_volume),
        ),
        result_dir=result_dir,
        sleep_seconds=request_sleep_seconds,
    )
    artifacts.append(artifact)
    if artifact.get("success") is not True:
        print("[경고] 매수는 성공했지만 매도가 실패했습니다.")
        return artifacts

    print(f"[대기] {settle_seconds}초 후 거래 후 잔고를 조회합니다.")
    time.sleep(settle_seconds)
    artifact, _ = _run_step(
        name="거래 후 잔고 조회",
        slug="accounts_after_trade",
        method="POST",
        endpoint="/v2.1/account/balance/all",
        params={},
        call=client.get_accounts,
        result_dir=result_dir,
        sleep_seconds=request_sleep_seconds,
    )
    artifacts.append(artifact)
    return artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description="Coinone Exchange API live tester.")
    parser.add_argument("--info", default="info.yaml")
    parser.add_argument(
        "--source",
        choices=["auto", "env", "keyring", "info_yaml"],
        default="auto",
    )
    parser.add_argument("--ticker", default="btc-krw")
    parser.add_argument("--base-currency", default="BTC")
    parser.add_argument("--transaction-id", default=None)
    parser.add_argument("--order-id", default=None)
    parser.add_argument("--user-order-id", default=None)
    parser.add_argument("--trade-krw", default="6000")
    parser.add_argument("--settle-seconds", type=float, default=2.0)
    parser.add_argument(
        "--request-sleep",
        type=float,
        default=1.0,
        help="seconds to sleep after each executed Exchange API request",
    )
    parser.add_argument("--execute-trade", action="store_true")
    parser.add_argument(
        "--result-dir",
        default="tests/results/coinone/exchange",
        help="directory where timestamped result artifacts are stored",
    )
    args = parser.parse_args()

    source: CredentialSource = args.source
    client = CoinoneRest.from_config(source=source, file_path=args.info)
    quote_currency, target_currency = _to_pair(args.ticker)
    if args.base_currency.upper() != target_currency:
        print(f"[참고] --base-currency={args.base_currency} 대신 ticker 기준 {target_currency}를 사용합니다.")
    trade_krw = _decimal(args.trade_krw)
    result_dir = _new_result_dir(PROJECT_ROOT / args.result_dir)

    print("[시작] 코인원 Exchange API 테스트")
    print(f"[설정] source={args.source}, ticker={args.ticker}")
    print(f"[설정] quote_currency={quote_currency}, target_currency={target_currency}")
    print(f"[설정] request_sleep={args.request_sleep}초")
    print(f"[저장 위치] {result_dir}")
    print("[안전장치] 출금/전량매도/전체주문취소 API는 실행하지 않습니다.")
    print("[저장 정책] 실제 응답을 tests/results 아래에 저장합니다. 이 경로는 Git에서 제외됩니다.")

    artifacts = _run_read_tests(
        client=client,
        ticker=args.ticker,
        quote_currency=quote_currency,
        target_currency=target_currency,
        transaction_id=args.transaction_id,
        order_id=args.order_id,
        user_order_id=args.user_order_id,
        result_dir=result_dir,
        request_sleep_seconds=args.request_sleep,
    )

    if args.execute_trade:
        artifacts.extend(
            _run_trade_test(
                client=client,
                ticker=args.ticker,
                quote_currency=quote_currency,
                target_currency=target_currency,
                trade_krw=trade_krw,
                settle_seconds=args.settle_seconds,
                result_dir=result_dir,
                request_sleep_seconds=args.request_sleep,
            )
        )
    else:
        artifact, _ = _run_step(
            name="실제 6000원 매수/일부 매도 테스트",
            slug="trade_round_trip_skipped",
            method="POST",
            endpoint="/v2.1/order",
            params={"trade_krw": str(trade_krw), "ticker": args.ticker},
            call=lambda: None,
            result_dir=result_dir,
            executed=False,
            skip_reason="--execute-trade가 지정되지 않았습니다.",
            sleep_seconds=args.request_sleep,
        )
        artifacts.append(artifact)

    summary = _build_summary(
        result_dir=result_dir,
        ticker=args.ticker,
        quote_currency=quote_currency,
        target_currency=target_currency,
        artifacts=artifacts,
    )
    _print_summary(summary)
    print(f"\n[요약 저장] {result_dir / 'summary.json'}")


if __name__ == "__main__":
    main()
