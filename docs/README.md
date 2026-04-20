# Docs Index

read_when: start here, onboarding, or you need to find the right doc quickly

## Quick Map

- Repository overview and setup: [../README.md](../README.md)
- Repo-specific agent instructions: [../AGENTS.md](../AGENTS.md)
- Mental model and core concepts: [mental-model.md](mental-model.md)
- Product scope and non-goals: [goal.md](goal.md)
- TUI product constraints and north star: [tui.md](tui.md)
- TUI architecture refactor plan: [tui-architecture-plan.md](tui-architecture-plan.md)
- ChatGPT subscription OAuth feasibility spike: [chatgpt-subscription-oauth-spike.md](chatgpt-subscription-oauth-spike.md)
- Target architecture: [architecture.md](architecture.md)
- Canonical public contract: [contracts.md](contracts.md)
- Permission execution and shell approval heuristics: [permission-execution.md](permission-execution.md)
- PydanticAI grounding sources: [grounding.md](grounding.md)
- Development workflow and commands: [development.md](development.md)
- Distribution, install lanes, and release behavior: [distribution.md](distribution.md)
- Compaction architecture and invariants: [compaction.md](compaction.md)
- Internal read-only worker contract: [read-only-worker.md](read-only-worker.md)
- Stateful orchestration boundary and plan: [stateful-orchestration.md](stateful-orchestration.md)
- Harbor and Terminal Bench workflow: [harbor-terminal-bench.md](harbor-terminal-bench.md)
- Terminal Bench run journal: [terminal-bench-journal.md](terminal-bench-journal.md)
- Terminal Bench slice analysis pipeline and dashboard: [tbench-slice-analysis.md](tbench-slice-analysis.md)
- Terminal Bench baseline checklist: [learning/terminal-bench-checklist.md](learning/terminal-bench-checklist.md)
- Subagent Terminal Bench eval cohort: [learning/subagent-terminal-bench-eval-set.md](learning/subagent-terminal-bench-eval-set.md)
- Architectural decisions: [adr/](adr/)

## Document Index

- [../README.md](../README.md) - repository overview, scope, and setup
- [../AGENTS.md](../AGENTS.md) - repo-specific coding and grounding instructions
- [mental-model.md](mental-model.md) - mental model, core concepts, and how the pieces fit together
- [goal.md](goal.md) - product target, scope, and non-goals
- [tui.md](tui.md) - TUI product bar, hard constraints, and anti-bloat rules
- [tui-architecture-plan.md](tui-architecture-plan.md) - TUI refactor direction, domain slices, controller shape, and rollout plan
- [chatgpt-subscription-oauth-spike.md](chatgpt-subscription-oauth-spike.md) - narrow feasibility plan for one ChatGPT subscription OAuth path
- [architecture.md](architecture.md) - package layout and architectural boundaries
- [contracts.md](contracts.md) - canonical coding-agent contract
- [permission-execution.md](permission-execution.md) - current backend permission execution model and shell approval heuristics
- [grounding.md](grounding.md) - official PydanticAI grounding sources
- [development.md](development.md) - environment, commands, CI, and test workflow
- [distribution.md](distribution.md) - published install path, repo checkout path, and release packaging rules
- [compaction.md](compaction.md) - durable session compaction architecture and invariants
- [read-only-worker.md](read-only-worker.md) - internal protocol and boundary for the shipped persistent read-only worker
- [stateful-orchestration.md](stateful-orchestration.md) - boundary between PydanticAI seams and our own session/orchestration contract
- [harbor-terminal-bench.md](harbor-terminal-bench.md) - Harbor adapter usage and Terminal Bench workflow
- [terminal-bench-journal.md](terminal-bench-journal.md) - running record of benchmark outcomes and task-picking learnings
- [tbench-slice-analysis.md](tbench-slice-analysis.md) - single-pipeline analyzer over `jobs/` and the dashboard refresh workflow
- [learning/terminal-bench-checklist.md](learning/terminal-bench-checklist.md) - full Terminal Bench task checklist with current pass, fail, and untried status
- [learning/subagent-terminal-bench-eval-set.md](learning/subagent-terminal-bench-eval-set.md) - curated GPT-5.4 high-failure cohort for subagent Harbor reruns
- [adr/0001-use-pydanticai-as-engine.md](adr/0001-use-pydanticai-as-engine.md) - engine choice
- [adr/0002-headless-coding-agent-is-the-product.md](adr/0002-headless-coding-agent-is-the-product.md) - product boundary
- [adr/0003-canonical-session-and-rpc-contract.md](adr/0003-canonical-session-and-rpc-contract.md) - external contract decision
- [adr/0004-canonical-package-layout.md](adr/0004-canonical-package-layout.md) - package layout decision
- [adr/0005-preserve-pi-product-behavior-not-architecture.md](adr/0005-preserve-pi-product-behavior-not-architecture.md) - inspiration boundary
- [adr/0006-internal-execution-helpers-must-not-own-tool-semantics.md](adr/0006-internal-execution-helpers-must-not-own-tool-semantics.md) - internal helper boundary
