#!/usr/bin/env python3
"""Run the travel app's offline QA checks and collect per-run evidence."""

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
PYTEST_EXCLUSIONS = (
    "not test_plan_stream_saves_history_when_logged_in "
    "and not test_graph_compiled_and_flows"
)
BROWSER_SUITES = ("trip-home", "plan-creation", "workspace")


@dataclass(frozen=True)
class Check:
    name: str
    cwd: Path
    command: list[str]
    junit: Path | None = None


def checks_for(profile: str, output: Path) -> list[Check]:
    checks = []
    if profile in {"backend", "all"}:
        junit = output / "backend.xml"
        checks.append(Check("backend", BACKEND, [
            sys.executable, "-m", "pytest", "tests", "-q", "--tb=short",
            "--ignore=tests/test_m1.py", "--ignore=tests/test_m2.py",
            "-k", PYTEST_EXCLUSIONS, f"--junitxml={junit}",
        ], junit))
    if profile in {"frontend", "all"}:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        tests = sorted(str(path.relative_to(FRONTEND))
                       for path in (FRONTEND / "test").glob("*.test.js"))
        if not tests:
            raise ValueError("No frontend/test/*.test.js files found")
        junit = output / "frontend-unit.xml"
        checks.append(Check("frontend-unit", FRONTEND, [
            "node", "--test", "--test-reporter=spec", "--test-reporter-destination=stdout",
            "--test-reporter=junit", f"--test-reporter-destination={junit}", *tests,
        ], junit))
        checks.append(Check("frontend-build", FRONTEND, [npm, "run", "build:travel"]))
    if profile in {"browser", "all"}:
        for suite in BROWSER_SUITES:
            name = f"browser-{suite}"
            checks.append(Check(name, FRONTEND, [
                "node", "node_modules/@playwright/test/cli.js", "test",
                "--config", f"playwright.{suite}.config.mjs", "--reporter=line,junit",
                "--output", str(output / name),
            ], output / f"{name}.xml"))
    return checks


def checkout_state() -> dict:
    def git(*args):
        try:
            result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
        except FileNotFoundError:
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    revision = git("rev-parse", "HEAD")
    status = git("status", "--porcelain") if revision else None
    return {"commit": revision, "dirty": bool(status) if status is not None else None}


def test_counts(report: Path) -> dict[str, int]:
    cases = ET.parse(report).getroot().findall(".//testcase")
    skipped = sum(case.find("skipped") is not None for case in cases)
    failed = sum(case.find("failure") is not None or case.find("error") is not None
                 for case in cases)
    return {"total": len(cases), "passed": len(cases) - skipped - failed,
            "failed": failed, "skipped": skipped}


def run_check(check: Check, output: Path, environment: dict[str, str]) -> dict:
    log = output / f"{check.name}.log"
    env = environment.copy()
    if check.name.startswith("browser-"):
        env["PLAYWRIGHT_JUNIT_OUTPUT_FILE"] = str(check.junit)
    started = time.monotonic()
    entry = {"name": check.name, "log": log.name}
    print(f"Running {check.name}", flush=True)
    with log.open("wb") as handle:
        try:
            result = subprocess.run(check.command, cwd=check.cwd, env=env,
                                    stdout=handle, stderr=subprocess.STDOUT)
            entry["exit_code"] = result.returncode
        except OSError as error:
            entry["exit_code"] = None
            entry["error"] = str(error)
            handle.write((str(error) + "\n").encode("utf-8"))
    if check.junit:
        entry["junit"] = check.junit.name
        try:
            entry["tests"] = test_counts(check.junit)
            if not entry["tests"]["passed"] and not entry["tests"]["failed"]:
                entry["error"] = "No tests executed"
        except (OSError, ET.ParseError) as error:
            entry.setdefault("error", f"Missing or invalid JUnit report: {error}")
    entry["seconds"] = round(time.monotonic() - started, 2)
    entry["status"] = (
        "passed" if entry["exit_code"] == 0 and "error" not in entry
        and not entry.get("tests", {}).get("failed") else "failed"
    )
    print(f"{check.name}: {entry['status']} ({entry['seconds']}s)", flush=True)
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=("backend", "frontend", "browser", "all"))
    parser.add_argument("--report-dir", type=Path, help="New directory for QA reports")
    args = parser.parse_args()

    git_state = checkout_state()
    try:
        output = (args.report_dir.resolve() if args.report_dir else
                  Path(tempfile.mkdtemp(prefix="travel-qa-")))
        if args.report_dir:
            output.mkdir(parents=True, exist_ok=False)
        checks = checks_for(args.profile, output)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    with tempfile.TemporaryDirectory(prefix="travel-qa-config-") as config_dir:
        env_file = Path(config_dir) / "offline.env"
        env_file.touch()
        environment = dict(os.environ, TRAVEL_ENV_FILE=str(env_file),
                           TRAVEL_E2E_PYTHON=sys.executable,
                           ZHIPU_API_KEY="qa-placeholder", AMAP_API_KEY="", AMAP_JS_KEY="",
                           AMAP_JS_SECURITY_CODE="", QWEATHER_API_KEY="", QWEATHER_API_HOST="",
                           VARIFLIGHT_API_KEY="", JWT_SECRET="", TRAVEL_PROVIDER_BUDGET="0",
                           TRAVEL_SHARED_CACHE="0", TRAVEL_SHARED_LIMITS="0")
        results = [run_check(check, output, environment) for check in checks]

    summary = {
        "profile": args.profile, **git_state,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "browser_channel": os.environ.get("TRAVEL_E2E_CHANNEL", "msedge"),
        },
        "checks": results,
    }
    report = output / "summary.json"
    report.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"QA report: {report}")
    return int(any(check["status"] == "failed" for check in results))


if __name__ == "__main__":
    sys.exit(main())
