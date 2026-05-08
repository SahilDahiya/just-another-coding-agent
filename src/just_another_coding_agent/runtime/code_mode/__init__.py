from just_another_coding_agent.runtime.code_mode.service import (
    CodeModeCellContext,
    CodeModeCellNotFoundError,
    CodeModeCellService,
    CodeModeCellStateError,
    CodeModeRunner,
)

__all__ = [
    "CodeModeCellContext",
    "CodeModeCellNotFoundError",
    "CodeModeCellService",
    "CodeModeCellStateError",
    "CodeModeParentContext",
    "CodeModeRunner",
    "CodeModeSourceRuntimeError",
    "CodeModeToolBridge",
    "PythonSubprocessCodeModeRuntime",
]


def __getattr__(name: str):
    if name in {"CodeModeParentContext", "CodeModeToolBridge"}:
        from just_another_coding_agent.runtime.code_mode.bridge import (
            CodeModeParentContext,
            CodeModeToolBridge,
        )

        return {
            "CodeModeParentContext": CodeModeParentContext,
            "CodeModeToolBridge": CodeModeToolBridge,
        }[name]
    if name in {"CodeModeSourceRuntimeError", "PythonSubprocessCodeModeRuntime"}:
        from just_another_coding_agent.runtime.code_mode.python_runtime import (
            CodeModeSourceRuntimeError,
            PythonSubprocessCodeModeRuntime,
        )

        return {
            "CodeModeSourceRuntimeError": CodeModeSourceRuntimeError,
            "PythonSubprocessCodeModeRuntime": PythonSubprocessCodeModeRuntime,
        }[name]
    raise AttributeError(name)
