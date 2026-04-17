# Architecture

read_when: you need the big picture or need to know where code belongs

## System Overview

The architecture is intentionally thin. PydanticAI is the engine. This repo owns the coding-agent product surface that is specific to the backend, plus a thin first-party TUI shell over that same runtime.

The main architectural risk in the current shape is split-brain product logic between Python and Go. The mitigation is explicit: Python owns backend semantics and public contracts; Go owns terminal presentation, input handling, and RPC client behavior. If the shell needs richer semantics, the backend contract should grow rather than teaching Go to infer or reinvent backend meaning locally.

That includes the shipped model surface. Python owns the canonical model
catalog, provider defaults, and model-context metadata. Go may fetch and render
that catalog, but it must not hardcode or reinterpret shipped model semantics
locally.
Provider auth semantics follow the same rule: Python owns auth meaning,
provider-specific readiness rules, secret-store resolution order, and
effective endpoint auth requirements. Go may render `/auth` and `/login` UX,
but it must not become a second owner of secret storage or provider-auth
policy.
Sandbox and approval semantics follow the same rule too: Python owns sandbox
policy, approval policy, effective-capability meaning, approval lifecycle, and
the boundary between control-plane policy and data-plane executor backends. Go
may render approval prompts and effective sandbox posture, but it must not
infer sandbox meaning locally or grow backend-specific policy logic of its own.

The same risk exists when a non-Python execution helper is introduced for
performance. The current read-only worker is a separate Go helper for
`read`, `ls`, `find`, and `grep`, and those read-only tool semantics are now
canonically implemented in that worker. This is a narrow backend-owned
exception, not a general invitation to spread product semantics across
languages. Python still owns tool registration, higher-level activity
metadata, session meaning, RPC meaning, and recovery policy.

For Go TUI refactors, optimize first for module boundaries, testability, and reduced semantic drift. Treat LOC reduction as a guardrail rather than a target, and sequence extractions before new interface layers so the same transcript subsystem is not refactored twice without learning anything.

## Implementation Stance

Prefer direct use of PydanticAI primitives before creating local abstractions:

- use PydanticAI agent runs and streaming as the execution core
- use PydanticAI function tools and toolsets as the default tool substrate
- use PydanticAI function signatures plus parameter constraints as the public
  canonical tool schema instead of mirroring per-tool input carriers in
  `contracts/`
- use PydanticAI message-history primitives as the default conversation substrate
- use PydanticAI `instructions` for the base product prompt and explicit
  model-visible message history for dynamic prompt-context layers
- use PydanticAI testing primitives for unit and contract tests

Local code should translate those primitives into the canonical backend contract for tools, events, sessions, RPC, and failure semantics.
`runtime/models.py` is the sanctioned local seam for explicit model construction and provider-native policy.
PydanticAI-native carrier features such as `ToolReturn.metadata` may be used internally, but they must be normalized immediately into typed backend contract fields before crossing the public stream/session boundary. For canonical tools, success activity ownership lives with the tools themselves; the runtime only validates and maps that metadata into the public event/session contract. Non-success tool activity stays intentionally small: backend-owned titles, optional summaries, and durations, without re-parsing typed args into structured details.
re-entry, that validator should stay private to the tool or runtime seam rather
than becoming a second public source of truth in `contracts/`.
If a future helper in another language executes a narrow internal tool seam, it
must have an explicitly documented ownership boundary. It should not create a
second semantic source of truth for the same tool surface.

The canonical agent assembly must take an explicit workspace root. Tool behavior uses that root as the default base for relative paths and shell cwd rather than relying on process cwd or other implicit global state.
Persisted sessions must also bind to that explicit workspace root and store native PydanticAI message history so later runs can resume through `message_history` instead of reconstructing context from public events.
Persisted sessions must also record the effective per-run thinking setting so later runs can inherit it when the caller omits `thinking`.
The canonical runtime is unbounded within a single run and does not impose backend-level request or tool-call ceilings.
Do not use PydanticAI `UsageLimits` as a JACA design primitive. If a framework call requires `UsageLimits`, keep it explicitly unbounded and treat it as an internal adapter detail rather than product policy.
The canonical prompt context has four Python-owned layers. The base product
prompt is static `instructions` text assembled from named sections. The
project-instructions layer injects bounded model-visible messages from
workspace-root `AGENTS.md` and `CLAUDE.md` when present. The runtime-context
layer injects current date, timezone, workspace root, shell family, model, and
thinking as dynamic model-visible contextual history. The mode/task layer is
only a seam for now; default mode adds no extra instructions, and task-specific
overlays should be added only when a concrete behavior gap justifies them.
`runtime/prompt_layers.py` owns this assembly boundary so these layers do not
become scattered prompt concatenation.
That prompt assembly is also tool-aware: restricted internal agents must be
given instructions that match the toolset they actually have, instead of
reusing the full canonical tool list and relying on hidden caveats.
Project-doc and runtime-context messages are runtime-owned contextual history,
not static baseline prompt text and not durable session memory.
The first subagent slice follows the same rule. The model-visible `subagent`
tool is only a thin frontend over the internal `runtime/subagent.py`
run-local seam: same runtime frame, same model, same thinking, backend-owned
child capability selection, and no durable child-session JSONL persistence. A
child always lacks `write` and `edit`; `shell` is optional and must be
requested explicitly through the backend-owned subagent contract. If subagent
behavior later grows into public session or RPC semantics, that contract must
be made explicit in Python first rather than being inferred in Go.
Even while child runs stay ephemeral, their lineage must still be real.
Root-session deps carry the durable session id, `runtime/run.py` binds the
active run id before tools execute, and ephemeral child scope records the real
parent session/run provenance instead of placeholder values.
Subagent context inheritance is also explicit now: `spawn_mode=fork` is the
default and inherits a sanitized snapshot of the parent's current message
history, while `spawn_mode=fresh` rebuilds only the fresh runtime/project
frame. The fork path must strip unresolved tool calls and old system-prompt
state before reuse so child history stays valid and backend-owned.
The child-to-parent contract is also Python-owned: the child must return one
plain-text report, while the backend derives only a compact summary line for
transcript rendering. If the parent needs a stricter report shape, it must ask
for that shape in the child task rather than relying on a global parser. That
means the parent prompt policy, tool schema, and docs should all push the
parent to write detailed child tasks with the exact goal, relevant artifacts,
constraints, stop condition, and desired output shape when needed.
The transcript slice for subagent work follows the same ownership rule:
Python emits one compact parent activity with bounded preview lines, and Go
only renders that typed detail block. Role, spawn mode, and capability are now
backend-owned child semantics in that activity contract, so the shell must not
stream or infer child transcript semantics locally.
The canonical prompt must also enforce side-effect truthfulness and verification discipline: the model must not claim to have created or modified files without tool evidence, and it should run the smallest relevant verification step before concluding after code changes or required file outputs.
The canonical runtime should expose `thinking` as an explicit run setting and pass it through PydanticAI model settings rather than encoding reasoning level in prompt text.
The canonical runtime should resolve model strings through one local model seam before agent construction so provider-native retries, instrumentation, and OpenAI-specific settings stay centralized instead of leaking through the runtime.
Provider secret resolution should stay centralized too: environment variables
override the canonical local auth file at `~/.jaca/auth.json`, while
`~/.jaca/config.json` persists only non-secret preferences.
The canonical agent also keeps `output_type=str` and deliberately sets a very
high PydanticAI output-validation retry budget. That is not a second generic
run retry policy; it is an explicit choice to avoid a framework output ceiling
becoming the stop condition for the plain-text coding agent. If the canonical
agent ever moves to structured output or output validators, this retry policy
must be revisited rather than silently inherited.
The canonical agent also sets a small explicit tool-correction retry budget for
recoverable model mistakes such as invented tool names or malformed tool args.
That budget is intentionally separate from both output-validation retries and
the hidden pre-stream run retry. The short-term goal is reliable bounded
correction; the longer-term direction is to make tool-call correction fully
runtime-owned rather than relying on framework defaults.
The canonical tool concurrency policy should also be explicit rather than left to framework defaults: read-only tools may run in parallel, mutating tools remain serialized, and provider-side `parallel_tool_calls` should be enabled by default for the canonical provider paths unless the backend explicitly carves out a known-bad model path.
Opt-in tracing should stay in one place too: `JACA_TRACE_MODE` controls whether the backend stays untraced, stores spans locally, or exports them to Logfire. `local` turns on PydanticAI/OpenTelemetry instrumentation plus a local JSONL span exporter under `~/.jaca/traces/`. `logfire` enables the same instrumentation but exports to Logfire and fails hard unless Logfire credentials are already configured.
Internal tool executors should also depend only on the execution context they actually use. If an executor only needs `deps`, `tool_call_id`, and `tool_name`, that narrower structural contract should be explicit instead of pretending to require a full PydanticAI `RunContext`.

## Stateful Orchestration Boundary

The repo is already session-stateful across runs because persisted native
PydanticAI `message_history` is loaded back into later runs. The next step is
thin stateful orchestration, not a second agent framework.

Use PydanticAI where it already has the right seam:

- use `message_history` as the canonical resume substrate
- do not use PydanticAI `history_processors` in the canonical runtime path;
  durable continuity stays backend-owned
- use Hooks for observability, classification, and other run-local interception
- use `model_settings` for explicit run settings such as `thinking`
- use provider-native model and provider classes when the backend needs retries, instrumentation, or OpenAI Responses history behavior that plain model strings cannot express cleanly

Keep product semantics in this repo's own contract layer:

- session JSONL entry types and format versioning
- explicit session commands such as `session.compact`
- compaction summary shape and resume semantics
- persisted runtime-framing snapshots and their invalidation rules
- continue semantics across runs
- durable recovery policy and public streamed event behavior

The rule of thumb is:

- if it changes behavior inside one run step, PydanticAI probably has a seam
- if it changes what the backend promises across runs or over RPC, we own it

## Model Resolution

Canonical model handling goes through `runtime/models.py`.

Current responsibilities:

- resolve canonical model strings into explicit PydanticAI model objects
- centralize OpenAI-compatible retry policy at the client transport layer
- centralize opt-in model instrumentation
- centralize model-setting policy such as `thinking`

Important boundary:

- durable JSONL session history is the authoritative source of truth for resumed runs
- persisted per-run turn-context snapshots are separate backend-owned runtime framing state, not conversation memory
- turn-context persistence powers a separate model-visible runtime-framing channel: resumed runs reconstruct the last full runtime-context prefix and, when the visible framing changed, append one runtime-context update message before the new user prompt
- the visible runtime-framing payload now carries current date, timezone, workspace root, shell family, model, and thinking setting so those changes can stay in the smaller update path instead of forcing a full prefix reset
- the canonical session runtime does not rely on provider-side server history during resume
- provider-native history settings may still exist inside the lower-level model seam, but they are not part of canonical session continuation semantics

## Compaction Direction

Compaction has one canonical role:

- durable compaction manages long-lived session growth across runs

Current sequence:

1. Add manual session compaction first.
2. Persist a compaction entry alongside existing session entries.
3. Persist one model-visible replacement-history artifact on that entry and rebuild resumed `message_history` from `replacement_messages` plus later native run deltas.
4. Add deterministic automatic compaction before resumed runs when estimated local next-run message history, including any reconstructed runtime-context prefix and runtime-context update message, plus reserve crosses a fraction of the effective model context window after compaction-output headroom is reserved.
5. Keep live-run recovery at the canonical streamed-run boundary when it must preserve a clean public event contract.

The important boundary is:

- compaction manages cross-run session size
- durable auto-compaction now uses one backend-owned token-estimation seam over
  the actual next resumed request substrate; it does not mix in prior
  provider-reported usage
- automatic compaction decisions now produce explicit backend-owned budget
  reports so clients and tests can inspect why compaction did or did not
  trigger without reverse-engineering token heuristics locally
- durable compaction entries now persist `replacement_messages` plus
  `compacted_through_run_id`, so resumed history rebuilds from one canonical
  model-visible compacted prefix rather than a hidden summary/checkpoint split
- later compaction invalidates the currently active persisted turn-context
  baseline so future runs do not diff against stale runtime framing
- if no canonical context metadata is known for the active model, live-run
  compaction falls back to one conservative default soft char limit
  should leave the current model step and resume with explicit results
- the runtime uses a separate model call to generate the durable compaction summary
- durable compaction state lives in our session file, and resumed runs
  materialize explicit compacted `message_history` from that state before the
  next run starts
- terminal successful runs persist only PydanticAI `new_messages()` deltas, not
  reconstructed full history with a later semantic strip step
- canonical agent construction does not hide compaction policy; durable
  compaction stays in the session runtime and resume path
- `run.start` on an existing session is the canonical continue operation; there is no second continue command today
- `stream_run_events()` may hide one retryable transient failure before any public stream event escapes, but once assistant text or tool lifecycle events have been emitted the runtime does not retry automatically
- recoverable tool-call validation failures inside one run remain model-visible
  and consume the canonical bounded tool-correction budget instead of becoming
  hidden stream-boundary retries

## Canonical Package Layout

- `src/just_another_coding_agent/runtime/`
  - agent construction
  - `compaction/`
    - `session_summary.py` for durable cross-run compaction orchestration
    - `constants.py`, `boundary.py`, `trigger.py`, and `source_builder.py` for
      focused durable-compaction helpers
    - `resume.py` for compacted session replay helpers
  - `token_estimation.py` for the canonical backend-owned token-estimation seam
  - `turn_context.py` for persisted per-run runtime-framing snapshots
  - `subagent.py` for internal ephemeral child-run scaffolding
  - orchestration entrypoints
  - event translation from PydanticAI into the public contract
- `src/just_another_coding_agent/tools/`
  - canonical coding tools
  - tool execution policy
  - Python-owned tool semantics even when a future internal helper executes a
    narrow read-only path
  - `read_only_worker/` for the internal helper protocol, launcher, and
    client/runtime transport for read-only execution
- `src/just_another_coding_agent/session/`
  - session persistence
  - session load/save helpers
  - `replacement_history.py` for replacement-message construction and validation
- `src/just_another_coding_agent/rpc/`
  - JSON-over-stdio protocol
  - command handlers
- `cmd/jaca/`
  - canonical interactive TUI entrypoint
- `cmd/jaca-read-only-worker/`
  - persistent internal Go helper for read-only workspace operations
- `internal/jaca/`
  - first-party Go terminal UI
  - presentation, input handling, shell state, and RPC client bridge
- `src/just_another_coding_agent/contracts/`
  - contract types, constants, and schema helpers
- `evaluations/`
  - non-product evaluation harness code such as Harbor and Terminal Bench glue
  - depends on `just_another_coding_agent`; product packages must not depend on evaluation code

## Boundaries

- Do not build a second general-purpose agent framework in this repo.
- Keep provider-specific behavior inside PydanticAI unless a coding-agent requirement forces a local adapter.
- Keep the runtime thin and contract-driven.
- Keep tool behavior strict and explicit.
- Keep sessions and RPC stable only when deliberately chosen as public contracts.
- Do not import pi-mono's package layout, UI model, or extension architecture into this repo.
- Keep the TUI constrained to exactly three zones: status bar, transcript, and prompt.
- TUI capabilities must live in those zones or behind slash commands; no side panels, drawers, or split-pane growth.
- Keep backend semantics in Python. Go may format, group, and present streamed state, but it must not become an independent source of truth for tool semantics, event semantics, session semantics, or recovery policy.
- Keep future internal execution helpers in the same role: they may optimize
  execution, but they must not become an independent source of truth for tool
  semantics, event semantics, session semantics, RPC semantics, or recovery
  policy.
- Keep Harbor, Terminal Bench, and similar external harness bindings out of `just_another_coding_agent` core packages.
- Evaluation harnesses may wrap the canonical stdio/session/runtime path, but they must not create a second execution contract.

## Data Flow

1. A caller starts a run through the runtime or RPC layer, and RPC delegates to the same session-aware runtime coordinator rather than maintaining a separate execution path.
   RPC owns only server-generated opaque session ids and the mapping to workspace-scoped session shards and metadata sidecars; clients do not see filesystem paths or workspace metadata.
2. The runtime creates or resumes a coding-agent run using PydanticAI primitives directly where possible, with `WorkspaceDeps(workspace_root=...)` passed as run deps, a thin canonical tool registry selecting the requested toolset, and persisted `message_history` supplied for session continuation.
3. Tools execute through the canonical tool layer.
4. Events are translated into the public streamed event contract rather than exposing raw framework internals directly.
5. Session persistence appends `session_run` plus public `session_event` entries incrementally during streaming, then appends the native PydanticAI `session_messages` for that run and the optional `session_turn_context` runtime-framing snapshot only after terminal completion, all bound to the authoritative workspace root and effective per-run thinking setting.
6. The TUI, when used, consumes the same runtime/session path rather than introducing a second execution model.
