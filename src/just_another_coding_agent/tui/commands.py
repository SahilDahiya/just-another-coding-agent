"""Slash-command helpers for the interactive TUI."""

from __future__ import annotations

import os

from .config import save_provider_config
from .widgets import TranscriptLog


def start_note_block(output: TranscriptLog, title: str) -> None:
    """Open a compact informational block in the transcript."""
    output.ensure_block_gap()
    output.write_line(f"note  {title}")


def write_help(output: TranscriptLog) -> None:
    """Render slash-command help text."""
    start_note_block(output, "commands")
    output.write_line("  /help              show this help")
    output.write_line("  /provider          configure provider credentials")
    output.write_line("  /model <name>      switch model")
    output.write_line("  /thinking <level>  set thinking level")
    output.write_line("  /workspace         show workspace root")
    output.write_line("  /session           show session info")
    output.write_line("  /compact           compact current session")
    output.write_line("  /new               start a new session")
    output.write_line("  /quit              exit")
    output.write("\n")
    output.write_line("keyboard")
    output.write_line("  up                 previous prompt")
    output.write_line("  down               next prompt / restore draft")
    output.write_line("  ctrl+u             clear prompt")
    output.write_line("  ctrl+c             interrupt, then quit")
    output.write("\n")
    output.write_line("provider setup")
    output.write_line(
        "  /provider ollama                     local ollama, no key needed"
    )
    output.write_line("  /provider ollama <url> [key]         custom endpoint")
    output.write_line("  /provider openai <key>               set OPENAI_API_KEY")
    output.write_line("  /provider anthropic <key>            set ANTHROPIC_API_KEY")
    output.write("\n")


def handle_provider_command(arg: str | None, output: TranscriptLog) -> None:
    """Update provider configuration from a slash command."""
    start_note_block(output, "provider")
    if not arg:
        output.write_line("usage")
        output.write_line("  /provider ollama                  local, no key needed")
        output.write_line("  /provider ollama <url> [key]      custom endpoint")
        output.write_line("  /provider openai <key>            set OPENAI_API_KEY")
        output.write_line("  /provider anthropic <key>         set ANTHROPIC_API_KEY")
        output.write("\n")
        output.write_line("credentials are saved to ~/.jaca/config.json")
        return

    tokens = arg.split()
    provider = tokens[0].lower()

    if provider == "ollama":
        base_url = tokens[1] if len(tokens) >= 2 else None
        api_key = tokens[2] if len(tokens) >= 3 else None
        if base_url:
            os.environ["OLLAMA_BASE_URL"] = base_url
        if api_key:
            os.environ["OLLAMA_API_KEY"] = api_key
        save_provider_config("ollama", base_url=base_url, api_key=api_key)
        effective_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        output.write_line(f"ollama: {effective_url}")
        if api_key:
            output.write_line("api key saved")
        output.write_line("saved to ~/.jaca/config.json")
        return

    if provider == "openai":
        if len(tokens) < 2:
            output.write_line("ERROR: usage: /provider openai <api-key>")
            return
        os.environ["OPENAI_API_KEY"] = tokens[1]
        save_provider_config("openai", api_key=tokens[1])
        output.write_line("OPENAI_API_KEY saved")
        output.write_line("saved to ~/.jaca/config.json")
        return

    if provider == "anthropic":
        if len(tokens) < 2:
            output.write_line("ERROR: usage: /provider anthropic <api-key>")
            return
        os.environ["ANTHROPIC_API_KEY"] = tokens[1]
        save_provider_config("anthropic", api_key=tokens[1])
        output.write_line("ANTHROPIC_API_KEY saved")
        output.write_line("saved to ~/.jaca/config.json")
        return

    output.write_line(f"ERROR: unknown provider: {provider}")
    output.write_line("supported: ollama, openai, anthropic")


__all__ = ["handle_provider_command", "start_note_block", "write_help"]
