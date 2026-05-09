# Contracts

read_when: you are defining behavior, writing tests, or deciding what must remain stable

## Purpose

This document defines the canonical external contract for the coding-agent backend. Tests should protect this contract before they protect internal implementation details.

The contract preserves the backend-facing behavior of a pi-style coding agent while remaining independent from pi-mono's internal architecture. Internally, the implementation should prefer direct PydanticAI primitives and expose one simplified, stable public contract.

## Prompt Context Contract

Canonical prompt context for the maintained version:

- base product prompt instructions assembled from named sections
- dynamic model-visible project-instruction messages from workspace-root
  `AGENTS.md` and `CLAUDE.md`, when present
- dynamic runtime-context messages containing current date, timezone,
  workspace root, shell family, model, thinking, and effective capability
  posture when that posture is part of the visible framing for the run
- a mode/task layer seam, currently active only as the default no-op mode

Rules:

- the canonical prompt context must be assembled through one Python-owned layer
  builder path
- base product prompt sections must have explicit names and stable ordering
- dynamic prompt context must be explicit, reproducible, and model-visible
- runtime-context injection is dynamic contextual history, not baked into the
  static baseline prompt
- project-doc injection is runtime-owned contextual history, not baked into the static baseline prompt
- project-doc injection is bounded and deterministic
- the mode/task layer must not grow new behavior unless a task-specific
  behavior gap justifies it
- prompt quality is a product-owner review responsibility; do not add
  arbitrary prompt character-budget gates as product policy
- the canonical agent prompt must explicitly forbid claiming file side effects without tool evidence
- the canonical agent prompt must explicitly instruct the model to verify code changes or required file outputs before concluding
- when the user asks to run tests, lint, or another obvious verification step,
  the model should run the narrowest relevant command directly instead of
  searching or diff-reading first unless the command or scope is ambiguous
- the canonical response style must be brief, direct, and outcome-first by
  default
- the model should not restate the user's request or narrate routine process
  unless that context is necessary
- final answers should usually state the outcome, verification, and blockers
  without a process recap
- longer explanations remain allowed when the user asks for detail or the task
  needs it

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
- The shipped OpenAI GPT-5 family uses `openai-responses:*` model ids in the
  backend-owned catalog rather than `openai:*`, because the canonical GPT-5
  tool path in this repo is the Responses API.
- The shipped provider surface currently includes only `openai` and
  `anthropic`. Subscription-backed OAuth model lanes such as
  `openai-responses:gpt-5.2-chatgpt`,
  `openai-responses:gpt-5.3-codex-chatgpt`,
  `openai-responses:gpt-5.4-chatgpt`, and
  `openai-responses:gpt-5.4-mini-chatgpt` remain backend-owned model ids under
  the `openai` provider catalog, while unsupported OAuth GPT-5 lanes are
  removed and must fail fast if referenced.
- Auth status and local secret-store shapes are backend-owned contract types in
  `contracts/auth.py`; runtime auth code and RPC models both import those
  shared contract models rather than defining or mirroring them locally.
- Provider readiness is backend-owned too. It is computed from the effective
  provider path, endpoint configuration, and local secret-store state rather
  than inferred from forgiving provider construction.
- Local provider-secret resolution is backend-owned and uses this precedence:
  environment, then the explicit local auth file, then hard failure.
- `~/.jaca/config.json` is not a secret store. It may persist only non-secret
  preferences such as provider selection, model selection, trace mode, and
  base URLs.
- API-key file setup is backend-owned too. `auth.prepare_file` must ensure the
  canonical local auth file exists as valid JSON, then return the raw file
  path plus exact file and entry snippets for the selected supported provider.

OAuth login RPC contract:

- OAuth login is backend-owned and uses explicit RPC commands rather than
  frontend polling loops.
- `auth.login_openai_codex.start` returns the browser URL and flow id.
- `auth.login_openai_codex.complete` is only the manual recovery path for a
  pasted redirect URL or authorization code.
- `auth.login_openai_codex.wait` is the canonical completion path. It blocks
  until the browser callback or manual completion resolves, then returns the
  final provider status.
- Manual OpenAI completion and background browser-callback completion must
  resolve the same canonical login result; the shell must not race two
  different notions of success.

API-key setup RPC contract:

- `auth.prepare_file` is the canonical first-run and `/auth <provider>` path
  for API-key setup.
- It returns the selected provider, env key, raw local auth-file path, whether
  the file was created during the call, a complete file snippet for an empty
  file, and a single-entry snippet for an existing JSON object.
- The Go TUI must render that backend-owned path and snippet verbatim instead
  of inventing file URLs or provider-specific setup text locally.

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

## Sandbox And Approval Contract

Initial canonical control-plane slice:

- `SandboxPolicy`
- `ApprovalPolicy`
- `EffectiveCapabilities`
- `PermissionState`
- `ApprovalRequest`
- `ApprovalDecision`

Rules:

- Sandbox and approval are explicit backend-owned policies, not ambient runtime
  facts inferred from UI mode or local process state.
- Omitted sandbox or approval settings mean "use backend defaults"; explicit
  policy values must validate fail-fast instead of degrading silently.
- Sandboxing is an explicit policy or mode, not silent narrowing of canonical
  tool behavior behind the same name.
- `EffectiveCapabilities` is the normalized contract view of what is actually
  true for the current run: filesystem posture, network posture, execution
  isolation posture, and approval posture.
- `PermissionState` is the live backend-owned control-plane snapshot composed
  of the current sandbox policy, approval policy, and normalized effective
  capabilities.
- Capability changes that materially affect what the model can do must be made
  explicit through backend-owned effective capabilities rather than inferred in
  Go or hidden inside executor implementation detail.
- Approval is control-plane policy. Executor backends are data-plane
  implementation detail.
- The canonical backend must not grow a side-channel unsandboxed shell path
  parallel to the main sandboxed tool contract.
- Executor-specific detail such as container ids, VM handles, or transport
  wiring stays internal unless it changes the public contract.

Initial sandbox policy modes:

- `read_only`
  - read-only filesystem posture
  - restricted network posture
  - sandboxed execution posture
- `workspace_write`
  - workspace-write filesystem posture
  - restricted or enabled network posture
  - sandboxed execution posture
- `danger_full_access`
  - full-access filesystem posture
  - enabled network posture
  - unsandboxed execution posture
- `external`
  - full-access filesystem posture from JACA's point of view
  - restricted or enabled network posture
  - sandboxed execution posture enforced outside JACA

Initial approval policy modes:

- `never`
  - the backend must not pause for approval; if approval would otherwise be
    required, the action fails explicitly
- `on_escalation`
  - the backend pauses only when requested capabilities exceed the current
    allowed posture
- `always`
  - the backend requires approval before high-risk execution even when the
    requested capabilities are otherwise allowed

Approval policy shape:

- `ApprovalPolicy.mode` is the canonical default approval mode
- `ApprovalPolicy.by_kind` is an optional backend-owned override map keyed by
  approval request kind:
  - `command_execution`
  - `file_change`
  - `permission_grant`
- request kind is the only canonical approval-policy granularity right now
  - do not add tool-name, tool-class, skill-family, MCP-specific, or
    user-input/elicitation policy dimensions to the contract without a new
    explicit contract decision
- when a request-kind override is present, that override replaces the default
  mode only for that approval class
- `never` means "do not prompt, and do not implicitly allow the requested
  delta"
  - if the resolved request-kind mode is `never` and the request would need a
    permission delta, the backend returns an explicit policy denial instead of
    widening capabilities silently
- empty explicit `by_kind` payloads are invalid and must fail fast
- `EffectiveCapabilities` still carries the default `approval_mode`, and now
  also carries `approval_by_kind` so the model-visible runtime posture remains
  honest when request-kind overrides are active

Approval carrier rules:

- approval requests and decisions are backend-owned typed contract models
- approval requests may also carry:
  - `display_subject`
    - the minimal user-facing subject for the approval prompt
  - `options`
    - backend-authored approval choices such as:
      - exact one-time approval
      - safe session-wide approval when the backend can derive a reusable
        boundary
      - deny
- approval requests may carry both:
  - `requested_permissions`
    - the aggregate permission delta being requested
  - `requested_grants`
    - the explicit scoped grants that make up that aggregate delta
- approval decisions may carry both:
  - `granted_permissions`
    - the aggregate permission delta that was granted
  - `granted_grants`
    - the explicit scoped grants that make up that granted delta
- approval decisions may also carry:
  - `option_id`
    - the specific backend-authored approval option the user selected
- scoped grants are typed as `SandboxPermissionGrant` and currently use
  `once` or `session` scope
- `SandboxPermissionGrant` may also carry `command_prefix` when a session-wide
  grant is safely generalized to a reusable command family such as `curl`
- when approval submitters send a lean approved or denied decision, the backend
  normalizes the final decision against the request before persistence or tool
  continuation
  - denied decisions must not include grants
  - approved decisions without explicit granted fields are normalized to the
    first approved backend option when options are present; this keeps
    `Allow once` as the canonical conservative default for lean approve flows
  - explicit option selection may widen that default to a safe session-wide
    grant when the backend exposed one
  - this keeps current submit flows small while preserving an explicit durable
    contract for resolved approvals
- the current approval request taxonomy is:
  - `command_execution`
    - used for shell command approvals
    - carries command-specific context such as the command string, cwd, and
      shell family
  - `file_change`
    - used for backend-owned file mutations such as `write` and `edit`
    - carries the target path and change kind
  - `permission_grant`
    - used when the backend is asking to widen capability itself rather than
      approve a concrete command
    - currently used for approval-gated outside-workspace read access in the
      read-only worker path
- approval decisions must refer to one request id and produce one explicit
  result
- approval lifecycle semantics belong to Python-owned RPC and streamed-event
  contracts, not to Go-local state machines
- `approval.submit` resolves a live pending approval request; by default,
  denial must be returned to the waiting tool through the canonical backend
  tool path so the model can decide whether to adapt, ask for a narrower
  action, or stop
  - backend guardrails may still terminate the run for hard policy stops or
    repeated denied-retry loops
- tools request approval through backend-owned runtime deps; the runtime emits
  `approval_requested` and `approval_resolved` events and preserves normal
  terminal run semantics
  - approval denial is not a stdio-only shortcut or a Go-local decision
  - approval denial is a first-class tool outcome, not a tool error
  - by default the run may continue and the model may recover within the same
    run
- the current scoped-grant behavior is:
  - shell network approvals request `once` grants
    - when the backend can safely derive a reusable command family, the prompt
      may also expose a session-wide option such as `Allow curl for this
      session`; that session option resolves to a grant with
      `command_prefix=("curl",)`
  - outside-workspace filesystem approvals request `session` grants
    - prompts describe those reusable filesystem grants in human terms such as
      `Allow reads under /tmp for this session` rather than exposing glob
      syntax
  - only `session` grants populate session permission memory
- denied tool results should stay operationally minimal
  - the current model-visible denial shape is intentionally limited to the
    blocked request and whether retrying the same request is allowed
  - the contract must not expose a denial-source taxonomy such as `user`,
    `policy`, or `repeat_guardrail` unless we later prove that the model needs
    that distinction to choose better follow-up actions
- live permission state is distinct from durable turn-context history:
  - `PermissionState` is live control-plane state for RPC and approval flows
  - when no session is active, `permission.get` / `permission.set` operate on
    the workspace default permission state
  - `session.create` inherits the current workspace default permission state
  - `session_turn_context.effective_capabilities` remains the durable
    model-visible snapshot written after completed runs
- workspace trust is a separate startup gate:
  - it is stored per repo-root trust target rather than per nested cwd
  - it blocks repo instruction loading and session bootstrap before any run
    starts
  - it does not grant sandbox or approval capability by itself
- until a restricted executor backend lands:
  - the workspace default permission state is `workspace_write` with
    `approval_policy=on_escalation`
  - execution isolation for shell remains the truthful host value
    (`unsandboxed`)
  - backend-owned file tools may still enforce narrower filesystem posture
    directly through approval-gated path policy
  - `shell` approval is currently modeled as `command_execution`; it may gate
    obvious network access or outside-workspace writes, but approved shell
    execution still runs on the host path until the restricted executor backend
    lands
  - the current backend-owned shell escalation heuristics are documented in
    `permission-execution.md` so they can evolve explicitly instead of staying
    implicit in implementation detail

## Tool Contract

Canonical tool set for the first maintained version:

- `read`
- `write`
- `edit`
- `shell`
- `grep`
- `ls`
- `find`
- `subagent`

Onboarding-mode extension tools:

- `ask_mcq_question`
- `generate_mcq_from_teaching_packets`
- `publish_teaching_packet`

Rules:

- Tool names are stable once published.
- The current canonical tools remain directly model-visible on default runs.
  Do not hide them behind a tool-search or deferred-loading indirection
  without a separate evidence-backed contract change.
- Mode-specific tools must have an explicit backend-owned visibility policy.
  They are not globally model-visible unless the backend enables that mode for
  the run.
- Session mode is durable backend state. When `run.start` omits `mode`, the
  backend must inherit the persisted session mode rather than asking the shell
  to infer it.
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
  such as canonical names and explicit tool failure/denial result shapes.
- Tool definitions sent to the model must have explicit top-level descriptions and parameter descriptions.
- Expected tool-domain failures must be explicit, model-visible results.
- Tools do not silently recover from invalid parameters or unsafe state.
- The runtime must not provide fallback tools or alternate tool behavior behind the same name.
- Tool registration and validation should prefer PydanticAI-native mechanisms unless the public contract requires a local wrapper.
- Workspace root is explicit backend configuration, not implicit process state.
- Workspace root sets the default base for relative paths; it is not a filesystem sandbox.
- Future non-core tools such as MCP, app, work-graph, or optional provider
  tools must define a Python-owned visibility policy before they are added to
  the model-visible surface.
- If future dynamic tools are large enough to threaten latency or context
  budget, the backend should introduce a deferred dynamic tool surface instead
  of sending every optional tool schema on every request.

Expected tool-domain error result:

- fields: `ok`, `error_type`, `message`
- `ok` is always `false`
- ordinary operational failures should use this result shape instead of terminating the run
- uncaught exceptions and invalid state remain runtime failures

Expected tool-domain denial result:

- fields:
  - `ok`
  - `outcome`
  - `denial_type`
  - `message`
  - `approval_kind`
    - optional typed approval surface such as `command_execution`,
      `file_change`, or `permission_grant`
  - `subject`
    - optional exact blocked subject such as `curl https://example.com`
  - `retry_same_request_allowed`
    - optional boolean guardrail hint for whether the exact same denied request
      may be retried immediately
- `ok` is always `false`
- `outcome` is always `denied`
- `denial_type` distinguishes backend denial causes such as:
  - `approval_denied`
    - a human reviewer denied an approval request
  - `policy_denied`
    - the current approval policy forbids prompting for the requested delta
- policy denials should use this result shape instead of being reported as
  tool errors
- exact repeated denied approval requests within the same run should be denied
  again without re-prompting the user

Initial executable tool slice:

- canonical registry names: `read`, `write`, `edit`, `shell`, `grep`, `ls`, `find`, `subagent`
- onboarding-mode extension registry names: `ask_mcq_question`, `generate_mcq_from_teaching_packets`, `publish_teaching_packet`
- unknown tool names fail explicitly
- initial concrete tool implementations: `read`, `write`, `edit`, `shell`, `grep`, `ls`, `find`, `subagent`, `ask_mcq_question`, `generate_mcq_from_teaching_packets`, `publish_teaching_packet`
- `publish_teaching_packet` accepts only code-file snippet refs; documentation
  paths such as `docs/*`, `README*`, `AGENTS.md`, `CLAUDE.md`, or markdown-like
  files must fail explicitly
- `publish_teaching_packet` requires:
  - one short `title`
  - one `concept`
  - one or more `relationships`
  - `2..5` code snippet refs using `path`, `start_line`, and `end_line`
- `publish_teaching_packet` returns a durable `packet_id`
- `generate_mcq_from_teaching_packets` accepts `1..3` `packet_id` values
  published earlier in the same active run and returns:
  - `packet_ids`
  - `question`
  - four `options`
  - `correct_index`
  - `explanation`
- `ask_mcq_question` must link to one or more `packet_id` values that were
  published earlier in the same active run
- the backend must fail `ask_mcq_question` explicitly when any linked
  `packet_id` is missing, duplicated, blank, or from a different run

Code Mode contract:

- first-slice Code Mode registry names are `exec` and `wait`
- `exec` and `wait` are not part of the default canonical tool set
- the backend must expose `exec` and `wait` only through an explicit
  backend-owned visibility policy
- the current first visibility policy is explicit `tool_names` selection at
  agent construction; Code Mode is not a durable run mode yet
- Code Mode lifecycle states are `running`, `yielded`, `completed`, `failed`,
  and `terminated`
- terminal Code Mode states are `completed`, `failed`, and `terminated`
- `CodeModeCellResult` may carry a typed error only when the state is `failed`
- Code Mode cells must call canonical tools through a backend-owned bridge
  rather than direct filesystem, shell, permission, session, or transcript
  access
- the Code Mode bridge surface exposes `read`, `grep`, `ls`, `find`, `write`,
  `edit`, and `shell`
- when no test runner is injected, `exec` uses the default Python subprocess
  source runtime
- the default source runtime executes source as async Python with top-level
  `await`, `emit`, `return_result`, and the bridged `tools.*` APIs
- the default source runtime normalizes positional tool arguments and a single
  positional argument dictionary into the same named backend tool arguments;
  ambiguous mixed forms fail explicitly
- the default source runtime is run-local and persistent across cells for the
  same `WorkspaceDeps`; variables, helper functions, and allowlisted imports
  defined in one completed cell remain available to later cells in that run
- the default source runtime exposes a small allowlist of standard-library
  modules for deterministic parsing and computation: `json`, `re`, `math`,
  `collections`, `statistics`, `itertools`, `functools`, and `decimal`
- the default source runtime does not expose arbitrary imports, `open`, or
  direct subprocess APIs, but this restriction is not a complete security
  sandbox
- nested tool calls must preserve normal workspace, sandbox, approval,
  permission, activity, and failure semantics
- runtime failures are represented as failed `CodeModeCellResult` payloads on
  the parent `exec` tool result, so the current model can inspect and recover
- nested Code Mode activity is surfaced as compact `tool_call_updated` events
  on the parent `exec` tool call using typed `code_mode` activity details
- failed nested bridge calls must surface as compact `exec` updates with
  `nested_status` set to `failed`
- nested bridge calls must not emit raw nested-tool updates, such as shell
  streaming output, directly into the public stream
- the first Code Mode streaming slice must not emit nested top-level
  `tool_call_started` or `tool_call_succeeded` events for `read`, `grep`, or
  `shell`
- Code Mode may later call `subagent` only as a nested canonical tool call
  through the backend bridge; the ordinary subagent contract would still own
  child-run creation, spawn mode, capability limits, parent session/run
  provenance, tool activity, and failure behavior
- Code Mode must not implement a separate child-agent system behind the bridge
- Code Mode contract direction and non-goals are documented in
  `code-mode.md`

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

`subagent` input contract:

- fields: `name`, `task`, `role`, `spawn_mode`, `capability`
- `name` must be a non-empty kebab-case session name
- `task` must be a non-empty string
- `task` should state the exact child goal, relevant files or artifacts,
  constraints, stop condition, and desired output shape when needed
- callers may omit `role`; omitted values default to `general`, otherwise the
  value must be one of `general`, `explore`, or `verification`
- callers may omit `spawn_mode`; omitted values default to `fork`,
  otherwise the value must be `fresh` or `fork`
- callers may omit `capability`; omitted values default to `default`,
  otherwise the value must be `default` or `shell`

`subagent` behavior contract:

- spawns exactly one ephemeral child run and waits for it to finish before returning
- child runs are non-recursive
- root run deps carry the durable parent `session_id`, and the runtime binds the
  active parent `run_id` before tools execute
- child lineage must be truthful: ephemeral child scope records the real parent
  `session_id`, real parent `run_id`, and the spawning parent `tool_call_id`
  when available
- `spawn_mode=fresh` gives the child a fresh runtime/project frame with no
  inherited parent conversation history
- `spawn_mode=fork` gives the child a sanitized snapshot of the parent's
  current conversation history with unresolved tool calls removed and old
  system-prompt state stripped
- child runs inherit the parent run's workspace root, shell family, model, thinking, current date, and timezone
- child runs never get `write` or `edit`
- `capability=default` exposes `read`, `grep`, `find`, and `ls`
- `capability=shell` additionally exposes `shell`
- child runs do not create durable session files or public session commands in this first slice
- subagent spawning is allowed only from root runs; nested child runs return an explicit tool error result
- child output is plain text; any desired structure belongs in the parent-provided task prompt
- empty child output returns an explicit tool error result
- successful results return an object with `ok: true`, `name`, `role`,
  `spawn_mode`, `capability`, backend-derived `summary_text`, and raw
  `output_text`
- child run failures surface as explicit tool error results rather than crashing the parent run
- parent guidance should treat subagent as a focused delegation tool:
  use it for one bounded subquestion, prefer `spawn_mode=fork` so the child
  can build on the parent's current conversation context, use
  `spawn_mode=fresh` only for an independent repo/artifact pass, request
  `shell` only when the child needs local commands or scripts, and avoid
  broad multi-step work
- parent transcript rendering for subagent work is backend-owned and compact:
  streamed activity carries typed child semantics (`role`, `spawn_mode`,
  `capability`) plus bounded `preview_lines` and `preview_terminal` so
  clients can render one stable parent block with optional `|` / `└` detail
  lines instead of replaying the child transcript

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
- Tool updates may arrive before the matching tool-call start is emitted from
  the framework. The runtime must buffer those early updates until the
  canonical `tool_call_started` event exists, then emit them in order instead
  of crashing on scheduler timing.
- Session lifecycle events may appear before `run_started` when the runtime
  performs work such as automatic session compaction at the resumed-run
  boundary.
- `session_turn_context_status` may also appear before `run_started` on
  session-backed runs so clients can see whether the persisted runtime-framing
  baseline was missing, reused, or cleared for that run.

Initial executable run slice:

- `session_turn_context_status`
  - fields: `type`, `status`, `reason`, `persisted_run_id`
- `session_compaction_started`
  - fields: `type`, `budget`
- `session_compaction_completed`
  - fields:
    - `type`
    - `compaction_id`
    - `compacted_through_run_id`
    - `budget_before`
    - `budget_after`
    - `estimated_tokens_saved`
    - `estimated_percent_saved`
    - `estimated_headroom_gain_tokens`
- `run_started`
  - fields: `type`, `run_id`
- `assistant_text_delta`
  - fields: `type`, `run_id`, `delta`
- `run_succeeded`
  - fields: `type`, `run_id`, `output_text`, `input_tokens`, `output_tokens`, `total_tokens`, `context_window_used`, `next_request_context_window_used`, `transcript_summary`
- `run_failed`
  - fields: `type`, `run_id`, `error_type`, `message`

Ordering rules for the initial slice:

- Successful text-only run: `run_started`, zero or more `assistant_text_delta`, `run_succeeded`
- Failed run: `run_started`, zero or more `assistant_text_delta`, `run_failed`
- `run_succeeded` and `run_failed` are mutually exclusive and terminal
- `run_succeeded` may also carry optional additive usage metadata when the model/provider reports it
- `input_tokens`, `output_tokens`, and `total_tokens` are optional integer token counts on `run_succeeded`
- `context_window_used` is an optional float ratio on `run_succeeded` and is omitted when the backend cannot determine the active model context window
- `next_request_context_window_used` is an optional float ratio on `run_succeeded` representing the backend estimate of the next resumed request substrate, not the cumulative cost of all inner model/tool turns from the run that just finished
- `transcript_summary` is optional backend-owned presentation metadata on
  `run_succeeded`. It contains total run elapsed time, tool-call counts,
  aggregate tool duration, optional token/context metrics copied from the
  terminal event, a `had_work_activity` boolean, a backend-owned
  `should_show_separator` recommendation, and zero or more typed activity
  group summaries.
- `budget`, `budget_before`, and `budget_after` are backend-owned
  `CompactionBudgetReport` objects. They are additive observability payloads
  for compaction decisions and must not require Go-side reinterpretation.
- `session_turn_context_status.status` is one of `missing`, `reused`, or
  `cleared`
- `session_turn_context_status.reason` is a backend-owned explanation for that
  status, such as `missing`, `no_active_turn_context`,
  `shell_family_mismatch`, `model_mismatch`, `thinking_mismatch`,
  `current_date_mismatch`, `timezone_mismatch`, or
  `runtime_context_mismatch`
- `session_turn_context_status.persisted_run_id` identifies the prior
  persisted run whose turn-context baseline was reused or cleared; it is null
  when no persisted baseline was available
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
  - `display_label`
  - `summary`
  - `duration_ms`
  - `details`
  - `group_kind`
- `title` is a terse backend-owned label for the tool action
- `display_label` is an optional backend-owned short verb for rendering, such as `Read`, `Search`, or `List`
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
- `transcript_summary.activity_groups` are derived from emitted backend-owned
  tool activity. Clients may render them as grouped transcript rows, but must
  not reclassify commands or tool names locally.
- `transcript_summary.activity_groups[].group_label` is deterministic
  backend-owned text such as `Shell`, `Edited files`, or `Read/Searched`.
- `Shell` grouping is intentionally generic. The backend must not infer
  specialized shell labels by parsing command strings; future command intent
  labels need explicit backend-owned metadata rather than a frontend or summary
  command taxonomy.
- `transcript_summary.should_show_separator` is a backend-owned hint for sparse
  end-of-run separators. It does not create a new event and does not require
  clients to invent local elapsed/token/context thresholds.
- started, updated, and failed/error-result activity should stay minimal: backend-owned `title`, optional `summary`, and `duration_ms` when applicable
- exploration-style rendering labels should come from backend `display_label`, not frontend tool-name maps
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
- `write`, `edit`, `shell`, `subagent`, and `ask_mcq_question` are sequential only
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
- When tool validation has already produced canonical validated args, public
  `tool_call_started.args` must come from those validated args rather than
  reparsing raw provider payload in the runtime layer

## Session Contract

Initial canonical session contract:

- append-only JSONL
- explicit session header with authoritative workspace metadata
- explicit run, native message-history, and event entries
- optional backend-owned project-doc disclosure at session start
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
  - fields: `type`, `version`, `workspace_root`, `shell_family`
- `session_info`
  - fields: `type`, `name`
  - `name` is the backend-normalized durable human session name
- `session_fork`
  - fields: `type`, `forked_from_session_id`, `forked_from_run_id`
  - records direct parent lineage for a forked session
  - `forked_from_run_id` is optional and, when present, identifies the latest
    completed parent run visible at fork time
- `session_project_docs`
  - fields: `type`, `documents`
  - `documents` is a list of backend-owned project-doc disclosures with:
    - `short_path`
    - `truncated`
  - records which workspace project-doc files were loaded when the session was created
- `session_run`
  - fields: `type`, `run_id`, `prompt`, `thinking`
  - `thinking` is optional and stores the effective thinking setting for that run
- `session_messages`
  - fields: `type`, `run_id`, `messages`
  - `messages` must be the native PydanticAI `ModelMessage` list for that run
  - `messages` must exclude internal instructions and `SystemPromptPart`
    content; those are ephemeral runtime state, not durable conversation state
- `session_turn_context`
  - fields: `type`, `run_id`, `model`, `thinking`, `effective_capabilities`, `workspace_root`, `shell_family`, `current_date`, `timezone`, `runtime_context_text`
  - records one persisted backend-owned runtime-framing snapshot for that completed run
  - `thinking`, `effective_capabilities`, `current_date`, and `timezone` are optional
  - `runtime_context_text` must be the dynamic runtime-framing payload for that run
  - when present, `runtime_context_text` includes the visible runtime-framing lines for current date, timezone, workspace root, shell family, model, thinking setting, and effective capability posture
  - static agent instructions are not persisted in `session_turn_context`
  - resumed runs reconstruct the last full model-visible runtime-context prefix from the latest active persisted snapshot when it is safe to do so
  - when visible runtime framing changed but the prior snapshot is still valid for reconstruction, resumed runs append one runtime-context update message before the new user prompt instead of replaying a second full prefix; this now covers model, thinking, effective capability posture, timezone, shell family, current date, and workspace-root changes
- `session_permission_grants`
  - fields: `type`, `run_id`, `grants`
  - records the latest backend-owned session-scoped grant snapshot for that completed run
  - `grants` is a tuple of `SandboxPermissionGrant` values with `scope="session"`
  - `session_permission_grants` persists durable operational permission memory such as approved filesystem roots and approved shell command prefixes
  - `session_permission_grants` is separate from `session_turn_context`; grants are operational session state, not model-visible runtime framing
- `session_event`
  - fields: `type`, `run_id`, `event`
  - `event` must be one canonical persisted run event payload
  - canonical session persistence must omit `assistant_text_delta`; deltas are
    live RPC transport for streaming UI updates, not durable session state
- `session_compaction`
  - fields: `type`, `compaction_id`, `compacted_through_run_id`, `replacement_messages`
  - `compacted_through_run_id` must reference an existing persisted `run_id`
  - `replacement_messages` is the canonical model-visible compacted prefix used
    for future resumed runs
  - `replacement_messages` must be non-empty
  - `replacement_messages` must end with exactly one compaction summary message

Ordering rules for the session slice:

- The first line must be exactly one `session_header`
- `session_fork` may appear at most once and, when present, must be the second
  line immediately after `session_header`
- `session_project_docs` may appear at most once before the first run and never inside or after a completed run
- `session_info` may appear only at completed-run boundaries, never in the middle of a run
- `session_info.name` is unique within the current workspace-backed session shard
- Each completed `session_run` is followed by one or more `session_event` lines for the same `run_id`, then exactly one trailing `session_messages` line for that run, and then optionally exactly one trailing `session_turn_context` line and optionally exactly one trailing `session_permission_grants` line for that same run
- A trailing run without `session_messages` is an incomplete run and authoritative session load must fail hard
- `session_turn_context` may appear only immediately after the completed run's trailing `session_messages`
- `session_turn_context` is optional so older sessions remain loadable, but a run may not have more than one
- `session_turn_context.workspace_root` must match the authoritative session workspace root exactly
- `session_permission_grants` may appear only at a completed run boundary, immediately after `session_messages` or immediately after that run's optional `session_turn_context`
- `session_permission_grants` is optional, but a run may not have more than one
- `session_compaction` may appear only at a completed run boundary, never in the middle of a run
- `session_compaction` entries are append-only and must not move the compaction boundary backward
- Authoritative session loads must provide the expected workspace root and it must match the persisted `session_header.workspace_root` exactly
- Session resume semantics must reconstruct effective conversation context from the latest compaction `replacement_messages` plus later `session_messages` strictly after `compacted_through_run_id`
- Session resume semantics must treat `session_turn_context` as separate runtime framing state rather than as conversation memory
- Session resume semantics must treat `session_permission_grants` as separate durable operational permission state rather than as conversation memory or runtime framing
- The latest active persisted `session_turn_context` baseline is invalidated by a later `session_compaction` entry
- Before a session-backed run starts, the runtime must explicitly classify the
  active persisted `session_turn_context` baseline as missing, reused, or
  cleared against the current run framing inputs
- The current framing inputs that can clear a persisted baseline include the
  effective model, effective thinking setting, shell family, current date, and
  runtime-context payload
- The actual model-visible next-run substrate starts with either a full
  runtime-context prefix or a reconstructed prior prefix, then durable resumed
  conversation history, then optionally one runtime-context update message,
  then the new user prompt
- Forked sessions do not inherit parent `session_turn_context` entries; a fork starts without an active persisted runtime-framing baseline
- Durable cross-run compaction must be materialized into resume `message_history` before the next run starts; JACA does not use PydanticAI `history_processors` in the canonical runtime path
- Durable compaction summary generation must use a plain-text model call that
  feeds one persisted summary message into `replacement_messages`; the runtime
  does not rebuild hidden instructions from compaction state
- Run-local history processors may compact current-run tool-return content for
  the model when context pressure grows, but the persistence layer must restore
  the original raw tool-return content before writing `session_messages`
- `session.compact` and automatic compaction must generate summaries through a model call; the persistence layer must not invent placeholder summaries locally
- When a new run omits `thinking`, the session-backed runtime inherits the most recent persisted non-null thinking setting from that session
- Session-backed runtime streaming must append `session_run` and `session_event` entries incrementally as the run streams and append `session_messages` only after terminal completion
- Session-backed runtime streaming must not persist `assistant_text_delta`
  events into canonical session JSONL; only durable non-delta run events are
  appended as `session_event` entries
- If cancellation unwinds through `stream_session_run_events()`, the runtime must persist terminal `tool_call_failed` events for any still-pending tool calls and then persist terminal `run_failed` before finalization
- Persisted `session_messages` for a terminal run must remain replay-safe; they must not contain unresolved tool calls
- Persisted `session_messages` for a terminal run must not persist internal
  instructions or `SystemPromptPart` content
- Persisted `session_messages` for a failed terminal run must also exclude unresolved failed-correction tails such as trailing `RetryPromptPart` repair prompts and the matching invalid `ToolCallPart` suffix that caused a provider-side abort; future resumed runs must continue only from the last known-good message boundary
- Trimming poisoned failed-correction tails from persisted `session_messages` must not delete observability data: the original streamed `session_event` sequence and provider traces remain authoritative for failure forensics
- If cancellation interrupts message capture mid-tool-call, finalization must strip those unresolved tool parts before writing `session_messages`
- Persisted events for a run must satisfy the streamed run contract, including exactly one terminal outcome
- Persisted `session_event` payloads must preserve any tool `activity` metadata unchanged
- Appending a new run must preserve all existing lines and write the header only once
- Fresh runs inject one full runtime-context prefix
- Resumed runs inject one full runtime-context prefix when there is no safe prior baseline to reconstruct
- Resumed runs may instead reconstruct the last full runtime-context prefix and append one runtime-context update message when the visible runtime framing changed in a diffable way
- Successful resumed runs must persist only new run deltas instead of replaying replacement history back into trailing `session_messages`
- Before a resumed run starts, the runtime may append one automatic `session_compaction` entry when estimated local next-run message history, including any reconstructed runtime-context prefix and runtime-context update message, plus reserve crosses the configured fraction of the effective active model context window after compaction-output headroom is reserved
- Before a resumed run starts, the automatic trigger estimates the actual local
  resume history the next run will use; it does not depend on prior
  provider-reported usage
- Automatic durable compaction may preserve a bounded recent user-message tail
  inside `replacement_messages`; future automatic trigger decisions count only
  completed runs beyond `compacted_through_run_id` as new work
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
- Typed permission and approval carrier models may land in the Python-owned RPC
  contract before the first executable handler slice is wired; executable RPC
  behavior is defined only by the commands explicitly listed below.

Initial executable RPC slice:

- request line
  - fields: `id`, `command`, `payload`
  - initial commands:
    - `auth.status` with payload `{}`
    - `auth.set` with payload `{"provider": <provider-name>, "secret": <string>, "storage": "file"}`
    - `auth.clear` with payload `{"provider": <provider-name>}`
    - `workspace.trust_status` with payload `{}`
    - `workspace.trust_accept` with payload `{}`
    - `session.create` with payload `{}`
    - `session.name` with payload `{"session_id": <opaque-lowercase-hex-string>, "name": <string>}`
    - `session.preview` with payload `{"session_id": <opaque-lowercase-hex-string>}`
    - `session.compact` with payload `{"session_id": <opaque-lowercase-hex-string>}`
    - `onboarding.start` with payload `{"session_id": <optional-opaque-lowercase-hex-string>}`
    - `onboarding.submit` with payload `{"session_id": <opaque-lowercase-hex-string>, "attempt_id": <opaque-lowercase-hex-string>, "selected_index": 0 | 1 | 2 | 3}`
    - `run.start` with payload `{"session_id": <opaque-lowercase-hex-string>, "prompt": <string>, "thinking": <optional-thinking-setting>, "enable_code_mode": <optional-bool>}`
    - `run.start` with payload `{"session_id": <opaque-lowercase-hex-string>, "prompt": <string>, "mode": "coding" | "onboarding", "thinking": <optional-bool-or-level>, "enable_code_mode": <optional-bool>}`
    - `run.enqueue` with payload `{"session_id": <opaque-lowercase-hex-string>, "prompt": <string>, "mode": "next" | "later"}`
    - `run.interrupt` with payload `{"session_id": <opaque-lowercase-hex-string>, "promote_queued_steer": <bool>}`
- `rpc_response`
  - fields: `type`, `id`, `response`
  - initial response payloads:
    - `{"providers": [{"provider": <provider-name>, "configured": <bool>, "secret_configured": <bool>, "requires_secret": <bool>, "source": "env" | "file" | "none", "env_key": <provider-env-var>, "reason": "ok" | "missing_secret" | "local_endpoint_no_secret_required"}, ...], "local_secret_store": {"available": <bool>, "message": <optional-string>, "file_store_path": <abs-path>}}`
    - `{"status": {"provider": <provider-name>, "configured": <bool>, "secret_configured": <bool>, "requires_secret": <bool>, "source": "env" | "file" | "none", "env_key": <provider-env-var>, "reason": "ok" | "missing_secret" | "local_endpoint_no_secret_required"}}` for `auth.set`
    - `{"status": {"provider": <provider-name>, "configured": <bool>, "secret_configured": <bool>, "requires_secret": <bool>, "source": "env" | "file" | "none", "env_key": <provider-env-var>, "reason": "ok" | "missing_secret" | "local_endpoint_no_secret_required"}}` for `auth.clear`
    - `{"trusted": <bool>, "trust_target": <abs-path>}` for `workspace.trust_status`
    - `{"trusted": true, "trust_target": <abs-path>}` for `workspace.trust_accept`
    - `{"session_id": <opaque-lowercase-hex-string>}`
    - `{"session_id": <opaque-lowercase-hex-string>, "name": <backend-normalized-session-name>}` for `session.name`
    - `{"session_id": <opaque-lowercase-hex-string>, "entries": [{"kind": "instructions" | "user" | "activity" | "assistant" | "error", "text": <string>}], "truncated": <bool>}` for `session.preview`
    - `{"compaction_id": <opaque-lowercase-hex-string>, "compacted_through_run_id": <run_id>}`
    - `{"session_id": <opaque-lowercase-hex-string>, "created_session": <bool>, "project_docs": [{"path": <path>, "filename": <filename>, "truncated": <bool>}], "attempt_id": <opaque-lowercase-hex-string>, "question_type": "mcq", "snippet_path": <path>, "snippet_start_line": <positive-int>, "snippet_end_line": <positive-int>, "snippet_text": <string>, "prompt": <string>, "options": [<string>, <string>, <string>, <string>], "explanation": <string>, "generator_version": <string>}` for `onboarding.start`
    - `{"session_id": <opaque-lowercase-hex-string>, "attempt_id": <opaque-lowercase-hex-string>, "question_type": "mcq", "selected_index": 0 | 1 | 2 | 3, "correct_index": 0 | 1 | 2 | 3, "correct_option": <string>, "is_correct": <bool>, "explanation": <string>}` for `onboarding.submit`
    - `{"session_id": <opaque-lowercase-hex-string>}` for `run.start`
    - `{"session_id": <opaque-lowercase-hex-string>, "queued_count": <positive-int>}` for `run.enqueue`
    - `{"session_id": <opaque-lowercase-hex-string>, "promoted_count": <non-negative-int>}` for `run.interrupt`
- `rpc_event`
  - fields: `type`, `id`, `event`
  - `event` must be one canonical streamed run event payload or session lifecycle event payload
  - current onboarding-specific run event:
    - `{"type": "onboarding_question_requested", "run_id": <run_id>, "attempt_id": <opaque-lowercase-hex-string>, "question_type": "mcq", "prompt": <string>, "options": [<string>, <string>, <string>, <string>]}`
- `rpc_error`
  - fields: `type`, `id`, `error_type`, `message`

Ordering rules for the RPC slice:

- A valid `auth.status` request yields exactly one `rpc_response` with one
  backend-authored status object per shipped provider plus one backend-authored
  `local_secret_store` object describing where the backend-owned auth file
  lives
- `configured` means the provider is ready to run for its current effective
  path, not merely that some secret exists
- `secret_configured` means a secret was found through environment or the
  explicit local auth file
- `requires_secret` is derived from the effective provider path and endpoint
  configuration
- A valid `auth.set` request yields exactly one `rpc_response` and stores the
  secret in the backend-owned local auth file without echoing the secret back
- A valid `auth.clear` request yields exactly one `rpc_response` and removes
  the stored local secret for that provider from the explicit local auth file
- A valid `workspace.trust_status` request yields exactly one `rpc_response`
  describing whether the repo-root trust target is currently trusted
- A valid `workspace.trust_accept` request yields exactly one `rpc_response`,
  persists trust for the repo-root trust target, and unblocks repo instruction
  loading plus `session.create`
- A valid `session.create` request yields exactly one `rpc_response` containing a server-generated opaque `session_id`
- `session.create` must fail hard with `WorkspaceUntrusted` until trust is
  accepted for the current trust target
- `session.create` may also append one backend-owned `session_project_docs`
  entry when workspace project docs were loaded for that new session
- A valid `onboarding.start` request yields exactly one `rpc_response`
  containing one backend-owned pending onboarding question for that session
- `onboarding.start` with no `session_id` must create a new trusted session,
  persist the onboarding attempt before responding, and return
  `created_session: true`
- `onboarding.start` on a session with an existing pending snippet-backed
  `onboarding.start` attempt must reopen that same attempt instead of
  generating a second pending question
- `onboarding.start` must fail with `InvalidRequest` if the session already has
  a pending live `ask_mcq_question` attempt, because that tool-owned
  question is owned by the normal run surface rather than the legacy
  `onboarding.start` snippet-backed response contract
- A valid `onboarding.submit` request yields exactly one `rpc_response` and
  resolves correctness from the persisted pending attempt rather than from a
  second model-generation step
- `onboarding.submit` also resolves any live `ask_mcq_question` tool
  request blocked inside an active run for the same session and attempt id
- `workspace.project_docs` must fail hard with `WorkspaceUntrusted` until trust
  is accepted for the current trust target
- A valid `session.name` request must reference an existing `session_id`, append one backend-normalized `session_info` entry when the requested name changes, enforce workspace-local name uniqueness, and yield exactly one `rpc_response` containing that normalized session name
- A valid `session.preview` request must reference an existing `session_id` and yields exactly one `rpc_response` containing a bounded recent-history preview derived from durable session runs plus any persisted `session_project_docs` disclosure; it is a presentation helper and does not change resume authority
- Session preview may include `activity` entries derived from persisted
  `run_succeeded.transcript_summary.activity_groups`. These rows are bounded
  summaries only; preview must not dump raw tool output.
- Session preview should omit generic `Shell` groups because they are not
  meaningful resumed-history landmarks. More specific shell intent may appear
  in preview only when the backend exposes explicit intent metadata instead of
  deriving it from command strings.
- A valid `session.compact` request must reference an existing `session_id` and yields exactly one `rpc_response` describing the newly appended compaction entry
- If model-driven compaction summary generation fails, `session.compact` fails hard; it does not append a placeholder summary
- A valid `run.start` request must reference an existing `session_id`, may
  optionally select a backend-owned run `mode`, yields zero or more
  `rpc_event` lines whose embedded events satisfy the streamed run contract,
  and ends with exactly one final `rpc_response` after the active run and any
  drained follow-up runs complete
- `run.start` with `mode: "coding"` exposes only the canonical coding tool
  set
- `run.start` with `mode: "onboarding"` exposes the canonical coding tools
  plus onboarding-only tools such as `ask_mcq_question`,
  `generate_mcq_from_teaching_packets`, and `publish_teaching_packet`, and
  applies the onboarding prompt overlay in Python
- `run.start` with `enable_code_mode: true` appends the Code Mode `exec` and
  `wait` tools to that run's model-facing tool list only; it does not change
  the session's persisted mode and does not enable Code Mode for later runs
- `run.start` with `code_mode_tools_only: true` exposes only the Code Mode
  `exec` and `wait` tools for that run; it is an experimental benchmarking
  control surface and does not change the session's persisted mode
- `/onboard` is the user-facing signal that sets the session mode to
  `onboarding` before starting the run; `/exit-mode` returns the session mode
  to `coding`
- A valid `run.enqueue` request must reference an existing `session_id`, must carry a non-blank prompt, is accepted only while that session currently has an active streamed run in this backend process, and yields exactly one `rpc_response` with the resulting queued-count
- A valid `run.interrupt` request must reference an existing `session_id`, is accepted only while that session currently has an active streamed run in this backend process, cancels that active run, and yields exactly one `rpc_response` with the resulting promoted-count
- Session lifecycle `rpc_event` payloads such as `session_compaction_started` and `session_compaction_completed` may appear before `run_started`
- `session_queue_state` is a backend-owned session lifecycle event that carries the authoritative active-run queue snapshot with:
  - `next_prompts`
  - `later_prompts`
- Clients must render queue preview from `session_queue_state`; they must not infer queue transitions from `run_started`, `run_failed`, or local enqueue bookkeeping
- `session_queued_prompt_batch_submitted` is a backend-owned session lifecycle event that carries the queued user text that was actually submitted with:
  - `mode`
  - `prompts`
- Clients should render queued user text from `session_queued_prompt_batch_submitted` so assistant answers do not appear without the queued prompt that triggered them
- `CompactionBudgetReport` fields are:
  - `should_compact`
  - `reason`
  - `context_window_tokens`
  - `effective_context_window_tokens`
  - `output_headroom_tokens`
  - `trigger_budget_tokens`
  - `prompt_reserve_tokens`
  - `estimation_method`
  - `estimated_resume_message_tokens`
  - `estimated_replacement_messages_tokens`
  - `estimated_replacement_summary_tokens`
  - `estimated_pre_run_tokens`
  - `estimated_post_compaction_headroom_tokens`
  - `runs_since_latest_compaction`
- `run.start` on an existing session is the canonical continue operation; there is no separate `session.continue` command
- `run.enqueue` is the canonical active-run queueing operation
- `run.enqueue` with `mode: "later"` is the canonical end-of-turn follow-up queueing operation; after the active streamed run for that session ends, the backend immediately drains queued follow-ups as additional runs on the same `run.start` stream until the queue is empty
- `run.enqueue` with `mode: "next"` is the canonical active-turn steer queueing operation; the backend may attach queued steer prompts only after the current tool phase completes, before the next model round-trip in the same run
- If a `mode: "next"` prompt is still pending when the active run ends, the backend downgrades it into the `later` queue before draining follow-ups
- `run.interrupt` with `promote_queued_steer: true` is the canonical promotion path from pending `next` steering into immediate follow-up delivery; any pending steer prompts are prepended to the `later` queue before the cancelled run drains queued follow-ups on the same `run.start` stream
- Queue ordering is explicit:
  - promoted or downgraded `next` prompts run before already-queued `later` prompts
  - FIFO order is preserved within the promoted/downgraded `next` bucket
  - FIFO order is preserved within the pre-existing `later` bucket
- Queue draining is bucket-batched:
  - multiple prompts in the same promoted/downgraded `next` bucket are combined into one follow-up prompt in FIFO order using blank-line separation
  - multiple prompts in the same `later` bucket are combined into one follow-up prompt in FIFO order using blank-line separation
  - promoted/downgraded `next` prompts do not merge with pre-existing `later` prompts
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
