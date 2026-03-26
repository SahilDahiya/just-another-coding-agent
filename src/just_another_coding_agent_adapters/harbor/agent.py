from __future__ import annotations

from pathlib import Path

from just_another_coding_agent_adapters.harbor.commands import (
    build_harbor_exec_command,
    build_provider_env,
)

try:
    from harbor.agents.installed.base import BaseInstalledAgent, ExecInput
    from harbor.models.agent.context import AgentContext
except ModuleNotFoundError as error:  # pragma: no cover
    _HARBOR_IMPORT_ERROR = error

    class JustAnotherCodingAgentHarborAgent:
        def __init__(self, *_args, **_kwargs) -> None:
            raise ModuleNotFoundError(
                "harbor is required to use JustAnotherCodingAgentHarborAgent"
            ) from _HARBOR_IMPORT_ERROR

else:

    class JustAnotherCodingAgentHarborAgent(BaseInstalledAgent):
        @staticmethod
        def name() -> str:
            return "just-another-coding-agent"

        @property
        def _install_agent_template_path(self) -> Path:
            return Path(__file__).with_name("install-just-another-coding-agent.sh.j2")

        async def setup(self, environment) -> None:
            repo_root = Path(__file__).resolve().parents[3]
            target_root = "/installed-agent/just-another-coding-agent"

            await environment.exec(command=f"mkdir -p {target_root}")
            await environment.upload_file(
                source_path=repo_root / "pyproject.toml",
                target_path=f"{target_root}/pyproject.toml",
            )
            await environment.upload_file(
                source_path=repo_root / "README.md",
                target_path=f"{target_root}/README.md",
            )
            await environment.upload_dir(
                source_dir=repo_root / "src",
                target_dir=f"{target_root}/src",
            )
            await super().setup(environment)

        def populate_context_post_run(self, context: AgentContext) -> None:
            if context.metadata is None:
                context.metadata = {}
            context.metadata["adapter"] = "just-another-coding-agent-harbor"

        def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
            if not self.model_name:
                raise ValueError("Model name is required")

            return [
                ExecInput(
                    command=build_harbor_exec_command(
                        instruction=instruction,
                        model=self.model_name,
                    ),
                    env=build_provider_env(),
                )
            ]
