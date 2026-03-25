# Repository Guidelines

## Start Here
- Read `docs/README.md` first.
- The active direction is in `docs/goal.md`, `docs/architecture.md`, and `docs/contracts.md`.
- Use `docs/grounding.md` for PydanticAI grounding rules.
- Do not import architecture from old repos unless the user explicitly asks for it.

## Grounding
- For PydanticAI design or implementation questions, use the official docs instead of memory.
- Prefer `https://ai.pydantic.dev/llms.txt` to find the right section quickly, then read the exact linked page or API reference.
- Use `https://ai.pydantic.dev/llms-full.txt` only when one large grounding source is justified.

## Working Style
- Keep changes small and reviewable.
- Use ASCII by default.
- Python 3.12 with 4-space indentation.
- Prefer one root package and one canonical codepath.

## Testing
- For bug reports, start with a reproducing test.
- Default to strict TDD for durable feature development.
- Use full red-to-green cycles: failing test first, minimal fix, then refactor.
- Run relevant tests when available and say what you did not run.

## Engineering Direction
- No fallback behavior, ever.
- Fail hard on failure conditions.
- No backward compatibility guarantees.
- Forward-only development: do not carry legacy baggage.

## Hard-Cut Product Policy
- Optimize for one canonical current-state implementation.
- Prefer fail-fast diagnostics and explicit recovery steps.
- Do not add migration shims, compatibility bridges, fallback paths, or dual behavior unless the user explicitly asks for them.
- Do not add automatic migration.
- Do not add silent fallbacks.
- If temporary compatibility code is introduced, the same diff must state why it exists and the exact deletion criteria.

## Docs
- Update relevant docs when behavior, contracts, or implementation direction changes.
- Every new doc must be linked in `docs/README.md`.
- Include a `read_when` line at the top of each doc.

## Git
- Safe by default: `git status`, `git diff`, `git log`.
- No destructive operations unless explicitly requested.
- Do not delete or rename unexpected files without stopping first.

## Decision Rules
- Fix root cause, not symptoms.
- If unsure, read code and docs before asking.
- Protect the public coding-agent contract, not internal implementation details.
