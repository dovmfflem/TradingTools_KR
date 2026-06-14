from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.exchanges.upbit.upbit_rest import UpbitRest, _to_market_code


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _slug(value: str) -> str:
    replacements = {
        "페어 목록 조회": "market_all",
        "초 캔들 조회": "candles_seconds",
        "분 캔들 조회": "candles_minutes",
        "일 캔들 조회": "candles_days",
        "주 캔들 조회": "candles_weeks",
        "월 캔들 조회": "candles_months",
        "연 캔들 조회": "candles_years",
        "페어 체결 이력 조회": "trades_ticks",
        "페어 단위 현재가 조회": "ticker_pair",
        "마켓 단위 현재가 조회": "ticker_quote",
        "호가 조회": "orderbook",
        "호가 정책 조회": "orderbook_instruments",
    }
    return replacements[value]


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


def _print_request(name: str, method: str, endpoint: str, params: dict[str, Any]) -> None:
    print("\n" + "=" * 80)
    print(f"[요청 API] {name}")
    print(f"[HTTP] {method} {endpoint}")
    print(f"[파라미터] {_json(params)}")


def _run_step(
    *,
    exchange: str,
    category: str,
    name: str,
    method: str,
    endpoint: str,
    params: dict[str, Any],
    call: Callable[[], Any],
    result_dir: Path | None,
) -> dict[str, Any]:
    _print_request(name, method, endpoint, params)
    captured_at = _now_iso()

    artifact: dict[str, Any] = {
        "exchange": exchange,
        "category": category,
        "api_name": name,
        "method": method,
        "endpoint": endpoint,
        "params": params,
        "captured_at": captured_at,
    }

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
    else:
        artifact.update({"success": True, "response": result})
        print(f"[성공] {name}")
        print("[결과]")
        print(_json(result))

    if result_dir is not None:
        file_name = f"{_slug(name)}.json"
        _write_json(result_dir / file_name, artifact)
        print(f"[저장] {result_dir / file_name}")

    return artifact


def _sleep(seconds: float) -> None:
    print(f"\n[대기] {seconds}초 후 다음 API를 호출합니다.")
    time.sleep(seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Upbit Quotation API live tester.")
    parser.add_argument("--api-key", default="quotation-only")
    parser.add_argument("--secret-key", default="quotation-only")
    parser.add_argument("--ticker", default="btc-krw")
    parser.add_argument("--quote-currency", default="KRW")
    parser.add_argument("--sleep", type=float, default=2.0)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--minute-unit", type=int, default=1)
    parser.add_argument(
        "--result-dir",
        default="tests/results/upbit/quotation",
        help="directory where timestamped result artifacts are stored",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="print results only without saving artifacts",
    )
    args = parser.parse_args()

    client = UpbitRest(api_key=args.api_key, secret_key=args.secret_key)
    market = _to_market_code(args.ticker)
    result_dir = None if args.no_save else _new_result_dir(PROJECT_ROOT / args.result_dir)

    print("[시작] 업비트 Quotation API 순차 테스트")
    print(
        f"[설정] ticker={args.ticker}, market={market}, "
        f"quote_currency={args.quote_currency}, count={args.count}"
    )
    print(f"[설정] sleep={args.sleep}초")
    print("[안내] Quotation API만 호출하므로 실제 업비트 인증정보가 필요하지 않습니다.")
    if result_dir is not None:
        print(f"[저장 위치] {result_dir}")

    steps: list[dict[str, Any]] = [
        {
            "name": "페어 목록 조회",
            "method": "GET",
            "endpoint": "/v1/market/all",
            "params": {"is_details": True},
            "call": lambda: client.list_trading_pairs(is_details=True)[: args.count],
        },
        {
            "name": "초 캔들 조회",
            "method": "GET",
            "endpoint": "/v1/candles/seconds",
            "params": {"market": market, "count": args.count},
            "call": lambda: client.get_second_candles(args.ticker, count=args.count),
        },
        {
            "name": "분 캔들 조회",
            "method": "GET",
            "endpoint": f"/v1/candles/minutes/{args.minute_unit}",
            "params": {
                "market": market,
                "unit": args.minute_unit,
                "count": args.count,
            },
            "call": lambda: client.get_minute_candles(
                args.ticker,
                unit=args.minute_unit,
                count=args.count,
            ),
        },
        {
            "name": "일 캔들 조회",
            "method": "GET",
            "endpoint": "/v1/candles/days",
            "params": {"market": market, "count": args.count},
            "call": lambda: client.get_day_candles(args.ticker, count=args.count),
        },
        {
            "name": "주 캔들 조회",
            "method": "GET",
            "endpoint": "/v1/candles/weeks",
            "params": {"market": market, "count": args.count},
            "call": lambda: client.get_week_candles(args.ticker, count=args.count),
        },
        {
            "name": "월 캔들 조회",
            "method": "GET",
            "endpoint": "/v1/candles/months",
            "params": {"market": market, "count": args.count},
            "call": lambda: client.get_month_candles(args.ticker, count=args.count),
        },
        {
            "name": "연 캔들 조회",
            "method": "GET",
            "endpoint": "/v1/candles/years",
            "params": {"market": market, "count": args.count},
            "call": lambda: client.get_year_candles(args.ticker, count=args.count),
        },
        {
            "name": "페어 체결 이력 조회",
            "method": "GET",
            "endpoint": "/v1/trades/ticks",
            "params": {"market": market, "count": args.count},
            "call": lambda: client.get_trades(args.ticker, count=args.count),
        },
        {
            "name": "페어 단위 현재가 조회",
            "method": "GET",
            "endpoint": "/v1/ticker",
            "params": {"markets": market},
            "call": lambda: client.get_ticker(args.ticker),
        },
        {
            "name": "마켓 단위 현재가 조회",
            "method": "GET",
            "endpoint": "/v1/ticker/all",
            "params": {"quote_currencies": args.quote_currency},
            "call": lambda: client.get_quote_tickers(args.quote_currency)[: args.count],
        },
        {
            "name": "호가 조회",
            "method": "GET",
            "endpoint": "/v1/orderbook",
            "params": {"markets": market, "count": args.count},
            "call": lambda: client.get_orderbook(args.ticker, count=args.count),
        },
        {
            "name": "호가 정책 조회",
            "method": "GET",
            "endpoint": "/v1/orderbook/instruments",
            "params": {"markets": market},
            "call": lambda: client.get_orderbook_policy(args.ticker),
        },
    ]

    artifacts: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        print(f"\n[진행] {index}/{len(steps)}")
        artifacts.append(
            _run_step(
                exchange="upbit",
                category="quotation",
                name=step["name"],
                method=step["method"],
                endpoint=step["endpoint"],
                params=step["params"],
                call=step["call"],
                result_dir=result_dir,
            )
        )
        if index < len(steps):
            _sleep(args.sleep)

    success_count = sum(1 for artifact in artifacts if artifact.get("success"))
    summary = {
        "exchange": "upbit",
        "category": "quotation",
        "captured_at": _now_iso(),
        "ticker": args.ticker,
        "market": market,
        "quote_currency": args.quote_currency,
        "count": args.count,
        "success_count": success_count,
        "failure_count": len(artifacts) - success_count,
        "artifacts": [
            {
                "api_name": artifact["api_name"],
                "endpoint": artifact["endpoint"],
                "success": artifact.get("success", False),
            }
            for artifact in artifacts
        ],
    }
    if result_dir is not None:
        _write_json(result_dir / "summary.json", summary)
        print(f"\n[요약 저장] {result_dir / 'summary.json'}")

    print("\n[완료] 업비트 Quotation API 순차 테스트 종료")
    print(f"[요약] 성공 {success_count}개, 실패 {len(artifacts) - success_count}개")


if __name__ == "__main__":
    main()
