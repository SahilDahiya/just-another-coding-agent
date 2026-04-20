from __future__ import annotations

import asyncio
import os
import socket
import time

import pytest

import just_another_coding_agent.oauth_openai_codex as oauth_openai_codex
from just_another_coding_agent.oauth_openai_codex import (
    OpenAICodexCredentials,
    start_openai_codex_login,
    wait_for_openai_codex_callback,
)


def _require_loopback_tcp() -> None:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except PermissionError:
        pytest.skip("TCP loopback sockets are not permitted in this environment")
    sock.close()


async def test_wait_for_openai_codex_callback_returns_promptly_after_callback(
    monkeypatch,
) -> None:
    _require_loopback_tcp()
    callback_host = "127.0.0.1"
    callback_port = 20000 + (os.getpid() % 20000)

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
    monkeypatch.setattr(oauth_openai_codex, "_CALLBACK_HOST", callback_host)
    monkeypatch.setattr(oauth_openai_codex, "_CALLBACK_PORT", callback_port)
    monkeypatch.setattr(
        oauth_openai_codex,
        "_REDIRECT_URI",
        f"http://{callback_host}:{callback_port}/auth/callback",
    )

    flow, _start = start_openai_codex_login()
    wait_task = asyncio.create_task(
        wait_for_openai_codex_callback(flow, timeout_seconds=5.0)
    )

    await asyncio.sleep(0.05)

    reader, writer = await asyncio.open_connection(callback_host, callback_port)
    writer.write(
        (
            "GET /auth/callback?code=code-123&state="
            f"{flow.state} HTTP/1.1\r\n"
            f"Host: {callback_host}\r\n"
            "\r\n"
        ).encode("ascii")
    )
    await writer.drain()
    response = await asyncio.wait_for(
        reader.readuntil(b"</html>"),
        timeout=1.0,
    )
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
