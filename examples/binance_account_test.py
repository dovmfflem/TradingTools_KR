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

from src.core.credentials import CredentialSource
from src.exchanges.binance.binance_futures_rest import BinanceFuturesRest
from src.exchanges.binance.binance_spot_rest import BinanceSpotRest


EXCHANGE = "binance"


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
    sleep_seconds: float,
) -> tuple[dict[str, Any], Any]:
    artifact: dict[str, Any] = {
        "exchange": EXCHANGE,
        "category": "account",
        "api_name": name,
        "method": method,
        "endpoint": endpoint,
        "params": params,
        "captured_at": _now_iso(),
        "executed": True,
    }

    print("\n" + "=" * 80)
    print(f"[요청 API] {name}")
    print(f"[HTTP] {method} {endpoint}")
    print(f"[파라미터] {_json(params)}")

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


def _build_summary(result_dir: Path, artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [
        {"api_name": item["api_name"], "method": item["method"], "endpoint": item["endpoint"]}
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
    summary = {
        "exchange": EXCHANGE,
        "category": "account",
        "captured_at": _now_iso(),
        "result_dir": str(result_dir),
        "executed_count": len(artifacts),
        "success_count": len(successful),
        "failure_count": len(failed),
        "successful": successful,
        "failed": failed,
    }
    _write_json(result_dir / "summary.json", summary)
    return summary


def _print_summary(summary: dict[str, Any]) -> None:
    print("\n" + "=" * 80)
    print("[요약] Binance Account API 테스트 결과")
    print(f"- 실행: {summary['executed_count']}개")
    print(f"- 성공: {summary['success_count']}개")
    print(f"- 실패: {summary['failure_count']}개")

    for title, key in (("성공한 API", "successful"), ("실패한 API", "failed")):
        print(f"\n[{title}]")
        items = summary[key]
        if not items:
            print("- 없음")
            continue
        for item in items:
            suffix = f" | {item['error']}" if item.get("error") else ""
            print(f"- {item['api_name']} ({item['method']} {item['endpoint']}){suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Binance spot/futures account balance live tester.")
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
    parser.add_argument(
        "--only",
        choices=["all", "spot", "futures"],
        default="all",
    )
    parser.add_argument("--include-zero-balances", action="store_true")
    parser.add_argument("--request-sleep", type=float, default=1.0)
    parser.add_argument(
        "--result-dir",
        default="tests/results/binance/account",
        help="directory where timestamped result artifacts are stored",
    )
    args = parser.parse_args()

    source: CredentialSource = args.source
    result_dir = _new_result_dir(PROJECT_ROOT / args.result_dir)

    print("[시작] Binance Account API 테스트")
    print(f"[설정] source={args.source}, only={args.only}")
    print(f"[설정] include_zero_balances={args.include_zero_balances}")
    print(f"[저장 위치] {result_dir}")
    print("[저장 정책] 실제 응답을 tests/results 아래에 저장합니다. 이 경로는 Git에서 제외됩니다.")

    artifacts: list[dict[str, Any]] = []
    if args.only in {"all", "spot"}:
        spot = BinanceSpotRest.from_config(
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
        artifact, _ = _run_step(
            name="현물 자산 조회",
            slug="spot_balances",
            method="GET",
            endpoint="/api/v3/account",
            params={"omitZeroBalances": not args.include_zero_balances},
            call=lambda: spot.get_balances(omit_zero_balances=not args.include_zero_balances),
            result_dir=result_dir,
            sleep_seconds=args.request_sleep,
        )
        artifacts.append(artifact)

    if args.only in {"all", "futures"}:
        futures = BinanceFuturesRest.from_config(
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
        artifact, _ = _run_step(
            name="USD-M 선물 자산 조회",
            slug="futures_balances",
            method="GET",
            endpoint="/fapi/v3/balance",
            params={},
            call=futures.get_balances,
            result_dir=result_dir,
            sleep_seconds=args.request_sleep,
        )
        artifacts.append(artifact)

    summary = _build_summary(result_dir, artifacts)
    _print_summary(summary)
    print(f"\n[요약 저장] {result_dir / 'summary.json'}")


if __name__ == "__main__":
    main()
