from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from pathlib import Path

from evaluations.harbor.commands import (
    build_harbor_exec_command,
    build_provider_env,
)
from just_another_coding_agent.go_binaries import build_go_binary
from just_another_coding_agent.tools.read_only_worker.launcher import (
    READ_ONLY_WORKER_BINARY,
)

try:
    from harbor.agents.installed.base import BaseInstalledAgent, ExecInput
    from harbor.environments.base import BaseEnvironment
    from harbor.models.agent.context import AgentContext
except ModuleNotFoundError as error:  # pragma: no cover
    _HARBOR_IMPORT_ERROR = error

    class JustAnotherCodingAgentHarborAgent:
        def __init__(self, *_args, **_kwargs) -> None:
            raise ModuleNotFoundError(
                "harbor is required to use JustAnotherCodingAgentHarborAgent"
            ) from _HARBOR_IMPORT_ERROR

else:

    def _build_harbor_read_only_worker(repo_root: Path) -> Path:
        build_root = Path(tempfile.mkdtemp(prefix="jaca-harbor-read-only-worker-"))
        return build_go_binary(
            project_root=repo_root,
            build_dir=build_root,
            output_name=READ_ONLY_WORKER_BINARY,
            package_path="./cmd/jaca-read-only-worker",
            failure_label="Harbor read-only worker",
        )

    class JustAnotherCodingAgentHarborAgent(BaseInstalledAgent):
        @staticmethod
        def name() -> str:
            return "just-another-coding-agent"

        @property
        def _install_agent_template_path(self) -> Path:
            return Path(__file__).with_name("install-just-another-coding-agent.sh.j2")

        async def setup(self, environment: BaseEnvironment) -> None:
            repo_root = Path(__file__).resolve().parents[2]
            target_root = "/installed-agent/just-another-coding-agent"
            target_prebuilt_dir = f"{target_root}/prebuilt"
            read_only_worker_binary = _build_harbor_read_only_worker(repo_root)

            try:
                mkdir_command = f"mkdir -p {target_root} {target_prebuilt_dir}"
                await environment.exec(command=mkdir_command)
                await environment.upload_file(
                    source_path=repo_root / "pyproject.toml",
                    target_path=f"{target_root}/pyproject.toml",
                )
                await environment.upload_file(
                    source_path=repo_root / "README.md",
                    target_path=f"{target_root}/README.md",
                )
                await environment.upload_file(
                    source_path=read_only_worker_binary,
                    target_path=f"{target_prebuilt_dir}/{READ_ONLY_WORKER_BINARY}",
                )
                await environment.upload_dir(
                    source_dir=repo_root / "src",
                    target_dir=f"{target_root}/src",
                )
                await environment.upload_dir(
                    source_dir=repo_root / "evaluations",
                    target_dir=f"{target_root}/evaluations",
                )
                await super().setup(environment)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    read_only_worker_binary.unlink()
                shutil.rmtree(read_only_worker_binary.parent, ignore_errors=True)

        def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
            if not self.model_name:
                raise ValueError("Model name is required")

            return [
                ExecInput(
                    command=build_harbor_exec_command(
                        instruction=instruction,
                        model=self.model_name,
                        thinking=os.environ.get("JUST_ANOTHER_CODING_AGENT_THINKING"),
                    ),
                    env=build_provider_env(model=self.model_name),
                )
            ]

        def populate_context_post_run(self, context: AgentContext) -> None:
            if context.metadata is None:
                context.metadata = {}
            context.metadata["adapter"] = "just-another-coding-agent-harbor"
