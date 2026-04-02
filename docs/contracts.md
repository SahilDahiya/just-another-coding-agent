# Contracts

read_when: you are defining behavior, writing tests, or deciding what must remain stable

## Purpose

This document defines the canonical external contract for the coding-agent backend. Tests should protect this contract before they protect internal implementation details.

The contract preserves the backend-facing behavior of a pi-style coding agent while remaining independent from pi-mono's internal architecture. Internally, the implementation should prefer direct PydanticAI primitives and expose one simplified, stable public contract.

## Prompt Context Contract

Canonical prompt context for the maintained version:

- static baseline instructions
- dynamic current date
- dynamic resolved workspace root

Rules:

- the canonical agent prompt must be assembled through one builder path
- dynamic prompt context must be explicit and reproducible
- the canonical agent prompt must explicitly forbid claiming file side effects without tool evidence
- the canonical agent prompt must explicitly instruct the model to verify code changes or required file outputs before concluding

## Model Settings Contract

Initial canonical model-setting slice:

- `thinking`

Rules:

- Model settings must be explicit run inputs, not hidden prompt text.
- The canonical runtime may expose only deliberately chosen settings instead of leaking arbitrary provider settings through the public contract.
- When `thinking` is omitted, the runtime uses model default behavior unless a resumed session has a persisted thinking level to inherit.
- Provider-native model settings may still be applied internally when they do not change the public contract.
- Run-local history compaction may derive its soft threshold from explicit
  model-context metadata in the runtime model seam, but that threshold remains
  an internal heuristic rather than a caller-controlled contract field.
- Every shipped default or picker-visible model id must have explicit
  model-context metadata in the runtime model seam; contract tests fail if the
  shipped model surface drifts ahead of that mapping.
- The shipped model catalog is backend-owned metadata. The Go TUI may request
  and render it, but it must not hardcode picker-visible model ids or provider
  defaults locally.
- The shipped provider surface currently includes `ollama`, `github`,
  `openai`, and `anthropic`, and new picker-visible providers must be added in
  the backend-owned catalog before the TUI can render them.
- Auth status and local secret-store shapes are backend-owned contract types in
  `contracts/auth.py`; runtime auth code and RPC models both import those
  shared contract models rather than defining or mirroring them locally.
- Local provider-secret resolution is backend-owned and uses this precedence:
  environment, then OS keychain, then explicit local secret file, then hard
  failure.
- `~/.jaca/config.json` is not a secret store. It may persist only non-secret
  preferences such as provider selection, model selection, trace mode, and
  base URLs.

`thinking` contract:

- allowed values: `true`, `false`, `"minimal"`, `"low"`, `"medium"`, `"high"`, `"xhigh"`
- `true` means enable provider-default thinking effort
- `false` means disable thinking where the provider supports turning it off
- string values request an explicit thinking effort level
- the canonical runtime passes `thinking` through PydanticAI model settings instead of encoding it in instructions

Canonical session resume authority:

- resumed runs use durable local `message_history` materialized from session state
- the canonical session runtime does not rely on provider-side server history for continuation
- provider-native history settings remain an internal model-seam capability, not part of the public session contract

## Tool Contract

Canonical tool set for the first maintained version:

- `read`
- `write`
- `edit`
- `shell`
- `grep`
- `ls`
- `find`

Rules:

- Tool names are stable once published.
- Tool inputs must be explicit and validated.
- Canonical public tool schema and validation live on the PydanticAI tool
  function signatures plus parameter constraints, not on duplicate public
  `*ToolInput` carrier models.
- Internal execution helpers may exist, but they are not part of the public
  tool contract.
- Python remains the semantic owner of tool schemas, validation, result
  shaping, activity metadata, and contract tests even if an internal helper in
  another language executes part of a tool path.
- `contracts/tools.py` should contain only shared public tool contract types
  such as canonical names and explicit tool error result shapes.
- Tool definitions sent to the model must have explicit top-level descriptions and parameter descriptions.
- Expected tool-domain failures must be explicit, model-visible results.
- Tools do not silently recover from invalid parameters or unsafe state.
- The runtime must not provide fallback tools or alternate tool behavior behind the same name.
- Tool registration and validation should prefer PydanticAI-native mechanisms unless the public contract requires a local wrapper.
- Workspace root is explicit backend configuration, not implicit process state.
- Workspace root sets the default base for relative paths; it is not a filesystem sandbox.

Expected tool-domain error result:

- fields: `ok`, `error_type`, `message`
- `ok` is always `false`
- ordinary operational failures should use this result shape instead of terminating the run
- uncaught exceptions and invalid state remain runtime failures

Initial executable tool slice:

- canonical registry names: `read`, `write`, `edit`, `shell`, `grep`, `ls`, `find`
- unknown tool names fail explicitly
- initial concrete tool implementations: `read`, `write`, `edit`, `shell`, `grep`, `ls`, `find`

`read` input contract:

- fields: `path`, `offset`, `limit`
- `path` must be a non-empty string
- `offset` is optional and, when present, must be a positive integer line number
- `limit` is optional and, when present, must be a positive integer line count

`read` behavior contract:

- reads one existing UTF-8 text file and returns a string
- resolves relative paths against the configured workspace root
- allows absolute paths and relative paths that resolve outside the workspace root
- `offset` is 1-indexed and line-based
- `limit` bounds the number of lines returned before any truncation ceiling is applied
- when `offset` or `limit` stops before end of file, the result must include an explicit continuation hint using the next `offset`
- when `limit` is omitted, `read` must still bound output size explicitly instead of dumping arbitrarily large files
- the canonical bounded-read ceiling is `2000` lines or `50 KiB`, whichever is hit first
- when the bounded-read ceiling is hit, the result must include an explicit continuation hint using the next `offset`
- if the first requested line alone exceeds the byte ceiling, the result must return an explicit recovery instruction telling the model to use `shell` for a narrower read
- missing files return an explicit tool error result
- directory paths return an explicit tool error result
- offsets beyond end-of-file return an explicit tool error result
- invalid UTF-8 content returns an explicit tool error result
- no silent truncation, binary fallback, or alternate decoding path

`write` input contract:

- fields: `path`, `content`
- `path` must be a non-empty string
- `content` must be a string and may be empty

`write` behavior contract:

- writes one UTF-8 text file and returns an explicit success message
- resolves relative paths against the configured workspace root
- allows absolute paths and relative paths that resolve outside the workspace root
- creates parent directories as needed
- overwrites an existing file completely
- directory targets return an explicit tool error result
- no append mode, merge mode, backup file, or silent alternate write path

`edit` input contract:

- fields: `path`, `old_text`, `new_text`
- `path` must be a non-empty string
- `old_text` must be a non-empty string
- `new_text` must be a string and may be empty

`edit` behavior contract:

- edits one existing UTF-8 text file by replacing exactly one occurrence of `old_text`
- resolves relative paths against the configured workspace root
- allows absolute paths and relative paths that resolve outside the workspace root
- tries exact matching first after BOM stripping and line-ending normalization
- if exact matching fails, falls back to normalized matching that trims trailing whitespace per line and normalizes common Unicode quote, dash, and space variants
- succeeds only when the chosen matching mode finds exactly one occurrence
- exact-match misses, normalized-match misses, ambiguous matches, and no-op replacements return an explicit tool error result
- allows deletion by using an empty `new_text`
- missing files, directory targets, and invalid UTF-8 return an explicit tool error result
- preserves a leading UTF-8 BOM when present
- restores the file's original line-ending style after writing
- when normalized fallback is used, matching is computed in normalized space but replacement is applied to the original LF-normalized file content so unmatched surrounding text is preserved
- on success, the model-facing tool result remains a short confirmation string, while any UI-only diff payload must travel through a separate internal metadata channel and be normalized into typed activity details before it becomes part of the public contract

`shell` input contract:

- fields: `command`, `timeout`
- `command` must be a non-empty string
- `timeout` is optional and, when present, must be a positive integer number of seconds

`shell` behavior contract:

- executes one local shell command in the configured workspace root using the active shell family (`posix`, which means Bash semantics, or `powershell`)
- sets command cwd to the configured workspace root, but does not sandbox filesystem access outside that root
- returns a JSON-compatible success result with fields `exit_code` and `output`
- successful `shell` results always use `exit_code: 0`
- `output` is the combined stdout and stderr decoded as UTF-8
- large `output` is tail-bounded to the last `2000` lines or `50 KiB`, whichever is hit first
- when `output` is truncated, the result must include an explicit notice with the temp-file path holding the full output
- non-zero exits return an explicit tool error result instead of a success payload
- timeout returns an explicit tool error result and includes captured output when available
- shell spawn failure and invalid UTF-8 output return an explicit tool error result
- no shell fallback, alternate decoder, or hidden retry path

`grep` input contract:

- fields: `pattern`, `path`, `glob`, `ignore_case`, `literal`, `limit`
- `pattern` must be a non-empty string
- `path` is optional and, when present, must be a non-empty string
- `glob` is optional and, when present, must be a non-empty string
- `ignore_case` must be a boolean
- `literal` must be a boolean
- `limit` must be a positive integer

`grep` behavior contract:

- searches UTF-8 text files for matching lines using local `rg`
- resolves relative `path` values against the configured workspace root
- allows absolute paths and relative paths that resolve outside the workspace root
- returns matching lines formatted as `<relative-or-absolute-path>:<line-number>:<line-text>`
- returns paths relative to the workspace root when possible
- respects `.gitignore` behavior from `rg`
- bounds output to at most `100` matches or `50 KiB`, whichever is hit first
- truncates any single displayed match line to `300` characters
- when output is bounded or line text is truncated, returns an explicit note telling the model to refine the search
- no matches return the explicit string `No matches found.`
- missing search paths, invalid `rg` execution, and non-UTF-8 decode failures return an explicit tool error result

`ls` input contract:

- fields: `path`, `limit`
- `path` is optional and, when present, must be a non-empty string
- `limit` must be a positive integer

`ls` behavior contract:

- lists one directory in alphabetical order
- resolves relative `path` values against the configured workspace root
- allows absolute paths and relative paths that resolve outside the workspace root
- includes dotfiles
- appends `/` to directory names
- returns the explicit string `(empty directory)` for empty directories
- bounds output to at most `500` entries or `50 KiB`, whichever is hit first
- when output is bounded, returns an explicit note telling the model how to ask for more
- missing paths and non-directory paths return an explicit tool error result

`find` input contract:

- fields: `pattern`, `path`, `limit`
- `pattern` must be a non-empty string
- `path` is optional and, when present, must be a non-empty string
- `limit` must be a positive integer

`find` behavior contract:

- finds files by glob pattern using ripgrep-backed file discovery
- resolves relative `path` values against the configured workspace root
- allows absolute paths and relative paths that resolve outside the workspace root
- searches one directory and returns result paths relative to that searched directory
- respects `.gitignore` behavior from `rg`
- returns the explicit string `No files found matching pattern.` when no files match
- bounds output to at most `1000` results or `50 KiB`, whichever is hit first
- when output is bounded, returns an explicit note telling the model how to ask for more or refine the pattern
- missing paths, non-directory search paths, invalid `rg` execution, and non-UTF-8 decode failures return an explicit tool error result

## Streamed Event Contract

Initial canonical event families:

- session lifecycle
- run lifecycle
- assistant text streaming
- tool execution lifecycle
- terminal success or terminal error

Rules:

- A run has exactly one terminal outcome: success or error.
- Errors are explicit and terminal.
- Event names and payloads should be simple, typed, and versionable.
- The runtime must not emit alternate fallback event shapes for older clients.
- The public event stream should represent the phases of a coding-agent run, not every internal PydanticAI event verbatim.
- Session lifecycle events may appear before `run_started` when the runtime
  performs work such as automatic session compaction at the resumed-run
  boundary.

Initial executable run slice:

- `session_compaction_started`
  - fields: `type`
- `session_compaction_completed`
  - fields: `type`, `compaction_id`, `summarized_through_run_id`
- `session_compaction_warning`
  - fields: `type`, `compaction_count`, `message`
- `run_started`
  - fields: `type`, `run_id`
- `assistant_text_delta`
  - fields: `type`, `run_id`, `delta`
- `run_succeeded`
  - fields: `type`, `run_id`, `output_text`, `input_tokens`, `output_tokens`, `total_tokens`, `context_window_used`
- `run_failed`
  - fields: `type`, `run_id`, `error_type`, `message`

Ordering rules for the initial slice:

- Successful text-only run: `run_started`, zero or more `assistant_text_delta`, `run_succeeded`
- Failed run: `run_started`, zero or more `assistant_text_delta`, `run_failed`
- `run_succeeded` and `run_failed` are mutually exclusive and terminal
- `run_succeeded` may also carry optional additive usage metadata when the model/provider reports it
- `input_tokens`, `output_tokens`, and `total_tokens` are optional integer token counts on `run_succeeded`
- `context_window_used` is an optional float ratio on `run_succeeded` and is omitted when the backend cannot determine the active model context window
- After a second-or-later durable automatic compaction, the runtime emits one
  explicit `session_compaction_warning` before `run_started` so clients can
  surface potential continuity degradation without inventing local heuristics
- Before any assistant text or tool lifecycle event is emitted, the runtime may hide one retryable transient failure and continue with the same public `run_id`
- Once any assistant text or tool lifecycle event has been emitted, the runtime must not retry the run automatically
- Consumers must not need to understand raw PydanticAI stream event kinds to consume this contract

Initial tool lifecycle slice:

- `tool_call_started`
  - fields: `type`, `run_id`, `tool_call_id`, `tool_name`, `args`, `args_valid`, `activity`
- `tool_call_updated`
  - fields: `type`, `run_id`, `tool_call_id`, `tool_name`, `partial_result`, `activity`
- `tool_call_succeeded`
  - fields: `type`, `run_id`, `tool_call_id`, `tool_name`, `result`, `activity`
- `tool_call_failed`
  - fields: `type`, `run_id`, `tool_call_id`, `tool_name`, `error_type`, `message`, `activity`

`activity` metadata contract:

- `activity` is optional and additive on tool lifecycle events
- it is backend-owned activity metadata for client rendering, replay, and traces
- when present, it must be typed and stable enough for non-TUI clients to consume without tool-specific heuristics
- v1 common fields:
  - `title`
  - `summary`
  - `duration_ms`
  - `details`
  - `group_kind`
- `title` is a terse backend-owned label for the tool action
- `summary` is optional and should stay trustworthy rather than aspirational
- `duration_ms` belongs on finished tool events and may also appear on `tool_call_updated`
- `details` is optional and, when present, must use typed per-tool metadata rather than an untyped bag
- `group_kind` is optional coarse presentation metadata from the backend; it may drive grouped transcript rendering in clients, but it is not a second event family and does not imply a public group identifier

Initial typed `details` slice for tool success activity:

- `shell`
  - `kind`, `command_preview`, `shell_family`, `timeout`, `exit_code`
- `read`
  - `kind`, `path`, `short_path`, `offset`, `limit`
- `write`
  - `kind`, `path`, `bytes_written`
- `edit`
  - `kind`, `path`, `diff`, `added_lines`, `removed_lines`
- `grep`
  - `kind`, `pattern`, `path`, `short_path`, `glob`, `ignore_case`, `literal`, `limit`
- `ls`
  - `kind`, `path`, `short_path`, `limit`
- `find`
  - `kind`, `pattern`, `path`, `short_path`, `limit`

Rules for the initial activity slice:

- v1 remains within the existing event families; no group or timeline event families are added
- `activity` must be derived from canonical tool semantics in the backend, not guessed in the frontend
- canonical tool success activity should be owned by the tools themselves and passed through an internal carrier such as `ToolReturn.metadata`; the runtime validates and normalizes that metadata before emitting public events
- started, updated, and failed/error-result activity should stay minimal: backend-owned `title`, optional `summary`, and `duration_ms` when applicable
- the runtime should not re-parse typed tool args into structured `details` for started, updated, or failed/error-result activity
- `group_kind` is the only current coarse grouping hint in the public contract; the backend does not expose a public `group_id`
- the current canonical `group_kind` value is `exploration` for exploration-style tools
- the shipped Go TUI may render consecutive exploration-tagged tool calls as a
  grouped `Exploring` / `Explored` transcript block while still consuming the
  underlying per-tool lifecycle events
- no untyped `artifacts` bag
- no absolute timestamps in the persisted public event contract
- existing consumers that ignore `activity` must continue to work unchanged
- framework-native carriers such as `ToolReturn.metadata` are allowed internally, but they are not themselves part of the public contract; the public contract begins only after the runtime validates and maps success-path metadata into typed `activity.details`

Canonical tool concurrency policy:

- `read`, `grep`, `find`, and `ls` are parallel-eligible
- `write`, `edit`, and `shell` are sequential only
- the runtime must set tool execution mode explicitly instead of relying on framework defaults
- provider-side `parallel_tool_calls` is enabled by default for canonical model/provider paths; carve-outs should be explicit when a specific model path is known not to support it correctly

Ordering rules for the tool slice:

- Each `tool_call_started` may be followed by zero or more matching `tool_call_updated` events and then exactly one matching `tool_call_succeeded` or `tool_call_failed`
- Expected tool-domain failures should normally be represented as `tool_call_succeeded` with an explicit error result object
- `RetryPromptPart` tool validation failures must be represented as `tool_call_succeeded` with an explicit error result object; they are not terminal by themselves
- the canonical agent keeps a small explicit model-visible tool-correction
  budget for recoverable tool-call mistakes such as invented tool names or
  malformed tool args
- once that bounded correction budget is exhausted, the current tool call must
  emit `tool_call_failed` and the run must end with `run_failed`
- `tool_call_failed` is reserved for uncaught tool failures or invalid runtime state and is terminal for the current run
- A tool exception that aborts the run must emit `tool_call_failed` before `run_failed`
- A tool result must match an existing pending `tool_call_started`; tool name mismatches or orphaned tool results are invalid state and fail the run explicitly
- Tool args and tool results in the public contract must be JSON-compatible

## Session Contract

Initial canonical session contract:

- append-only JSONL
- explicit session header with authoritative workspace metadata
- explicit run, native message-history, and event entries
- no automatic migration of old local session states

Rules:

- Invalid session data should fail load explicitly.
- Session format changes require an ADR and test updates.
- Do not add silent repair logic.
- Session persistence should preserve coding-agent continuity without importing legacy session-tree or migration behavior by default.
- A session belongs to exactly one resolved workspace root; authoritative session loads must provide that workspace root and fail on mismatch.
- Public run events remain part of the persisted contract, but resume-capable conversation state must use the native PydanticAI `ModelMessage` history persisted alongside them.

Initial executable session slice:

- `session_header`
  - fields: `type`, `version`, `workspace_root`
- `session_info`
  - fields: `type`, `name`
  - `name` is the backend-normalized durable human session name
- `session_fork`
  - fields: `type`, `forked_from_session_id`, `forked_from_run_id`
  - records direct parent lineage for a forked session
  - `forked_from_run_id` is optional and, when present, identifies the latest
    completed parent run visible at fork time
- `session_run`
  - fields: `type`, `run_id`, `prompt`, `thinking`
  - `thinking` is optional and stores the effective thinking setting for that run
- `session_messages`
  - fields: `type`, `run_id`, `messages`
  - `messages` must be the native PydanticAI `ModelMessage` list for that run
- `session_event`
  - fields: `type`, `run_id`, `event`
  - `event` must be one canonical streamed run event payload
- `session_compaction`
  - fields: `type`, `compaction_id`, `summarized_through_run_id`, `first_kept_run_id`, `summary`
  - `summarized_through_run_id` must reference an existing persisted `run_id`
  - `first_kept_run_id`, when present, must reference an existing persisted
    `run_id` strictly after `summarized_through_run_id`
  - `summary` is structured durable compaction state, not arbitrary untyped metadata

Ordering rules for the session slice:

- The first line must be exactly one `session_header`
- `session_fork` may appear at most once and, when present, must be the second
  line immediately after `session_header`
- `session_info` may appear only at completed-run boundaries, never in the middle of a run
- `session_info.name` is unique within the current workspace-backed session shard
- Each completed `session_run` is followed by one or more `session_event` lines for the same `run_id` and then exactly one trailing `session_messages` line for that run
- A trailing run without `session_messages` is an incomplete run and authoritative session load must fail hard
- `session_compaction` may appear only at a completed run boundary, never in the middle of a run
- `session_compaction` entries are append-only and must not move the summary boundary backward
- Authoritative session loads must provide the expected workspace root and it must match the persisted `session_header.workspace_root` exactly
- Session resume semantics must reconstruct effective conversation context from the latest compaction summary plus retained `session_messages` when a compaction entry exists; retained messages start at `first_kept_run_id` when present, otherwise they start strictly after `summarized_through_run_id`
- Durable cross-run compaction must be materialized into resume `message_history` before the next run starts; only run-local compaction uses PydanticAI `history_processors`
- Durable compaction summaries must carry backend-owned deterministic survival
  state in addition to model-written prose: `read_paths` for explicitly read
  files, `modified_paths` for explicitly written or edited files,
  `recent_shell_commands` for recent shell command/outcome snapshots, and
  `recent_failures` for recent failed tool calls or terminal run failures
- Durable compaction summary generation must use the model only for narrative
  fields such as `current_objective`, `established_facts`, `user_preferences`,
  `important_paths`, `open_questions`, and `unresolved_work`; backend-owned
  deterministic fields must be derived from persisted run events instead of
  model recall
- Run-local history processors may compact current-run tool-return content for
  the model when context pressure grows, but the persistence layer must restore
  the original raw tool-return content before writing `session_messages`
- `session.compact` and automatic compaction must generate summaries through a model call; the persistence layer must not invent placeholder summaries locally
- When a new run omits `thinking`, the session-backed runtime inherits the most recent persisted non-null thinking setting from that session
- Session-backed runtime streaming must append `session_run` and `session_event` entries incrementally as the run streams and append `session_messages` only after terminal completion
- If cancellation unwinds through `stream_session_run_events()`, the runtime must persist terminal `tool_call_failed` events for any still-pending tool calls and then persist terminal `run_failed` before finalization
- Persisted `session_messages` for a terminal run must remain replay-safe; they must not contain unresolved tool calls
- Persisted `session_messages` for a failed terminal run must also exclude unresolved failed-correction tails such as trailing `RetryPromptPart` repair prompts and the matching invalid `ToolCallPart` suffix that caused a provider-side abort; future resumed runs must continue only from the last known-good message boundary
- Trimming poisoned failed-correction tails from persisted `session_messages` must not delete observability data: the original streamed `session_event` sequence and provider traces remain authoritative for failure forensics
- If cancellation interrupts message capture mid-tool-call, finalization must strip those unresolved tool parts before writing `session_messages`
- Persisted events for a run must satisfy the streamed run contract, including exactly one terminal outcome
- Persisted `session_event` payloads must preserve any tool `activity` metadata unchanged
- Appending a new run must preserve all existing lines and write the header only once
- Synthetic compaction-summary messages used at runtime must not be persisted back into `session_messages`
- Before a resumed run starts, the runtime may append one automatic `session_compaction` entry when measured local resume history plus reserve crosses the configured fraction of the effective active model context window after compaction-output headroom is reserved
- Automatic durable compaction may preserve one bounded raw tail run via `first_kept_run_id`; future automatic trigger decisions must then count only completed runs beyond that retained boundary as new work
- After three consecutive automatic compaction failures for one session, the runtime blocks further automatic compaction attempts for that session and fails hard until the user reduces context or starts a new session

## RPC Contract

Initial canonical RPC transport:

- JSON over stdio
- explicit command names
- explicit response and event payloads
- server-generated opaque session ids
- strict error responses for invalid commands or invalid state

Rules:

- No compatibility aliases unless deliberately chosen and documented.
- No hidden fallback commands.
- Protocol changes require an ADR and tests.
- RPC exposes the backend contract only; UI-specific command surfaces are out of scope unless deliberately added later.

Initial executable RPC slice:

- request line
  - fields: `id`, `command`, `payload`
  - initial commands:
    - `auth.status` with payload `{}`
    - `auth.set` with payload `{"provider": <provider-name>, "secret": <string>, "storage": "keychain" | "file"}`
    - `auth.clear` with payload `{"provider": <provider-name>}`
    - `session.create` with payload `{}`
    - `session.name` with payload `{"session_id": <opaque-lowercase-hex-string>, "name": <string>}`
    - `session.preview` with payload `{"session_id": <opaque-lowercase-hex-string>}`
    - `session.compact` with payload `{"session_id": <opaque-lowercase-hex-string>}`
    - `run.start` with payload `{"session_id": <opaque-lowercase-hex-string>, "prompt": <string>, "thinking": <optional-thinking-setting>}`
- `rpc_response`
  - fields: `type`, `id`, `response`
  - initial response payloads:
    - `{"providers": [{"provider": <provider-name>, "configured": <bool>, "source": "env" | "keychain" | "file" | "none", "env_key": <provider-env-var>}, ...], "local_secret_store": {"available": <bool>, "message": <optional-string>, "file_store_path": <abs-path>}}`
    - `{"status": {"provider": <provider-name>, "configured": <bool>, "source": "env" | "keychain" | "file" | "none", "env_key": <provider-env-var>}}` for `auth.set`
    - `{"status": {"provider": <provider-name>, "configured": <bool>, "source": "env" | "keychain" | "file" | "none", "env_key": <provider-env-var>}}` for `auth.clear`
    - `{"session_id": <opaque-lowercase-hex-string>}`
    - `{"session_id": <opaque-lowercase-hex-string>, "name": <backend-normalized-session-name>}` for `session.name`
    - `{"session_id": <opaque-lowercase-hex-string>, "entries": [{"kind": "user" | "assistant" | "error", "text": <string>}], "truncated": <bool>}` for `session.preview`
    - `{"compaction_id": <opaque-lowercase-hex-string>, "summarized_through_run_id": <run_id>, "first_kept_run_id": <optional-run_id>, "summary": <structured-compaction-summary>}`
- `rpc_event`
  - fields: `type`, `id`, `event`
  - `event` must be one canonical streamed run event payload or session lifecycle event payload
- `rpc_error`
  - fields: `type`, `id`, `error_type`, `message`

Ordering rules for the RPC slice:

- A valid `auth.status` request yields exactly one `rpc_response` with one
  backend-authored status object per shipped provider plus one backend-authored
  `local_secret_store` object describing whether interactive local secret
  storage is available on this machine and where the explicit local secret file
  would live
- A valid `auth.set` request yields exactly one `rpc_response` and stores the
  secret in the requested backend-owned local secret store without echoing the
  secret back
- A valid `auth.clear` request yields exactly one `rpc_response` and removes
  the stored local secret for that provider from both keychain and explicit
  local file storage
- A valid `session.create` request yields exactly one `rpc_response` containing a server-generated opaque `session_id`
- A valid `session.name` request must reference an existing `session_id`, append one backend-normalized `session_info` entry when the requested name changes, enforce workspace-local name uniqueness, and yield exactly one `rpc_response` containing that normalized session name
- A valid `session.preview` request must reference an existing `session_id` and yields exactly one `rpc_response` containing a bounded recent-history preview derived from durable session runs; it is a presentation helper and does not change resume authority
- A valid `session.compact` request must reference an existing `session_id` and yields exactly one `rpc_response` describing the newly appended compaction entry
- `session.compact` responses must include the durable summary's backend-owned
  deterministic fields (`read_paths`, `modified_paths`,
  `recent_shell_commands`, and `recent_failures`) alongside the narrative
  fields
- If model-driven compaction summary generation fails, `session.compact` fails hard; it does not append a placeholder summary
- A valid `run.start` request must reference an existing `session_id` and yields zero or more `rpc_event` lines whose embedded events satisfy the streamed run contract
- Session lifecycle `rpc_event` payloads such as `session_compaction_started` and `session_compaction_completed` may appear before `run_started`
- `run.start` on an existing session is the canonical continue operation; there is no separate `session.continue` command
- Forking is a wrapper-level session-store operation today: the Python launcher
  may create a new session file with one `session_fork` entry before the TUI
  starts, but RPC clients do not have a separate `session.fork` command yet
- A valid `run.start` request may include `thinking`; when omitted, session-backed execution inherits the latest persisted thinking setting for that session when present
- A valid request that ends in run failure still yields `rpc_event` lines ending in `run_failed`; it does not switch to `rpc_error`
- Clients must not provide filesystem paths or workspace identifiers in the RPC session contract
- Invalid JSON yields exactly one `rpc_error` with `id: null` and `error_type: InvalidJSON`
- Invalid command or payload yields exactly one `rpc_error` with the parsed request `id` when available and `error_type: InvalidRequest`
- Unknown `session_id` yields exactly one `rpc_error` with `error_type: UnknownSession`
- Persisted-but-invalid session state yields exactly one `rpc_error` with `error_type: InvalidSession`
- Unexpected internal server failures yield exactly one `rpc_error` with `error_type: InternalError`

## Failure Semantics

- No fallback behavior, ever.
- Fail hard on invalid state, invalid inputs, and unsupported operations.
- Prefer explicit recovery instructions in error payloads over automatic retries or silent behavior changes.
- The canonical path should be the only path.
- The canonical runtime is unbounded within a single run and does not impose backend-level request or tool-call ceilings.
- Compaction is the planned mechanism for managing long-lived session growth; it does not constrain an in-flight run.
- Expected tool-domain failures should be returned to the model as explicit tool result objects instead of ending the run immediately.
- `stream_run_events` intentionally converts pre-terminal runtime exceptions into canonical failure events instead of leaking raw exceptions through the public stream.
- `stream_run_events` may retry one retryable transient timeout or transport/provider connection failure before any assistant text or tool lifecycle event escapes the public stream.
- If a pre-terminal exception occurs while tool calls are still pending, each pending tool call emits `tool_call_failed` before the terminal `run_failed`.
- An exception after `run_succeeded` is invalid state and is raised instead of being re-encoded as another event.
