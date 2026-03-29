from __future__ import annotations

import os
from pathlib import Path

from evaluations.harbor.commands import (
    build_harbor_exec_command,
    build_provider_env,
)

try:
    from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
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

    class JustAnotherCodingAgentHarborAgent(BaseInstalledAgent):
        @staticmethod
        def name() -> str:
            return "just-another-coding-agent"

        async def setup(self, environment: BaseEnvironment) -> None:
            repo_root = Path(__file__).resolve().parents[2]
            target_root = "/installed-agent/just-another-coding-agent"

            await environment.exec(command=f"mkdir -p {target_root}", user="root")
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
            await environment.upload_dir(
                source_dir=repo_root / "evaluations",
                target_dir=f"{target_root}/evaluations",
            )
            await super().setup(environment)

        async def install(self, environment: BaseEnvironment) -> None:
            install_script = (
                "/installed-agent/just-another-coding-agent/evaluations/harbor/"
                "install-just-another-coding-agent.sh.j2"
            )
            await self.exec_as_root(
                environment,
                command=f"sed 's/\\r$//' {install_script} | bash",
                env={"DEBIAN_FRONTEND": "noninteractive"},
            )

        @with_prompt_template
        async def run(
            self,
            instruction: str,
            environment: BaseEnvironment,
            context: AgentContext,
        ) -> None:
            if not self.model_name:
                raise ValueError("Model name is required")

            await self.exec_as_agent(
                environment,
                command=build_harbor_exec_command(
                    instruction=instruction,
                    model=self.model_name,
                    thinking=os.environ.get("JUST_ANOTHER_CODING_AGENT_THINKING"),
                ),
                env=build_provider_env(),
            )

        def populate_context_post_run(self, context: AgentContext) -> None:
            if context.metadata is None:
                context.metadata = {}
            context.metadata["adapter"] = "just-another-coding-agent-harbor"
