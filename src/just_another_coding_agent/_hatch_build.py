from __future__ import annotations

import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from just_another_coding_agent.go_binaries import (
    build_go_binary,
    explicit_release_wheel_tag,
)
from just_another_coding_agent.go_tui import GO_TUI_BINARY, go_tui_build_requested
from just_another_coding_agent.tools.read_only_worker.launcher import (
    READ_ONLY_WORKER_BINARY,
)

class build_hook(BuildHookInterface):  # noqa: N801
    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        del version
        shared_scripts = dict(build_data.get("shared_scripts", {}))

        helper_binary_path = build_go_binary(
            project_root=Path(self.root),
            build_dir=Path(self.directory) / "read-only-worker",
            output_name=READ_ONLY_WORKER_BINARY,
            package_path="./cmd/jaca-read-only-worker",
            prebuilt_env_var="JACA_PREBUILT_READ_ONLY_WORKER",
            failure_label="read-only worker binary",
        )
        build_data["pure_python"] = False
        build_data["infer_tag"] = True
        explicit_tag = explicit_release_wheel_tag()
        if explicit_tag is not None:
            build_data["tag"] = explicit_tag
        shared_scripts[str(helper_binary_path)] = helper_binary_path.name

        if go_tui_build_requested():
            tui_binary_path = build_go_binary(
                project_root=Path(self.root),
                build_dir=Path(self.directory) / "go-tui",
                output_name=GO_TUI_BINARY,
                package_path="./cmd/jaca",
                failure_label="Go TUI binary",
            )
            shared_scripts[str(tui_binary_path)] = tui_binary_path.name

        build_data["shared_scripts"] = shared_scripts
