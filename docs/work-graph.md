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
- which sessions contributed to it

The work graph is durable workspace state, not transcript state.

These systems should be linked, not merged.

The linkage is part of the product differentiation. The work graph should
eventually connect work items to real coding evidence such as sessions,
verification breadcrumbs, and touched files. That is more useful for a coding
agent than a generic tracker that only stores prose.

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
- explicit links from work items to sessions
- archiving without losing history

That is enough to support the agent offloading cognitive burden without hiding it.

Over time, this subsystem should become the place where coding continuity is
made explicit and inspectable across sessions, not just the place where titles
and statuses are stored.

## Proposed Core Model

V1 should stay small and explicit.

### Work Item

A work item is the main unit.

Recommended fields:

- `id`
- `slug`
- `title`
- `status`
- `parent_id`
- `body_md`
- `created_at`
- `updated_at`
- `archived_at`

### Work Update

A work item should have an append-only update log for important changes.

Recommended update kinds:

- note
- decision
- verification
- status_change
- completion

### Work Session Link

Sessions should be linkable to work items so the agent and user can see which
threads contributed to which work.

This is a link, not a merge of storage systems.

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

## Commands And Surfaces

The first implementation should be backend-first.

Candidate command family:

- `/work new <title>`
- `/work list`
- `/work show <slug>`
- `/work use <slug>`
- `/work note <slug> <text>`
- `/work done <slug>`

This should not begin as a large TUI surface. The important part is durable
state and clear backend ownership.

## What The Agent May Do

The agent may:

- create or update work items when the user explicitly asks for it
- append durable notes and verification breadcrumbs
- link sessions to work items
- update status as part of explicit work management flows

The agent should not, by default:

- silently invent long-lived work items
- silently close or archive work
- treat the work graph as hidden memory
- dump the entire work graph into prompts

## Out Of Scope For V1

Do not build these in the first slice:

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

- explicit links from work items to sessions that contributed to the work
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
- linked to sessions
- auditable by the user

That makes it a better place for durable active-work state than transcript
compaction or invisible background notes.

## First Slice

The first implementation slice should be:

1. workspace-local SQLite store
2. minimal schema for work items, updates, and session links
3. backend-owned create/list/read/update operations
4. exact slug uniqueness within a workspace
5. no automatic prompt injection
6. no large new TUI surface

Only after that should we decide whether a richer agent workflow or session
integration is justified.
