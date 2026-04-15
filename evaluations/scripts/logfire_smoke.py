"""Smoke-test that LOGFIRE_TOKEN and LOGFIRE_SERVICE_NAME are routed
correctly to the Logfire project this repo is pinned to.

Run it from the repo root *after* sourcing logfire_env.sh::

    . evaluations/scripts/logfire_env.sh
    uv run --with logfire python evaluations/scripts/logfire_smoke.py

It emits one span and one info log under
``service.name=${LOGFIRE_SERVICE_NAME:-jaca-harbor-smoke}``, then flushes
and exits. Within ~30 seconds the span should land in your Logfire
project so you can verify the credentials path is working.

The script exits non-zero if LOGFIRE_TOKEN isn't set at all — that's
the fast way to catch a missing or stale credentials file.
"""

from __future__ import annotations

import os
import sys
import time


def main() -> int:
    token = os.environ.get("LOGFIRE_TOKEN", "").strip()
    if not token:
        print(
            "LOGFIRE_TOKEN is not set. Source logfire_env.sh first, "
            "or run `logfire auth` and `logfire projects use <project>` "
            "inside the repo root.",
            file=sys.stderr,
        )
        return 2

    try:
        import logfire
    except ImportError:
        print(
            "logfire package not importable in this env. Re-run with "
            "`uv run --with logfire python evaluations/scripts/logfire_smoke.py`.",
            file=sys.stderr,
        )
        return 2

    service_name = os.environ.get("LOGFIRE_SERVICE_NAME", "jaca-harbor-smoke")

    try:
        console = logfire.ConsoleOptions(min_log_level="info")
    except Exception:
        console = True  # older SDKs accept a plain bool

    logfire.configure(
        send_to_logfire=True,
        console=console,
        service_name=service_name,
    )

    started = time.time()
    with logfire.span(
        "jaca.logfire_smoke",
        source="evaluations/scripts/logfire_smoke.py",
        user=os.environ.get("USER", "unknown"),
    ):
        logfire.info(
            "smoke test emitted",
            ts=started,
            service_name=service_name,
        )

    try:
        logfire.force_flush(timeout_millis=5000)
    except Exception:
        pass

    elapsed = time.time() - started
    print(
        f"logfire_smoke: emitted one span under service.name={service_name} "
        f"in {elapsed * 1000:.0f}ms. Check your project for a span named "
        f"`jaca.logfire_smoke`."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
