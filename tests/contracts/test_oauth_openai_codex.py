from __future__ import annotations

import asyncio
import time

from just_another_coding_agent.oauth_openai_codex import (
    OpenAICodexCredentials,
    start_openai_codex_login,
    wait_for_openai_codex_callback,
)


async def test_wait_for_openai_codex_callback_returns_promptly_after_callback(
    monkeypatch,
) -> None:
    async def _exchange(*, code: str, verifier: str) -> OpenAICodexCredentials:
        assert code == "code-123"
        assert verifier
        return OpenAICodexCredentials(
            access="a.b.c",
            refresh="refresh-token",
            expires=1760000000000,
            account_id="acct-123",
        )

    monkeypatch.setattr(
        "just_another_coding_agent.oauth_openai_codex._exchange_authorization_code",
        _exchange,
    )

    flow, _start = start_openai_codex_login()
    wait_task = asyncio.create_task(
        wait_for_openai_codex_callback(flow, timeout_seconds=5.0)
    )

    await asyncio.sleep(0.05)

    reader, writer = await asyncio.open_connection("localhost", 1455)
    writer.write(
        (
            "GET /auth/callback?code=code-123&state="
            f"{flow.state} HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "\r\n"
        ).encode("ascii")
    )
    await writer.drain()
    response = await reader.read()
    browser_done = time.monotonic()
    writer.close()
    await writer.wait_closed()

    body = response.decode("utf-8", errors="replace")
    assert "Login complete" in body
    assert "Copy code" in body
    assert "code-123" in body

    credentials = await asyncio.wait_for(wait_task, timeout=1.0)
    assert credentials.account_id == "acct-123"
    assert (time.monotonic() - browser_done) < 0.25
