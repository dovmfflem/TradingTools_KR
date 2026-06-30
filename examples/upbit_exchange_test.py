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
from src.exchanges.upbit.upbit_rest import UpbitRest, _to_market_code


MAX_TRADE_KRW = Decimal("6000")


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


def _account_balance(accounts: list[dict[str, Any]], currency: str) -> Decimal:
    currency_upper = currency.upper()
    for account in accounts:
        if str(account.get("currency", "")).upper() == currency_upper:
            return _decimal(account.get("balance"))
    return Decimal("0")


def _find_deposit_address_pair(
    addresses: Any,
    *,
    preferred_currency: str,
    preferred_net_type: str,
) -> dict[str, str] | None:
    if not isinstance(addresses, list):
        return None

    preferred_currency_upper = preferred_currency.upper()
    preferred_net_type_upper = preferred_net_type.upper()

    for item in addresses:
        if not isinstance(item, dict):
            continue
        currency = str(item.get("currency") or "").upper()
        net_type = str(item.get("net_type") or "").upper()
        if not currency or not net_type:
            continue

        if currency == preferred_currency_upper and net_type == preferred_net_type_upper:
            return {"currency": currency, "net_type": net_type}

    return None


def _slug(value: str) -> str:
    replacements = {
        "계좌 조회": "accounts",
        "주문 가능 정보 조회": "order_chance",
        "미체결 주문 조회": "open_orders",
        "종료 주문 조회": "closed_orders",
        "지갑 상태 조회": "wallet_statuses",
        "API 키 조회": "api_keys",
        "출금 가능 정보 조회": "withdraw_chance",
        "출금 주소 조회": "withdraw_addresses",
        "입금 주소 목록 조회": "deposit_addresses",
        "입금 가능 정보 조회": "deposit_chance",
        "입금 주소 조회": "deposit_address",
        "주문 테스트 API": "order_test",
        "거래 전 현재가 조회": "price_before_trade",
        "거래 전 계좌 조회": "accounts_before_trade",
        "6000원 시장가 매수": "market_buy_6000",
        "매도 전 계좌 조회": "accounts_before_sell",
        "매도 전 현재가 조회": "price_before_sell",
        "테스트 매수분 시장가 매도": "market_sell_test_delta",
        "거래 후 계좌 조회": "accounts_after_trade",
        "실제 6000원 매수/매도 테스트": "trade_round_trip_skipped",
    }
    return replacements[value]


def _print_request(name: str, method: str, endpoint: str, params: dict[str, Any]) -> None:
    print("\n" + "=" * 80)
    print(f"[요청 API] {name}")
    print(f"[HTTP] {method} {endpoint}")
    print(f"[파라미터] {_json(params)}")


def _sleep_after_request(seconds: float) -> None:
    if seconds <= 0:
        return
    print(f"\n[대기] {seconds:.1f}초 후 다음 단계를 진행합니다.")
    time.sleep(seconds)


def _run_step(
    *,
    name: str,
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
        "exchange": "upbit",
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
        _write_json(result_dir / f"{_slug(name)}.json", artifact)
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
        _write_json(result_dir / f"{_slug(name)}.json", artifact)
        print(f"[저장] {result_dir / f'{_slug(name)}.json'}")
        _sleep_after_request(sleep_seconds)
        return artifact, error

    artifact.update({"success": True, "response": result})
    print(f"[성공] {name}")
    print("[결과]")
    print(_json(result))
    _write_json(result_dir / f"{_slug(name)}.json", artifact)
    print(f"[저장] {result_dir / f'{_slug(name)}.json'}")
    _sleep_after_request(sleep_seconds)
    return artifact, result


def _print_summary(summary: dict[str, Any]) -> None:
    print("\n" + "=" * 80)
    print("[요약] 업비트 Exchange API 테스트 결과")
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


def _build_summary(
    *,
    result_dir: Path,
    ticker: str,
    market: str,
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
        "exchange": "upbit",
        "category": "exchange",
        "captured_at": _now_iso(),
        "ticker": ticker,
        "market": market,
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


def _run_read_tests(
    *,
    client: UpbitRest,
    ticker: str,
    market: str,
    base_currency: str,
    net_type: str,
    deposit_currency: str,
    deposit_net_type: str,
    result_dir: Path,
    request_sleep_seconds: float,
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = [
        {
            "name": "계좌 조회",
            "method": "GET",
            "endpoint": "/v1/accounts",
            "params": {},
            "call": client.get_accounts,
        },
        {
            "name": "주문 가능 정보 조회",
            "method": "GET",
            "endpoint": "/v1/orders/chance",
            "params": {"market": market},
            "call": lambda: client.get_order_chance(ticker),
        },
        {
            "name": "미체결 주문 조회",
            "method": "GET",
            "endpoint": "/v1/orders/open",
            "params": {"market": market, "limit": 3},
            "call": lambda: client.list_open_orders(ticker=ticker, limit=3),
        },
        {
            "name": "종료 주문 조회",
            "method": "GET",
            "endpoint": "/v1/orders/closed",
            "params": {"market": market, "limit": 3},
            "call": lambda: client.list_closed_orders(ticker=ticker, limit=3),
        },
        {
            "name": "지갑 상태 조회",
            "method": "GET",
            "endpoint": "/v1/status/wallet",
            "params": {},
            "call": client.list_wallet_statuses,
        },
        {
            "name": "API 키 조회",
            "method": "GET",
            "endpoint": "/v1/api_keys",
            "params": {},
            "call": client.list_api_keys,
        },
        {
            "name": "출금 가능 정보 조회",
            "method": "GET",
            "endpoint": "/v1/withdraws/chance",
            "params": {"currency": base_currency, "net_type": net_type},
            "call": lambda: client.get_withdraw_chance(currency=base_currency, net_type=net_type),
        },
        {
            "name": "출금 주소 조회",
            "method": "GET",
            "endpoint": "/v1/withdraws/coin_addresses",
            "params": {"currency": base_currency, "net_type": net_type},
            "call": lambda: client.list_withdraw_addresses(currency=base_currency, net_type=net_type),
        },
        {
            "name": "입금 주소 목록 조회",
            "method": "GET",
            "endpoint": "/v1/deposits/coin_addresses",
            "params": {"currency": deposit_currency, "net_type": deposit_net_type},
            "call": lambda: client.list_deposit_addresses(
                currency=deposit_currency,
                net_type=deposit_net_type,
            ),
        },
        {
            "name": "입금 가능 정보 조회",
            "method": "GET",
            "endpoint": "/v1/deposits/chance/coin",
            "params": {"currency": deposit_currency, "net_type": deposit_net_type},
            "call": lambda: client.get_deposit_chance(
                currency=deposit_currency,
                net_type=deposit_net_type,
            ),
        },
        {
            "name": "입금 주소 조회",
            "method": "GET",
            "endpoint": "/v1/deposits/coin_address",
            "params": {"currency": deposit_currency, "net_type": deposit_net_type},
            "call": lambda: client.get_deposit_address(
                currency=deposit_currency,
                net_type=deposit_net_type,
            ),
        },
        {
            "name": "주문 테스트 API",
            "method": "POST",
            "endpoint": "/v1/orders/test",
            "params": {"market": market, "side": "bid", "ord_type": "price", "price": "6000"},
            "call": lambda: client.test_order(
                ticker=ticker,
                side="bid",
                order_type="price",
                price="6000",
            ),
        },
    ]

    artifacts: list[dict[str, Any]] = []
    deposit_address_pair: dict[str, str] | None = {
        "currency": deposit_currency.upper(),
        "net_type": deposit_net_type.upper(),
    }
    deposit_address_list_checked = False

    for step in steps:
        params = step["params"]
        call = step["call"]
        executed = True
        skip_reason = None

        if step["name"] == "입금 주소 조회":
            if deposit_address_pair is None:
                executed = False
                skip_reason = (
                    f"입금 주소 목록에 {deposit_currency.upper()}/"
                    f"{deposit_net_type.upper()} 주소가 없어 단건 주소 조회를 건너뜁니다."
                )
                params = {
                    "currency": deposit_currency.upper(),
                    "net_type": deposit_net_type.upper(),
                }
                call = lambda: None
            else:
                selected_currency = deposit_address_pair["currency"]
                selected_net_type = deposit_address_pair["net_type"]
                params = {
                    "currency": selected_currency,
                    "net_type": selected_net_type,
                }
                call = lambda c=selected_currency, n=selected_net_type: client.get_deposit_address(
                    currency=c,
                    net_type=n,
                )

        artifact, result = _run_step(
            name=step["name"],
            method=step["method"],
            endpoint=step["endpoint"],
            params=params,
            call=call,
            result_dir=result_dir,
            executed=executed,
            skip_reason=skip_reason,
            sleep_seconds=request_sleep_seconds,
        )
        artifacts.append(artifact)

        if step["name"] == "입금 주소 목록 조회":
            deposit_address_list_checked = True
            if artifact.get("success") is True:
                deposit_address_pair = _find_deposit_address_pair(
                    result,
                    preferred_currency=deposit_currency,
                    preferred_net_type=deposit_net_type,
                )
                if deposit_address_pair is None:
                    print(
                        "[참고] 입금 주소 목록에 "
                        f"{deposit_currency.upper()}/{deposit_net_type.upper()} 주소가 없습니다."
                    )

    return artifacts


def _run_deposit_address_only(
    *,
    client: UpbitRest,
    deposit_currency: str,
    deposit_net_type: str,
    result_dir: Path,
    request_sleep_seconds: float,
) -> list[dict[str, Any]]:
    artifact, _ = _run_step(
        name="입금 주소 조회",
        method="GET",
        endpoint="/v1/deposits/coin_address",
        params={
            "currency": deposit_currency.upper(),
            "net_type": deposit_net_type.upper(),
        },
        call=lambda: client.get_deposit_address(
            currency=deposit_currency.upper(),
            net_type=deposit_net_type.upper(),
        ),
        result_dir=result_dir,
        sleep_seconds=request_sleep_seconds,
    )
    return [artifact]


def _run_trade_test(
    *,
    client: UpbitRest,
    ticker: str,
    market: str,
    base_currency: str,
    trade_krw: Decimal,
    settle_seconds: float,
    result_dir: Path,
    request_sleep_seconds: float,
) -> list[dict[str, Any]]:
    if trade_krw != MAX_TRADE_KRW:
        raise ValueError("trade_krw must be exactly 6000")

    artifacts: list[dict[str, Any]] = []
    print("\n[거래 테스트] 6000원 시장가 매수 후 테스트 매수분만 시장가 매도합니다.")
    artifact, _ = _run_step(
        name="거래 전 현재가 조회",
        method="GET",
        endpoint="/v1/ticker",
        params={"markets": market},
        call=lambda: client.get_ticker(ticker),
        result_dir=result_dir,
        sleep_seconds=request_sleep_seconds,
    )
    artifacts.append(artifact)

    artifact, accounts_before = _run_step(
        name="거래 전 계좌 조회",
        method="GET",
        endpoint="/v1/accounts",
        params={},
        call=client.get_accounts,
        result_dir=result_dir,
        sleep_seconds=request_sleep_seconds,
    )
    artifacts.append(artifact)
    if artifact.get("success") is not True or not isinstance(accounts_before, list):
        print("[중단] 거래 전 계좌 조회 실패")
        return artifacts

    base_before = _account_balance(accounts_before, base_currency)
    krw_before = _account_balance(accounts_before, "KRW")
    print(f"[잔고] 거래 전 KRW={krw_before}, {base_currency}={base_before}")

    if krw_before < trade_krw:
        artifact, _ = _run_step(
            name="6000원 시장가 매수",
            method="POST",
            endpoint="/v1/orders",
            params={"market": market, "side": "bid", "ord_type": "price", "price": str(trade_krw)},
            call=lambda: None,
            result_dir=result_dir,
            executed=False,
            skip_reason=f"KRW 잔고가 {trade_krw}보다 작습니다.",
            sleep_seconds=request_sleep_seconds,
        )
        artifacts.append(artifact)
        return artifacts

    artifact, _ = _run_step(
        name="6000원 시장가 매수",
        method="POST",
        endpoint="/v1/orders",
        params={"market": market, "side": "bid", "ord_type": "price", "price": str(trade_krw)},
        call=lambda: client.place_order(
            ticker=ticker,
            side="bid",
            order_type="price",
            price=str(trade_krw),
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
        name="매도 전 계좌 조회",
        method="GET",
        endpoint="/v1/accounts",
        params={},
        call=client.get_accounts,
        result_dir=result_dir,
        sleep_seconds=request_sleep_seconds,
    )
    artifacts.append(artifact)
    if artifact.get("success") is not True or not isinstance(accounts_before_sell, list):
        print("[중단] 매도 전 계좌 조회 실패")
        return artifacts

    base_after_buy = _account_balance(accounts_before_sell, base_currency)
    bought_delta = base_after_buy - base_before
    print(f"[매수 증가분] {base_currency}={bought_delta}")

    if bought_delta <= 0:
        artifact, _ = _run_step(
            name="테스트 매수분 시장가 매도",
            method="POST",
            endpoint="/v1/orders",
            params={"market": market, "side": "ask", "ord_type": "market", "volume": str(bought_delta)},
            call=lambda: None,
            result_dir=result_dir,
            executed=False,
            skip_reason="매수 증가분이 없어 매도하지 않습니다.",
            sleep_seconds=request_sleep_seconds,
        )
        artifacts.append(artifact)
        return artifacts
    if bought_delta > base_after_buy:
        artifact, _ = _run_step(
            name="테스트 매수분 시장가 매도",
            method="POST",
            endpoint="/v1/orders",
            params={"market": market, "side": "ask", "ord_type": "market", "volume": str(bought_delta)},
            call=lambda: None,
            result_dir=result_dir,
            executed=False,
            skip_reason="계산된 매도 수량이 현재 잔고보다 커서 매도하지 않습니다.",
            sleep_seconds=request_sleep_seconds,
        )
        artifacts.append(artifact)
        return artifacts

    artifact, _ = _run_step(
        name="매도 전 현재가 조회",
        method="GET",
        endpoint="/v1/ticker",
        params={"markets": market},
        call=lambda: client.get_ticker(ticker),
        result_dir=result_dir,
        sleep_seconds=request_sleep_seconds,
    )
    artifacts.append(artifact)

    artifact, _ = _run_step(
        name="테스트 매수분 시장가 매도",
        method="POST",
        endpoint="/v1/orders",
        params={"market": market, "side": "ask", "ord_type": "market", "volume": str(bought_delta)},
        call=lambda: client.place_order(
            ticker=ticker,
            side="ask",
            order_type="market",
            volume=str(bought_delta),
        ),
        result_dir=result_dir,
        sleep_seconds=request_sleep_seconds,
    )
    artifacts.append(artifact)
    if artifact.get("success") is not True:
        print("[경고] 매수는 성공했지만 매도가 실패했습니다.")
        return artifacts

    print(f"[대기] {settle_seconds}초 후 거래 후 계좌를 조회합니다.")
    time.sleep(settle_seconds)
    artifact, _ = _run_step(
        name="거래 후 계좌 조회",
        method="GET",
        endpoint="/v1/accounts",
        params={},
        call=client.get_accounts,
        result_dir=result_dir,
        sleep_seconds=request_sleep_seconds,
    )
    artifacts.append(artifact)
    return artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description="Upbit Exchange API live tester.")
    parser.add_argument("--info", default="info.yaml")
    parser.add_argument(
        "--source",
        choices=["auto", "env", "keyring", "info_yaml"],
        default="auto",
    )
    parser.add_argument("--env-prefix", default=None)
    parser.add_argument("--env-primary", default=None)
    parser.add_argument("--env-secret", default=None)
    parser.add_argument("--yaml-primary", default=None)
    parser.add_argument("--yaml-secret", default=None)
    parser.add_argument("--keyring-primary", default=None)
    parser.add_argument("--keyring-secret", default=None)
    parser.add_argument("--keyring-service", default="TradingTools_KR")
    parser.add_argument("--ticker", default="btc-krw")
    parser.add_argument("--base-currency", default="BTC")
    parser.add_argument("--net-type", default="BTC")
    parser.add_argument("--deposit-currency", default=None)
    parser.add_argument("--deposit-net-type", default=None)
    parser.add_argument(
        "--only",
        choices=["all", "deposit_address"],
        default="all",
        help="run only one Exchange API test group",
    )
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
        default="tests/results/upbit/exchange",
        help="directory where timestamped result artifacts are stored",
    )
    args = parser.parse_args()

    source: CredentialSource = args.source
    client = UpbitRest.from_config(
        source=source,
        file_path=args.info,
        env_prefix=args.env_prefix,
        env_primary=args.env_primary,
        env_secret=args.env_secret,
        yaml_primary=args.yaml_primary,
        yaml_secret=args.yaml_secret,
        keyring_primary=args.keyring_primary,
        keyring_secret=args.keyring_secret,
        keyring_service=args.keyring_service,
    )
    market = _to_market_code(args.ticker)
    trade_krw = _decimal(args.trade_krw)
    deposit_currency = (args.deposit_currency or args.base_currency).upper()
    deposit_net_type = (args.deposit_net_type or args.net_type).upper()
    result_dir = _new_result_dir(PROJECT_ROOT / args.result_dir)

    print("[시작] 업비트 Exchange API 테스트")
    print(f"[설정] source={args.source}, ticker={args.ticker}, market={market}")
    print(f"[설정] base_currency={args.base_currency}, net_type={args.net_type}")
    print(f"[설정] deposit_currency={deposit_currency}, deposit_net_type={deposit_net_type}")
    print(f"[설정] only={args.only}")
    print(f"[설정] request_sleep={args.request_sleep}초")
    print(f"[저장 위치] {result_dir}")
    print("[안전장치] 출금/전송/전량매도/일괄취소 API는 실행하지 않습니다.")
    print("[저장 정책] 이 스크립트는 실제 응답 값을 그대로 출력하고 tests/results 아래에 저장합니다.")

    if args.only == "deposit_address":
        artifacts = _run_deposit_address_only(
            client=client,
            deposit_currency=deposit_currency,
            deposit_net_type=deposit_net_type,
            result_dir=result_dir,
            request_sleep_seconds=args.request_sleep,
        )
    else:
        artifacts = _run_read_tests(
            client=client,
            ticker=args.ticker,
            market=market,
            base_currency=args.base_currency,
            net_type=args.net_type,
            deposit_currency=deposit_currency,
            deposit_net_type=deposit_net_type,
            result_dir=result_dir,
            request_sleep_seconds=args.request_sleep,
        )

    if args.only == "all" and args.execute_trade:
        artifacts.extend(
            _run_trade_test(
                client=client,
                ticker=args.ticker,
                market=market,
                base_currency=args.base_currency,
                trade_krw=trade_krw,
                settle_seconds=args.settle_seconds,
                result_dir=result_dir,
                request_sleep_seconds=args.request_sleep,
            )
        )
    elif args.only == "all":
        artifact, _ = _run_step(
            name="실제 6000원 매수/매도 테스트",
            method="POST",
            endpoint="/v1/orders",
            params={"trade_krw": str(trade_krw), "market": market},
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
        market=market,
        artifacts=artifacts,
    )
    _print_summary(summary)
    print(f"\n[요약 저장] {result_dir / 'summary.json'}")


if __name__ == "__main__":
    main()
