# Docs Index

read_when: start here, onboarding, or you need to find the right doc quickly

## Quick Map

- Repository overview and setup: [../README.md](../README.md)
- Repo-specific agent instructions: [../AGENTS.md](../AGENTS.md)
- Mental model and core concepts: [mental-model.md](mental-model.md)
- Product scope and non-goals: [goal.md](goal.md)
- TUI product constraints and north star: [tui.md](tui.md)
- Target architecture: [architecture.md](architecture.md)
- Canonical public contract: [contracts.md](contracts.md)
- PydanticAI grounding sources: [grounding.md](grounding.md)
- Development workflow and commands: [development.md](development.md)
- Compaction architecture and invariants: [compaction.md](compaction.md)
- Internal read-only worker contract: [read-only-worker.md](read-only-worker.md)
- Stateful orchestration boundary and plan: [stateful-orchestration.md](stateful-orchestration.md)
- Harbor and Terminal Bench workflow: [harbor-terminal-bench.md](harbor-terminal-bench.md)
- Terminal Bench run journal: [terminal-bench-journal.md](terminal-bench-journal.md)
- Terminal Bench baseline checklist: [learning/terminal-bench-checklist.md](learning/terminal-bench-checklist.md)
- Architectural decisions: [adr/](adr/)

## Document Index

- [../README.md](../README.md) - repository overview, scope, and setup
- [../AGENTS.md](../AGENTS.md) - repo-specific coding and grounding instructions
- [mental-model.md](mental-model.md) - mental model, core concepts, and how the pieces fit together
- [goal.md](goal.md) - product target, scope, and non-goals
- [tui.md](tui.md) - TUI product bar, hard constraints, and anti-bloat rules
- [architecture.md](architecture.md) - package layout and architectural boundaries
- [contracts.md](contracts.md) - canonical coding-agent contract
- [grounding.md](grounding.md) - official PydanticAI grounding sources
- [development.md](development.md) - environment, commands, CI, and test workflow
- [compaction.md](compaction.md) - durable session compaction architecture and invariants
- [read-only-worker.md](read-only-worker.md) - internal protocol and boundary for the shipped persistent read-only worker
- [stateful-orchestration.md](stateful-orchestration.md) - boundary between PydanticAI seams and our own session/orchestration contract
- [harbor-terminal-bench.md](harbor-terminal-bench.md) - Harbor adapter usage and Terminal Bench workflow
- [terminal-bench-journal.md](terminal-bench-journal.md) - running record of benchmark outcomes and task-picking learnings
- [learning/terminal-bench-checklist.md](learning/terminal-bench-checklist.md) - full Terminal Bench task checklist with current pass, fail, and untried status
- [adr/0001-use-pydanticai-as-engine.md](adr/0001-use-pydanticai-as-engine.md) - engine choice
- [adr/0002-headless-coding-agent-is-the-product.md](adr/0002-headless-coding-agent-is-the-product.md) - product boundary
- [adr/0003-canonical-session-and-rpc-contract.md](adr/0003-canonical-session-and-rpc-contract.md) - external contract decision
- [adr/0004-canonical-package-layout.md](adr/0004-canonical-package-layout.md) - package layout decision
- [adr/0005-preserve-pi-product-behavior-not-architecture.md](adr/0005-preserve-pi-product-behavior-not-architecture.md) - inspiration boundary
- [adr/0006-internal-execution-helpers-must-not-own-tool-semantics.md](adr/0006-internal-execution-helpers-must-not-own-tool-semantics.md) - internal helper boundary
