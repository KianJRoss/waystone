#!/usr/bin/env python3
"""Run the Windows-selected pytest modules in separate processes.

pytest-timeout's portable thread watchdog terminates its *pytest process* when
it fires. A single uninterruptible test must therefore not own the process for
the entire suite. This runner keeps normal test-module grouping (so module/class
fixtures still work) and isolates the known Xodus cancellation module test by
test. A failed/timed-out group is reported and the remaining groups still run;
the overall exit code remains nonzero if any group failed.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time


def discover_files(paths: list[str]) -> list[Path]:
    found: set[Path] = set()
    for raw in paths:
        path = Path(raw)
        if path.is_file() and path.name.startswith("test_") and path.suffix == ".py":
            found.add(path)
        elif path.is_dir():
            found.update(p for p in path.rglob("test_*.py") if p.is_file())
    return sorted(found, key=lambda p: p.as_posix().casefold())


def pytest_prefix(timeout: int, pytest_args: list[str]) -> list[str]:
    return [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            f"--timeout={timeout}", "--durations=10", "-m", "not linux_only", *pytest_args]


def run_group(command: list[str], *, cwd: Path, env: dict[str, str], hard_timeout: int) -> tuple[int, str]:
    try:
        result = subprocess.run(command, cwd=cwd, env=env, encoding="utf-8", errors="replace",
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=hard_timeout, check=False)
        return result.returncode, result.stdout
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        return 124, output + f"\n[runner] isolated pytest group exceeded {hard_timeout}s hard ceiling\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=60, help="per-test pytest watchdog seconds")
    parser.add_argument("--hard-timeout", type=int, default=600, help="per-group parent-process ceiling")
    parser.add_argument("--isolate-file", action="append", default=[],
                        help="run each collected test in this file in its own pytest process")
    parser.add_argument("--list-only", action="store_true", help="print discovered test modules and exit")
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)
    root = Path.cwd()
    files = discover_files(args.paths)
    if not files:
        parser.error("no test_*.py files found")
    if args.list_only:
        print("\n".join(p.as_posix() for p in files))
        return 0

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    failed = []
    isolated = {Path(p) for p in args.isolate_file}
    prefix = pytest_prefix(args.timeout, [])
    for path in files:
        groups: list[str] = [path.as_posix()]
        if path in isolated:
            try:
                collect = subprocess.run(
                    [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", "not linux_only", path.as_posix()],
                    cwd=root, env=env, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    timeout=args.hard_timeout, check=False)
            except subprocess.TimeoutExpired as exc:
                output = exc.stdout or ""
                if isinstance(output, bytes):
                    output = output.decode("utf-8", "replace")
                failed.append((path.as_posix() + " (collection)", 124,
                               output + f"\n[runner] pytest collection exceeded {args.hard_timeout}s hard ceiling\n"))
                print(f"[FAIL 124] {path} (collection)", flush=True)
                if output:
                    print(output, flush=True)
                print(f"[runner] pytest collection exceeded {args.hard_timeout}s hard ceiling", flush=True)
                continue
            if collect.returncode:
                failed.append((path.as_posix() + " (collection)", collect.returncode, collect.stdout))
                print(f"[FAIL collection] {path}", flush=True)
                print(collect.stdout, flush=True)
                continue
            groups = [line.strip() for line in collect.stdout.splitlines()
                      if line.strip().startswith(path.as_posix() + "::")]
            if not groups:
                print(f"[PASS empty] {path}", flush=True)
                continue
        for group in groups:
            label = group
            print(f"[RUN] {label}", flush=True)
            started = time.monotonic()
            try:
                code, output = run_group([*prefix, group], cwd=root, env=env,
                                         hard_timeout=args.hard_timeout)
            except Exception as exc:  # the runner must never die on one group
                code, output = 125, f"[runner] could not run group: {exc!r}\n"
            elapsed = time.monotonic() - started
            if code:
                failed.append((label, code, output))
                print(f"[FAIL {code}] {label} ({elapsed:.0f}s)", flush=True)
                print(output, flush=True)
            else:
                summary = next((line.strip() for line in reversed(output.splitlines())
                                if " passed" in line or " skipped" in line), "passed")
                print(f"[PASS] {label}: {summary} ({elapsed:.0f}s)", flush=True)
    print(f"Windows pytest groups: {len(files)} modules, {len(failed)} failed/timed out", flush=True)
    for label, code, _output in failed:
        print(f"  FAILED [{code}] {label}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
