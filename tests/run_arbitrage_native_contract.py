"""Focused development verification only, 120s absolute child deadline.

python -B tests/run_arbitrage_native_contract.py
Always saves success/failure/timeout output; no retries or release build.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run():
    stamp = datetime.now(timezone.utc)
    command = [sys.executable, "-B", "-m", "unittest", "-v",
        "tests.test_arbitrage_native_spot", "tests.test_arbitrage_fill_evidence", "tests.test_arbitrage_native_depth", "tests.test_arbitrage_spot",
        "tests.test_arbitrage_depth", "tests.test_spot_trading",
        "tests.test_public_grid_orderbooks", "tests.test_order_authorization_error",
        "tests.test_global_spot_wallet", "tests.test_korbit_candles"]
    report = dict(captured_at=stamp.isoformat(), reason="focused adapter development; not release validation",
                  live_api_calls=False, deadline_seconds=120, state="RUNNING", command=command,
                  service_state="not applicable; synthetic tests", restart_count=0)
    report["source_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
        "src/exchanges/arbitrage_spot.py", "src/exchanges/arbitrage_native_spot.py", "src/exchanges/arbitrage_depth.py",
        "src/exchanges/arbitrage_timeout.py",
        "src/exchanges/arbitrage_fill_evidence.py", "tests/test_arbitrage_fill_evidence.py",
        "src/exchanges/global_spot_wallet.py", "tests/test_global_spot_wallet.py", "tests/test_korbit_candles.py",
        "src/exchanges/coinone/coinone_rest.py", "src/exchanges/korbit/korbit_rest.py",
        "tests/test_arbitrage_native_spot.py")}
    try:
        report["library_head"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=120)
        report.update(state="HEALTHY" if result.returncode == 0 else "FAILED",
                      exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    except subprocess.TimeoutExpired as error:
        report.update(state="TIMED_OUT", exit_code=None,
                      stdout=str(error.stdout or ""), stderr=str(error.stderr or ""))
    except (OSError, subprocess.CalledProcessError) as error:
        report.update(state="FAILED", exit_code=None, error=str(error))
    root = ROOT / "tests" / "results" / "arbitrage-native-spot"
    root.mkdir(parents=True, exist_ok=True)
    path = root / ("contract-" + stamp.strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Saved: {path}")
    return 0 if report["state"] == "HEALTHY" else 1


if __name__ == "__main__":
    raise SystemExit(run())
