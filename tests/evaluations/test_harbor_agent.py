from __future__ import annotations

import asyncio
import importlib
import sys
import types
from dataclasses import dataclass
from pathlib import Path


def _install_fake_harbor_modules() -> None:
    harbor = types.ModuleType("harbor")
    agents = types.ModuleType("harbor.agents")
    installed = types.ModuleType("harbor.agents.installed")
    base = types.ModuleType("harbor.agents.installed.base")
    environments = types.ModuleType("harbor.environments")
    environments_base = types.ModuleType("harbor.environments.base")
    models = types.ModuleType("harbor.models")
    models_agent = types.ModuleType("harbor.models.agent")
    models_agent_context = types.ModuleType("harbor.models.agent.context")

    @dataclass
    class ExecInput:
        command: str
        cwd: str | None = None
        env: dict[str, str] | None = None
        timeout_sec: int | None = None

    class BaseInstalledAgent:
        def __init__(
            self,
            logs_dir: Path,
            model_name: str | None = None,
            *args,
            **kwargs,
        ):
            self.logs_dir = logs_dir
            self.model_name = model_name

        @staticmethod
        def name() -> str:
            raise NotImplementedError

        def version(self) -> str | None:
            return None

        async def setup(self, environment) -> None:
            environment.exec_commands.append("super-setup")

    class BaseEnvironment:
        pass

    class AgentContext:
        def __init__(self) -> None:
            self.metadata: dict[str, str] | None = None

    base.BaseInstalledAgent = BaseInstalledAgent
    base.ExecInput = ExecInput
    environments_base.BaseEnvironment = BaseEnvironment
    models_agent_context.AgentContext = AgentContext

    sys.modules["harbor"] = harbor
    sys.modules["harbor.agents"] = agents
    sys.modules["harbor.agents.installed"] = installed
    sys.modules["harbor.agents.installed.base"] = base
    sys.modules["harbor.environments"] = environments
    sys.modules["harbor.environments.base"] = environments_base
    sys.modules["harbor.models"] = models
    sys.modules["harbor.models.agent"] = models_agent
    sys.modules["harbor.models.agent.context"] = models_agent_context


def test_harbor_agent_supports_current_base_installed_agent_api(
    monkeypatch, tmp_path: Path
) -> None:
    for module_name in list(sys.modules):
        if module_name.startswith("evaluations.harbor.agent") or module_name.startswith(
            "harbor"
        ):
            sys.modules.pop(module_name)

    _install_fake_harbor_modules()
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("JUST_ANOTHER_CODING_AGENT_THINKING", "high")
    monkeypatch.setenv(
        "JACA_SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION",
        "0.1",
    )
    monkeypatch.setenv("LOGFIRE_TOKEN", "logfire-secret")

    module = importlib.import_module("evaluations.harbor.agent")
    agent = module.JustAnotherCodingAgentHarborAgent(
        logs_dir=tmp_path / "logs",
        model_name="openai-responses:gpt-5.4",
    )

    install_template_path = agent._install_agent_template_path
    assert install_template_path.name == "install-just-another-coding-agent.sh.j2"
    assert install_template_path.exists()

    commands = agent.create_run_agent_commands("Fix the task")

    assert len(commands) == 1
    assert commands[0].env == {
        "OPENAI_API_KEY": "openai-secret",
        "JUST_ANOTHER_CODING_AGENT_THINKING": "high",
        "JACA_SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION": "0.1",
        "JACA_TRACE_MODE": "logfire",
        "LOGFIRE_SERVICE_NAME": "jaca-harbor",
        "LOGFIRE_TOKEN": "logfire-secret",
    }
    assert "evaluations.bench.exec_prompt" in commands[0].command
    assert "--model openai-responses:gpt-5.4" in commands[0].command


def test_harbor_agent_uses_explicit_harbor_logfire_service_name_override(
    monkeypatch, tmp_path: Path
) -> None:
    for module_name in list(sys.modules):
        if module_name.startswith("evaluations.harbor.agent") or module_name.startswith(
            "harbor"
        ):
            sys.modules.pop(module_name)

    _install_fake_harbor_modules()
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("JUST_ANOTHER_CODING_AGENT_THINKING", "high")
    monkeypatch.setenv("LOGFIRE_TOKEN", "logfire-secret")
    monkeypatch.setenv("LOGFIRE_SERVICE_NAME", "harbor-task")

    module = importlib.import_module("evaluations.harbor.agent")
    agent = module.JustAnotherCodingAgentHarborAgent(
        logs_dir=tmp_path / "logs",
        model_name="openai-responses:gpt-5.4",
    )

    commands = agent.create_run_agent_commands("Fix the task")

    assert len(commands) == 1
    assert commands[0].env == {
        "OPENAI_API_KEY": "openai-secret",
        "JUST_ANOTHER_CODING_AGENT_THINKING": "high",
        "JACA_TRACE_MODE": "logfire",
        "LOGFIRE_TOKEN": "logfire-secret",
        "LOGFIRE_SERVICE_NAME": "harbor-task",
    }


def test_harbor_agent_setup_matches_current_environment_exec_api(
    monkeypatch, tmp_path: Path
) -> None:
    for module_name in list(sys.modules):
        if module_name.startswith("evaluations.harbor.agent") or module_name.startswith(
            "harbor"
        ):
            sys.modules.pop(module_name)

    _install_fake_harbor_modules()
    module = importlib.import_module("evaluations.harbor.agent")
    commands_module = importlib.import_module("evaluations.harbor.commands")
    prebuilt_worker = tmp_path / "jaca-read-only-worker"
    prebuilt_worker.write_text("fake worker", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "_build_harbor_read_only_worker",
        lambda _repo_root: prebuilt_worker,
    )
    monkeypatch.setattr(commands_module, "AUTH_FILE_PATH", tmp_path / "auth.json")
    monkeypatch.setattr(commands_module, "OAUTH_FILE_PATH", tmp_path / "oauth.json")

    class FakeEnvironment:
        def __init__(self) -> None:
            self.exec_commands: list[str] = []
            self.uploaded_files: list[tuple[Path, str]] = []
            self.uploaded_dirs: list[tuple[Path, str]] = []

        async def exec(
            self,
            command: str,
            cwd: str | None = None,
            env: dict[str, str] | None = None,
            timeout_sec: int | None = None,
        ) -> None:
            del cwd, env, timeout_sec
            self.exec_commands.append(command)

        async def upload_file(self, source_path: Path, target_path: str) -> None:
            self.uploaded_files.append((source_path, target_path))

        async def upload_dir(self, source_dir: Path, target_dir: str) -> None:
            self.uploaded_dirs.append((source_dir, target_dir))

    agent = module.JustAnotherCodingAgentHarborAgent(
        logs_dir=tmp_path / "logs",
        model_name="openai-responses:gpt-5.4",
    )
    environment = FakeEnvironment()

    asyncio.run(agent.setup(environment))

    expected_mkdir = (
        "mkdir -p /installed-agent/just-another-coding-agent "
        "/installed-agent/just-another-coding-agent/prebuilt /root/.jaca"
    )
    assert environment.exec_commands == [
        expected_mkdir,
        "super-setup",
    ]
    assert [target for _, target in environment.uploaded_files] == [
        "/installed-agent/just-another-coding-agent/pyproject.toml",
        "/installed-agent/just-another-coding-agent/README.md",
        "/installed-agent/just-another-coding-agent/prebuilt/jaca-read-only-worker",
    ]
    assert [target for _, target in environment.uploaded_dirs] == [
        "/installed-agent/just-another-coding-agent/src",
        "/installed-agent/just-another-coding-agent/evaluations",
    ]
