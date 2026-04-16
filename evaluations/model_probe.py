from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import anthropic
import openai

from just_another_coding_agent.auth import (
    resolve_openai_codex_oauth_credentials_sync,
    resolve_provider_secret,
)
from just_another_coding_agent.contracts.model_catalog import (
    shipped_models,
)

ProbeLane = Literal["openai-api", "openai-oauth", "anthropic-api"]
ProbeSource = Literal["shipped", "legacy_candidate", "cli"]
ProbeStatus = Literal[
    "ok",
    "bad_request",
    "auth_error",
    "api_status_error",
    "timeout",
    "client_error",
    "missing_credentials",
]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOTENV_PATH = REPO_ROOT / ".env"
DEFAULT_TIMEOUT_SECONDS = 30.0
OPENAI_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
OPENAI_PROBE_INSTRUCTIONS = "Reply with the single character X."
OPENAI_PROBE_MAX_OUTPUT_TOKENS = 16
LEGACY_OPENAI_MODEL_PROBE_IDS: tuple[str, ...] = tuple(
    f"openai-responses:{model_name}"
    for model_name in (
        "gpt-5-codex",
        "gpt-5-chatgpt",
        "gpt-5-mini-chatgpt",
    )
)


@dataclass(frozen=True)
class ProbeTarget:
    model_id: str
    lane: ProbeLane
    provider_model_name: str
    source: ProbeSource


@dataclass(frozen=True)
class ProbeResult:
    model_id: str
    lane: ProbeLane
    provider_model_name: str
    source: ProbeSource
    status: ProbeStatus
    http_status: int | None
    duration_ms: int | None
    detail: str | None


def load_dotenv_file(
    path: Path,
    *,
    environ: dict[str, str] | None = None,
    override: bool = False,
) -> None:
    target = os.environ if environ is None else environ
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if override or key not in target:
            target[key] = value


def infer_probe_target(model_id: str, *, source: ProbeSource) -> ProbeTarget:
    if model_id.startswith("anthropic:"):
        provider_model_name = model_id.split(":", 1)[1]
        return ProbeTarget(
            model_id=model_id,
            lane="anthropic-api",
            provider_model_name=provider_model_name,
            source=source,
        )
    if model_id.startswith("openai-responses:"):
        provider_model_name = model_id.split(":", 1)[1]
        if provider_model_name == "gpt-5-codex" or provider_model_name.endswith(
            "-chatgpt"
        ):
            if provider_model_name.endswith("-chatgpt"):
                provider_model_name = provider_model_name[: -len("-chatgpt")]
            return ProbeTarget(
                model_id=model_id,
                lane="openai-oauth",
                provider_model_name=provider_model_name,
                source=source,
            )
        return ProbeTarget(
            model_id=model_id,
            lane="openai-api",
            provider_model_name=provider_model_name,
            source=source,
        )
    raise ValueError(
        "Probe supports only canonical openai-responses:* and anthropic:* model ids: "
        f"{model_id}"
    )


def default_probe_targets(*, shipped_only: bool = False) -> tuple[ProbeTarget, ...]:
    targets = [
        infer_probe_target(model.model_id, source="shipped")
        for model in shipped_models()
    ]
    if shipped_only:
        return tuple(targets)
    for model_id in LEGACY_OPENAI_MODEL_PROBE_IDS:
        targets.append(infer_probe_target(model_id, source="legacy_candidate"))
    return tuple(targets)


def select_probe_targets(
    *,
    lane_filters: set[ProbeLane],
    explicit_models: Sequence[str],
    shipped_only: bool = False,
) -> tuple[ProbeTarget, ...]:
    if explicit_models:
        targets = [
            infer_probe_target(model_id, source="cli")
            for model_id in explicit_models
        ]
    else:
        targets = list(default_probe_targets(shipped_only=shipped_only))
    return tuple(target for target in targets if target.lane in lane_filters)


def probe_targets(
    targets: Sequence[ProbeTarget],
    *,
    timeout_seconds: float,
) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    openai_api_client: openai.OpenAI | None = None
    openai_oauth_client: openai.OpenAI | None = None
    anthropic_client: anthropic.Anthropic | None = None
    missing_lanes = missing_credentials_by_lane(targets)

    for target in targets:
        if target.lane in missing_lanes:
            results.append(
                ProbeResult(
                    model_id=target.model_id,
                    lane=target.lane,
                    provider_model_name=target.provider_model_name,
                    source=target.source,
                    status="missing_credentials",
                    http_status=None,
                    duration_ms=None,
                    detail=missing_lanes[target.lane],
                )
            )
            continue

        started = time.perf_counter()
        try:
            if target.lane == "openai-api":
                if openai_api_client is None:
                    openai_api_client = build_openai_api_client(
                        timeout_seconds=timeout_seconds
                    )
                results.append(
                    _probe_openai_target(
                        client=openai_api_client,
                        target=target,
                        started=started,
                    )
                )
                continue
            if target.lane == "openai-oauth":
                if openai_oauth_client is None:
                    openai_oauth_client = build_openai_oauth_client(
                        timeout_seconds=timeout_seconds
                    )
                results.append(
                    _probe_openai_target(
                        client=openai_oauth_client,
                        target=target,
                        started=started,
                    )
                )
                continue
            if anthropic_client is None:
                anthropic_client = build_anthropic_client(
                    timeout_seconds=timeout_seconds
                )
            results.append(
                _probe_anthropic_target(
                    client=anthropic_client,
                    target=target,
                    started=started,
                )
            )
        except TimeoutError as error:
            results.append(
                _timeout_result(
                    target=target,
                    started=started,
                    detail=str(error).strip() or "request timed out",
                )
            )
    return results


def missing_credentials_by_lane(
    targets: Sequence[ProbeTarget],
) -> dict[ProbeLane, str]:
    lanes = {target.lane for target in targets}
    missing: dict[ProbeLane, str] = {}
    if "openai-api" in lanes:
        secret = resolve_provider_secret("openai")
        if not secret:
            missing["openai-api"] = (
                "missing OpenAI API credentials; set OPENAI_API_KEY in the "
                "environment, "
                f"{DEFAULT_DOTENV_PATH}, or ~/.jaca/auth.json"
            )
    if "openai-oauth" in lanes:
        credentials = resolve_openai_codex_oauth_credentials_sync()
        if credentials is None:
            missing["openai-oauth"] = (
                "missing openai-codex OAuth credentials; run `/login openai-codex` or "
                "populate ~/.jaca/oauth.json"
            )
    if "anthropic-api" in lanes:
        secret = resolve_provider_secret("anthropic")
        if not secret:
            missing["anthropic-api"] = (
                "missing Anthropic API credentials; set ANTHROPIC_API_KEY in the "
                f"environment, {DEFAULT_DOTENV_PATH}, or ~/.jaca/auth.json"
            )
    return missing


def build_openai_api_client(*, timeout_seconds: float) -> openai.OpenAI:
    secret = resolve_provider_secret("openai")
    if not secret:
        raise RuntimeError("OpenAI API credentials are required")
    return openai.OpenAI(
        api_key=secret,
        base_url=os.environ.get("OPENAI_BASE_URL"),
        timeout=timeout_seconds,
        max_retries=0,
    )


def build_openai_oauth_client(*, timeout_seconds: float) -> openai.OpenAI:
    credentials = resolve_openai_codex_oauth_credentials_sync()
    if credentials is None:
        raise RuntimeError("openai-codex OAuth credentials are required")
    return openai.OpenAI(
        api_key=credentials.access,
        base_url=OPENAI_CODEX_BASE_URL,
        default_headers={
            "chatgpt-account-id": credentials.account_id,
            "originator": "jaca-model-probe",
            "OpenAI-Beta": "responses=experimental",
        },
        timeout=timeout_seconds,
        max_retries=0,
    )


def build_anthropic_client(*, timeout_seconds: float) -> anthropic.Anthropic:
    secret = resolve_provider_secret("anthropic")
    if not secret:
        raise RuntimeError("Anthropic API credentials are required")
    return anthropic.Anthropic(
        api_key=secret,
        timeout=timeout_seconds,
        max_retries=0,
    )


def _probe_openai_target(
    *,
    client: openai.OpenAI,
    target: ProbeTarget,
    started: float,
) -> ProbeResult:
    try:
        if target.lane == "openai-oauth":
            stream = client.responses.create(
                model=target.provider_model_name,
                instructions=OPENAI_PROBE_INSTRUCTIONS,
                input=_openai_probe_input(),
                store=False,
                stream=True,
            )
            try:
                next(iter(stream), None)
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
        else:
            client.responses.create(
                model=target.provider_model_name,
                instructions=OPENAI_PROBE_INSTRUCTIONS,
                input=_openai_probe_input(),
                max_output_tokens=OPENAI_PROBE_MAX_OUTPUT_TOKENS,
                store=False,
            )
        return _ok_result(target=target, started=started)
    except openai.BadRequestError as error:
        return _error_result(
            target=target,
            started=started,
            status="bad_request",
            http_status=error.status_code,
            detail=_exception_detail(error),
        )
    except openai.AuthenticationError as error:
        return _error_result(
            target=target,
            started=started,
            status="auth_error",
            http_status=error.status_code,
            detail=_exception_detail(error),
        )
    except openai.APIStatusError as error:
        return _error_result(
            target=target,
            started=started,
            status="api_status_error",
            http_status=error.status_code,
            detail=_exception_detail(error),
        )
    except openai.OpenAIError as error:
        return _error_result(
            target=target,
            started=started,
            status="client_error",
            http_status=None,
            detail=_exception_detail(error),
        )


def _probe_anthropic_target(
    *,
    client: anthropic.Anthropic,
    target: ProbeTarget,
    started: float,
) -> ProbeResult:
    try:
        client.messages.create(
            model=target.provider_model_name,
            max_tokens=1,
            messages=[
                {
                    "role": "user",
                    "content": "Reply with the single character X.",
                }
            ],
        )
        return _ok_result(target=target, started=started)
    except anthropic.BadRequestError as error:
        return _error_result(
            target=target,
            started=started,
            status="bad_request",
            http_status=error.status_code,
            detail=_exception_detail(error),
        )
    except anthropic.AuthenticationError as error:
        return _error_result(
            target=target,
            started=started,
            status="auth_error",
            http_status=error.status_code,
            detail=_exception_detail(error),
        )
    except anthropic.APIStatusError as error:
        return _error_result(
            target=target,
            started=started,
            status="api_status_error",
            http_status=error.status_code,
            detail=_exception_detail(error),
        )
    except anthropic.AnthropicError as error:
        return _error_result(
            target=target,
            started=started,
            status="client_error",
            http_status=None,
            detail=_exception_detail(error),
        )


def _ok_result(*, target: ProbeTarget, started: float) -> ProbeResult:
    return ProbeResult(
        model_id=target.model_id,
        lane=target.lane,
        provider_model_name=target.provider_model_name,
        source=target.source,
        status="ok",
        http_status=200,
        duration_ms=_duration_ms(started),
        detail=None,
    )


def _timeout_result(*, target: ProbeTarget, started: float, detail: str) -> ProbeResult:
    return ProbeResult(
        model_id=target.model_id,
        lane=target.lane,
        provider_model_name=target.provider_model_name,
        source=target.source,
        status="timeout",
        http_status=None,
        duration_ms=_duration_ms(started),
        detail=detail,
    )


def _error_result(
    *,
    target: ProbeTarget,
    started: float,
    status: ProbeStatus,
    http_status: int | None,
    detail: str,
) -> ProbeResult:
    return ProbeResult(
        model_id=target.model_id,
        lane=target.lane,
        provider_model_name=target.provider_model_name,
        source=target.source,
        status=status,
        http_status=http_status,
        duration_ms=_duration_ms(started),
        detail=detail,
    )


def _duration_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _exception_detail(error: Exception) -> str:
    message = getattr(error, "message", None)
    if isinstance(message, str) and message.strip():
        return message.strip()
    return str(error).strip() or error.__class__.__name__


def _openai_probe_input() -> list[dict[str, object]]:
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Reply with the single character X.",
                }
            ],
        }
    ]


def print_target_list(targets: Sequence[ProbeTarget]) -> None:
    for target in targets:
        print(
            f"{target.lane:14} {target.source:16} {target.model_id} "
            f"-> {target.provider_model_name}"
        )


def print_result_table(results: Sequence[ProbeResult]) -> None:
    for result in results:
        http_status = "-" if result.http_status is None else str(result.http_status)
        duration_ms = "-" if result.duration_ms is None else str(result.duration_ms)
        detail = "" if result.detail is None else f"  {result.detail}"
        print(
            f"{result.lane:14} {result.source:16} {result.status:18} "
            f"{http_status:4} {duration_ms:6}ms {result.model_id}{detail}"
        )


def print_result_json(results: Sequence[ProbeResult]) -> None:
    payload = [asdict(result) for result in results]
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="probe_model_support",
        description=(
            "Probe backend-owned model ids against live provider APIs to see whether "
            "they succeed or fail with 400/auth/other errors."
        ),
    )
    parser.add_argument(
        "--lane",
        action="append",
        choices=("openai-api", "openai-oauth", "anthropic-api"),
        help="Restrict probing to one or more credential lanes.",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        help=(
            "Canonical model id to probe. When omitted, the script probes the shipped "
            "catalog plus legacy GPT-5 OpenAI candidates."
        ),
    )
    parser.add_argument(
        "--shipped-only",
        action="store_true",
        help=(
            "When no explicit --model values are provided, skip legacy GPT-5 "
            "candidates."
        ),
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Print the probe target list without making network calls.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit results as JSON instead of a text table.",
    )
    parser.add_argument(
        "--skip-dotenv",
        action="store_true",
        help=f"Do not load {DEFAULT_DOTENV_PATH} before resolving credentials.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-request timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.skip_dotenv:
        load_dotenv_file(DEFAULT_DOTENV_PATH)
    lanes = set(args.lane or ("openai-api", "openai-oauth", "anthropic-api"))
    targets = select_probe_targets(
        lane_filters=lanes,
        explicit_models=args.model,
        shipped_only=args.shipped_only,
    )
    if not targets:
        print("No probe targets selected.", file=sys.stderr)
        return 2
    if args.list_only:
        print_target_list(targets)
        return 0

    results = probe_targets(targets, timeout_seconds=args.timeout_seconds)
    if args.json:
        print_result_json(results)
    else:
        print_result_table(results)

    statuses = {result.status for result in results}
    if "missing_credentials" in statuses:
        return 2
    if statuses == {"ok"}:
        return 0
    return 1


__all__ = [
    "DEFAULT_DOTENV_PATH",
    "LEGACY_OPENAI_MODEL_PROBE_IDS",
    "ProbeResult",
    "ProbeTarget",
    "default_probe_targets",
    "infer_probe_target",
    "load_dotenv_file",
    "main",
    "missing_credentials_by_lane",
    "parse_args",
    "probe_targets",
    "select_probe_targets",
]
