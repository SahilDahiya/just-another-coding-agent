# Execution And Trust Boundaries

read_when: you want to understand where JACA actually runs commands, what the trust and workspace boundaries are, and how the current seams would evolve toward stronger sandbox backends

## What This Component Owns

The execution plane owns:

- where code actually runs
- which process is launched
- how stdout and lifecycle are streamed back
- what filesystem and network restrictions are enforced by the runtime backend

The trust-boundary layer owns:

- what workspace or repo root is trusted
- what directory tree is in or out of bounds
- what blast radius the agent is allowed to touch

These are related but not identical.

## Why It Matters

This is where many systems get sloppy.

They blur together:

- trust
- permission policy
- process launch
- workspace boundaries

JACA is trying to keep those as separate concepts.

That separation is exactly what you want for future migration from host execution to gVisor or Firecracker.

## Core Files

Read these first:

- [../../src/just_another_coding_agent/tools/deps.py](../../src/just_another_coding_agent/tools/deps.py)
- [../../src/just_another_coding_agent/tools/sandbox_executor.py](../../src/just_another_coding_agent/tools/sandbox_executor.py)
- [../../src/just_another_coding_agent/tools/read_only_worker/runtime.py](../../src/just_another_coding_agent/tools/read_only_worker/runtime.py)
- [../workspace-trust.md](../workspace-trust.md)

## The Main Seam: `sandbox_executor`

`WorkspaceDeps` carries a `sandbox_executor`.

That is the critical execution seam.

Why it matters:

- tool code does not need to know the concrete backend
- executor choice can evolve without rewriting the entire tool layer

Today the default is `HostSandboxExecutor`.

That means the current product contract already assumes the executor is replaceable, even though the shipped implementation is still host-based.

## What `HostSandboxExecutor` Actually Does

Read [../../src/just_another_coding_agent/tools/sandbox_executor.py](../../src/just_another_coding_agent/tools/sandbox_executor.py).

Today it:

- builds a shell command request
- spawns a subprocess in the workspace root
- pipes stdout and stderr together
- exposes a command handle for read, wait, and terminate

This is intentionally narrow.

That is good design because it keeps the executor contract small:

- execute
- read output
- wait
- terminate

The narrower this seam is, the easier backend swapping becomes.

## Why The Execution And Policy Planes Must Stay Separate

If you let backend details leak into policy logic:

- every permission change becomes a backend change
- API shape becomes backend-specific
- migrations become painful

If you keep them separate:

- policy can stay stable
- execution backend can change underneath

That is the clean path from:

- host execution
- to gVisor-backed execution
- to Firecracker-backed execution

without rewriting the product contract.

## Workspace Boundary

The workspace boundary is one of the most important practical safety concepts in the repo.

You can see it in `WorkspaceDeps` through:

- `workspace_root`
- `session_scope`
- `read_only_worker`

And in docs through:

- [../workspace-trust.md](../workspace-trust.md)

The key idea is:

most agent-safety questions become "what is the allowed blast radius?"

That is why workspace scoping matters so much.

## Trust Is Not The Same As Sandbox Policy

This distinction matters.

From [../workspace-trust.md](../workspace-trust.md):

- workspace trust is a startup safety gate
- it is separate from permission modeling
- it is separate from shell sandboxing

That means:

- trust answers whether repo-owned instructions should be loaded
- policy answers what capabilities are allowed
- execution answers where the code actually runs

Three different jobs.

## Read-Only Worker As A Boundary Tool

`ReadOnlyWorkerRuntime` is another good seam to understand.

It exists so read-only operations can go through a stable worker protocol instead of every tool reinventing raw file crawling.

Why this matters:

- workspace-scoped behavior can be enforced consistently
- protocol and runtime stay explicit
- read-only capabilities stay separable from general shell execution

This is a good example of not forcing the shell tool to own every interaction with the filesystem.

## Invariants

1. The execution seam should stay backend-neutral.
2. The workspace root should be explicit for every run.
3. Trust state should not silently imply sandbox capability.
4. Execution termination should be explicit and controllable.
5. Read-only operations should have a stable bounded path that is narrower than arbitrary shell execution.

## What Is Replaceable

Replaceable:

- `HostSandboxExecutor`
- future gVisor adapter
- future Firecracker adapter
- read-only worker implementation details

Not replaceable without changing the architecture:

- existence of an execution seam
- explicit workspace boundary
- separation between trust and sandbox policy

## Tradeoffs

### Good Tradeoff

JACA chooses an explicit executor seam before it has a strong sandbox backend.

That is the right architectural move.

Why:

- you can harden the backend later
- without having to change the whole control plane

### Current Limitation

The shipped executor is still host-based.

That means the architecture is ahead of the enforcement.

That is acceptable if you understand it honestly and do not make claims the implementation cannot support yet.

## Interview Explanation

Good answer:

> I would keep execution behind a narrow backend-neutral executor contract. The policy plane decides allowed posture, while the execution plane maps that posture onto a concrete backend like host execution, gVisor, or Firecracker. I would also keep workspace trust and workspace blast radius explicit, because most practical agent safety questions reduce to what part of the filesystem and environment the workload can actually affect.

## Good Pushback To Practice

1. Why not standardize on Firecracker everywhere?
2. Why isn't trust equivalent to sandboxing?
3. What does the executor contract need to expose and what should it avoid exposing?
4. What is the real difference between workspace boundary and permission policy?

## What To Remember

The shortest accurate sentence is:

JACA already has the right execution seam; the main future work is strengthening what sits behind it.
