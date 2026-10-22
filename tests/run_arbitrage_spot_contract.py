"""Bounded, saved, synthetic-only focused verification (not a release run).

Run from the library root: python -B tests/run_arbitrage_spot_contract.py
Use --parent-positions once for the caller's requested valuation regression.
Each child has a 120-second deadline, no retries, and captured failure output.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULES = ["tests.test_bithumb_wallet", "tests.test_arbitrage_spot", "tests.test_arbitrage_depth", "tests.test_bithumb_spot_rest_contract",
           "tests.test_bithumb_api_surface", "tests.test_upbit_api_surface",
           "tests.test_spot_trading", "tests.test_order_authorization_error"]


def worker(parent=False):
    sys.path.insert(0, str(ROOT))
    if parent:
        sys.path.insert(0, str(ROOT.parents[1]))
        # Load by file to avoid collision with the library's tests package.
        suite = unittest.defaultTestLoader.discover(str(ROOT.parents[1] / "tests"),
                                                    pattern="test_overseas_positions_exchanges.py")
    else:
        suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")):
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


def run(parent=False):
    captured = datetime.now(timezone.utc)
    output = ROOT / "tests" / "results" / "arbitrage-spot"
    output.mkdir(parents=True, exist_ok=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                         text=True, timeout=5, check=True).stdout.strip()
    commands = [([sys.executable, "-B", str(Path(__file__).resolve()), "--worker"], ROOT),
                (["node", "--test", "tests/arbitrage-spot-depth.test.mjs", "tests/public-heartbeat.test.mjs"], ROOT)]
    if parent:
        commands = [([sys.executable, "-B", str(Path(__file__).resolve()), "--parent-worker"], ROOT.parents[1])]
    results = []
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    for command, cwd in commands:
        entry = {"command": command, "cwd": str(cwd), "deadline_seconds": 120, "state": "RUNNING"}
        try:
            result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=120)
            entry.update(state="HEALTHY" if result.returncode == 0 else "FAILED", exit_code=result.returncode,
                         stdout=result.stdout, stderr=result.stderr)
        except subprocess.TimeoutExpired as error:
            entry.update(state="TIMED_OUT", exit_code=None,
                         stdout=str(error.stdout or ""), stderr=str(error.stderr or ""))
        except OSError as error:
            entry.update(state="FAILED", exit_code=None, error=str(error))
        results.append(entry)
        print(json.dumps(entry, indent=2))
        if entry["state"] != "HEALTHY":
            break
    report = {"captured_at": captured.isoformat(), "library_head": sha, "dirty_worktree": True,
              "reason": "focused development contract verification", "live_api_calls": False,
              "service_state": "not applicable; synthetic tests", "restart_count": 0, "results": results}
    name = ("parent-positions" if parent else "contract") + "-" + captured.strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    path = output / name
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved: {path}")
    return 0 if all(entry["state"] == "HEALTHY" for entry in results) else 1


if __name__ == "__main__":
    if "--worker" in sys.argv or "--parent-worker" in sys.argv:
        raise SystemExit(worker("--parent-worker" in sys.argv))
    raise SystemExit(run("--parent-positions" in sys.argv))
