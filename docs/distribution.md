# Distribution

read_when: you are working on install, packaging, release, or cross-language distribution behavior

JACA has three distinct distribution lanes. They should stay explicit.

## Published Install

This is the normal end-user path.

- install with `uv tool install just-another-coding-agent`
- run with `jaca`
- no local Go toolchain should be required
- published wheels must already contain both bundled binaries:
  - `jaca-go`
  - `jaca-read-only-worker`

For published installs, startup must not try to auto-build or auto-install
missing binaries. Repair is explicit. If a packaged binary is missing, the
message should point to reinstall or repair of the installed package, not to
repo-only contributor commands.

## Repo Checkout

This is the contributor path.

- `uv sync --extra dev --extra test` is the default setup
- that setup builds the read-only worker because backend tools depend on it
- it does not build the packaged `jaca-go` binary by default
- `uv run jaca` is still the canonical launcher in a repo checkout

Repo checkout behavior should be smooth:

- if `jaca-go` is already present, use it
- if `jaca-go` is missing but `go` is available, fall back to `go run ./cmd/jaca`
- if both are unavailable, fail hard with the explicit repo rebuild command

That keeps development unblocked without hiding packaging defects in published
installs.

## Release

Release is the packaging path, not the development path.

- full CI should validate branch commits before tagging
- tag pushes should trigger the release workflow, not a redundant second full CI pass
- release builds platform-specific wheels plus one sdist
- release verification should happen twice:
  - each wheel must contain the bundled binaries
  - the assembled release manifest must contain every supported artifact

Current supported packaged targets are:

- Linux `x86_64`
- Windows `amd64`
- macOS `x86_64`
- macOS `arm64`

## Rules

- Treat the Python package as the distribution root. Go executables are bundled implementation artifacts, not separate products.
- Keep runtime semantics in Python. Bundled Go binaries are execution helpers and UI clients.
- Avoid startup-time installers, auto-builds, or hidden environment mutation.
- Prefer explicit in-app update choices over blocking shell prompts:
  - use cached release info for the current launch and refresh it in the background for the next one
  - `Update now` exits cleanly and runs the exact external updater command
  - `Later` snoozes the notice for a bounded window
  - `Skip this version` suppresses only that exact release, not all future checks
- Keep repair commands context-aware:
  - repo checkout: rebuild with `uv sync`
  - isolated uv tool install: repair with `uv tool upgrade --reinstall`
  - generic environment: reinstall the package explicitly
- Keep PyPI-facing docs aligned with the actual command surface. Outdated slash commands on the package page are a distribution bug.
