# Workspace Work Graph

read_when: you are designing or implementing durable workspace-level work state beyond sessions

## Why This Exists

Sessions are the append-only record of what happened. They are good at run history,
resume, fork lineage, and durable conversation continuity. They are not a good home
for mutable project work state such as active objectives, decomposed tasks, durable
notes, or archived follow-ups that may span multiple sessions.

This doc defines a separate subsystem for that state: a workspace-native work graph.

The work graph is inspired by how this repo has actually used Linear:

- umbrella issue plus child slices
- durable rationale in the body
- human-readable handles
- status over time
- verification breadcrumbs

It is not intended to recreate full project-management software.

## Why This Should Be Distinctive

JACA should not build a weaker clone of Linear.

The work graph should be distinguishable because it is designed for coding-agent
continuity, not general project management.

That means it should eventually be better than a generic issue tracker at
questions like:

- what was the actual coding objective
- what was tried already
- what failed
- what remains unresolved
- which sessions contributed to this work
- what evidence verified progress

The differentiator is not boards, labels, or assignees. The differentiator is a
workspace-native work ledger that is grounded in agent execution and repo work.

## Product Position

The work graph is:

- workspace-scoped
- backend-owned
- agent-optimized
- user-inspectable
- lightly user-controllable

The work graph is not:

- a replacement for sessions
- hidden agent memory
- a generic issue tracker clone
- a second source of architectural truth

The core principle is simple:

**The work graph is agent-optimized but never hidden.**

If it changes agent behavior, the user must be able to inspect it.

## Boundary With Sessions

Sessions and the work graph serve different purposes.

### Session

Session answers:

- what happened
- what the model saw
- how the next run resumes
- what forked from what

Session remains append-only JSONL and the canonical source of run history.

### Work Graph

Work graph answers:

- what we are trying to do
- why it matters
- how the work is decomposed
- what is blocked
- what remains unresolved
- which session created it
- which session produced important updates

The work graph is durable workspace state, not transcript state.

These systems should be linked, not merged.

The linkage is part of the product differentiation. The work graph should
eventually connect work items to real coding evidence such as session
provenance, verification breadcrumbs, and touched files. That is more useful
for a coding agent than a generic tracker that only stores prose.

## Boundary With Docs

Docs remain the canonical place for architecture and product truth.

The work graph may reference docs, but it must not silently become the place where
architecture decisions live forever without being written back to the repo.

Use the work graph for active work management. Use docs for canonical design.

## Design Principles

1. Workspace-local first. The default mental model is "resume work in this repo",
   not "search all work across every repo".
2. Explicit state beats inferred memory. The system should store deliberate work
   items, notes, links, and status changes rather than opaque background summaries.
3. No hidden prompt injection. The existence of work-graph state must be
   inspectable, and any future prompt use must be intentional and bounded.
4. Sessions stay authoritative for history. The work graph must not become a
   second resume engine.
5. Keep v1 small. Only ship the pieces that materially improve agent continuity.
6. Prefer durable handles. Human-readable slugs are better than opaque ids for
   everyday use.
7. Optimize for continuation quality. The system should help the next session
   pick up real work cleanly, not just preserve task titles.
8. Be distinguishable on coding value. Prefer links to agent evidence and repo
   state over generic project-management surface area.

## What The Work Graph Needs To Do

For JACA, the work graph needs to support:

- one durable workspace-local record of active work
- human-readable work item handles
- parent/child decomposition
- status tracking
- durable rationale and notes
- session provenance on work items and updates
- archiving without losing history

That is enough to support the agent offloading cognitive burden without hiding it.

Over time, this subsystem should become the place where coding continuity is
made explicit and inspectable across sessions, not just the place where titles
and statuses are stored.

## Proposed Core Model

V1 should stay small and explicit.

There should be no separate `projects` table in v1.

The workspace is already the outer project boundary. Inside a workspace, a
top-level work item can represent a major workstream such as `work-graph`,
`session-continuity`, or `bloat-and-rot`.

To support that without growing a second hierarchy system, `work_items` should
carry a small `kind` field in v1.

### Work Item

A work item is the main unit.

Recommended fields:

- `id`
- `kind`
- `slug`
- `title`
- `status`
- `parent_id`
- `body_md`
- `created_session_id`
- `created_at`
- `updated_at`
- `archived_at`

Recommended `kind` values in v1:

- `project`
- `task`

Recommended `status` values in v1:

- `todo`
- `in_progress`
- `blocked`
- `done`
- `archived`

`body_md` is the canonical current description of the work item. It is mutable.
The durable history of how the item changed belongs in `work_updates`.
`updated_at` should advance whenever the current visible state of the item
changes, including appended updates.
`created_session_id` records which session originally created the item when
known.

### Work Update

A work item should have an append-only update log for important changes.

One work item may have many updates.

Recommended update kinds:

- note
- decision
- verification
- status_change
- completion

Recommended fields:

- `id`
- `work_item_id`
- `kind`
- `body_md`
- `session_id` nullable
- `created_at`

This is the durable narrative of the item. It replaces the need for a separate
comments subsystem in v1.

## Storage Direction

The likely v1 storage shape is one SQLite database per workspace.

Recommended path:

`~/.jaca/workspaces/<workspace-key>/work.sqlite`

Why SQLite:

- work items are mutable
- parent/child queries matter
- archive and recent queries matter
- unique slug enforcement matters
- session linkage matters

JSONL is the right shape for append-only session history. It is not the right
shape for mutable workspace work state.

## V1 Storage Rules

The v1 storage contract should be strict and small.

- ids are opaque text ids, not integer sequences
- `slug` must be unique within a workspace database
- `parent_id` is nullable
- top-level `project` items group child tasks
- `created_session_id` is optional and records the session that created the
  work item when known
- `archived_at` records archive time rather than deleting data
- `updated_at` advances on any meaningful mutation to the item, including:
  - body changes
  - status changes
  - archive transitions
  - appended `work_updates`
- `work_updates` are append-only
- `work_updates.session_id` is optional and records which session produced a
  specific update when known
- SQL constraints should enforce the allowed `kind` and `status` values
- backend validation should enforce the same rules before writes
- `created_session_id` should stay nullable text and should not depend on a
  foreign key into session JSONL storage
- `work_updates.session_id` should stay nullable text and should not depend on a
  foreign key into session JSONL storage

If an item is marked `done`, the preferred path is:

- update `work_items.status`
- append a `completion` update explaining how it was finished
- optionally attach the producing session through `session_id`

For v1, `archived` should remain a status value rather than becoming a separate
orthogonal state machine. That keeps the first implementation smaller and the
queries simpler.

## Commands And Surfaces

The first implementation should be backend-first.

The initial human command surface is CLI:

- `jaca work new <title>`
- `jaca work list`
- `jaca work show <slug>`
- `jaca work note <slug> <text>`
- `jaca work status <slug> <status>`

These commands should remain thin wrappers over backend-owned Python
operations. They are for users and operators, not the canonical semantic
interface for the agent itself.

The initial agent-facing surface is a small backend-owned toolset:

- `work_list`
- `work_read`
- `work_create`
- `work_update`
- `work_status`

These tools are also thin wrappers over backend-owned Python operations. They
exist so the agent can use durable work state directly without shelling out to
CLI commands and parsing text.

This should not begin as a large TUI surface. The important part is durable
state, bounded agent behavior, and clear backend ownership.

## What The Agent May Do

The agent may:

- read work items freely
- autonomously create child `task` items when there is an explicit `project` or
  active work context
- append durable notes and verification breadcrumbs
- check existing work items before creating a new task to avoid obvious
  duplicates
- update status as part of explicit work management flows

The agent should not, by default:

- silently create new top-level `project` items
- silently close or archive work
- treat the work graph as hidden memory
- dump the entire work graph into prompts

## Policy Layer Direction

The work graph should keep a strict split between durable state and learned or
heuristic policy.

The durable state remains backend-owned:

- SQLite-backed `work_items` and `work_updates`
- strict status, kind, and slug validation
- explicit `work_*` CLI commands and agent tools

Any future LM-assisted judgment should sit above that deterministic substrate.

That means the repo may add bounded policy modules for questions like:

- should this Harbor result create a new child task or update an existing item
- should this result append a `verification` update, a `decision`, or a plain
  `note`
- should a task move to `blocked`, remain `in_progress`, or become `done`
- which open task is the best next candidate for execution

Those policy modules may use DSPy or similar tooling, but they must never
replace the canonical work-graph store or mutate it implicitly.

## RPC Plugin Direction

If the repo later adds a DSPy-backed work-policy engine, the cleanest boundary
is an RPC-facing plugin-style module rather than a rewrite of the canonical
backend.

That means:

- the canonical backend still owns work-graph storage and validation
- the canonical backend still owns tool semantics, sessions, and public
  contracts
- a bounded policy module may read inspectable work-graph state and return
  structured proposals
- normal backend operations remain the only path that mutates durable work
  state

The intended role is "policy advisor", not "shadow backend".

Good candidates for that plugin-style policy surface:

- propose child-task decomposition for one active `project`
- propose the next best open task to execute
- interpret a Harbor result into one explicit work action
- propose whether a task should stay `in_progress`, become `blocked`, or move
  to `done`

Bad candidates:

- direct database writes from the policy module
- hidden prompt stuffing from unbounded work-graph state
- a second source of truth for work-item lifecycle semantics
- a second execution engine that bypasses backend validation

The preferred contract shape is:

- request bounded work-graph state plus explicit evidence such as one Harbor
  result or one recent session excerpt
- response with a typed action proposal
- require the canonical backend to validate and apply any accepted action

For example, a future RPC/plugin command family could look like:

- `work.policy.decompose`
- `work.policy.select_next`
- `work.policy.resolve`

Those commands should return structured proposals, not hidden side effects.

## DSPy Fit

DSPy is a plausible fit for bounded work-graph policy modules because it is
good at structured LM programs with explicit input/output schemas.

The strongest near-term DSPy uses here are:

- decomposition proposals from a top-level `project` into candidate child
  `task` items
- duplicate detection against existing active work items before creation
- update drafting that turns session or Harbor evidence into one explicit
  `work_update`
- next-task selection over a bounded set of open work items

Important boundary:

- DSPy should propose structured actions
- backend-owned Python operations should validate and execute those actions
- the work graph must remain inspectable and explicitly mutated through normal
  backend seams

## GEPA Fit

GEPA is a later-stage fit, not a v1 storage dependency.

If the repo adopts DSPy-based policy modules, GEPA could optimize the natural
language policy used by those modules against explicit metrics. The strongest
target is not generic work-item prose quality; it is work-management quality on
real coding tasks.

The best candidate policy slice is:

- work item creation policy
- execution prioritization policy
- resolution and status policy
- Harbor-result interpretation policy

In practical terms, that means optimizing rules like:

- when to create a new child task instead of appending evidence to an existing
  task
- when Harbor output is strong enough to count as `verification`
- when failure means `blocked` rather than "keep trying"
- when a task is actually `done` versus merely promising progress

## Harbor-Driven Evaluation Direction

Harbor tasks are a strong future evaluation source for work-graph policy
because they produce grounded outcomes, concrete artifacts, and repeatable
follow-up decisions.

This suggests a future evaluator that scores whether a bounded work-policy
module:

- avoids duplicate task creation
- selects useful next tasks
- records meaningful verification breadcrumbs
- marks `done` only when explicit evidence exists
- distinguishes unresolved blockers from ordinary intermediate failures
- improves downstream Harbor task completion rather than only producing nicer
  prose

The key design rule is:

- Harbor and similar task harnesses may evaluate work-graph policy
- they must not become the storage system or a hidden side channel for work
  state

## Recommended Sequence

The sequence should remain strict:

1. Ship deterministic v1 work-graph storage and backend operations.
2. Add one bounded structured policy module above that substrate.
3. Build evaluation data from real work-item histories and Harbor task
   outcomes.
4. Only then consider GEPA to optimize the policy text for those modules.

Do not:

- make DSPy or GEPA the source of truth for work-item state
- let GEPA write directly to the work-graph database
- let a policy plugin bypass canonical backend validation or storage rules
- optimize work-graph prompts before a real evaluator exists
- use the whole work graph as unbounded prompt context

## Out Of Scope For V1

Do not build these in the first slice:

- a separate `projects` table
- labels
- assignees
- cycles
- milestones
- comments as a separate subsystem
- kanban or board views
- global cross-workspace search
- automatic sync with external systems
- broad automatic extraction from every conversation

This is not a local clone of Linear. It is a focused workspace work ledger for
agent continuity.

## Long-Term Differentiators

If this subsystem proves useful, the strongest future differentiators are:

- explicit session provenance on work items and work updates
- durable verification breadcrumbs on work items
- workspace-native handles and decomposition designed around code work
- bounded prompt use that improves continuation quality without becoming hidden
  memory

Those are more aligned with a coding agent than general PM features like boards,
labels, or assignees.

## Relationship To Future Memory Work

If JACA later adds richer working memory, the work graph should remain explicit
and inspectable. It is safer than hidden memory because it is:

- structured
- durable
- grounded in explicit session provenance
- auditable by the user

That makes it a better place for durable active-work state than transcript
compaction or invisible background notes.

## First Slice

The first implementation slice should be:

1. workspace-local SQLite store
2. minimal schema for work items and updates with session provenance
3. backend-owned create/list/read/update operations
4. exact slug uniqueness within a workspace
5. no automatic prompt injection
6. no large new TUI surface

Only after that should we decide whether a richer agent workflow or session
integration is justified.
