# Troubleshooting

This page documents the failures we have seen in the wild, what they mean,
and how to recover. If you hit something not listed here, see
[How to file a useful bug report](#how-to-file-a-useful-bug-report) at the
bottom.

## The TUI shows `invalid character 'N' looking for beginning of value`

**Symptom.** One or more error lines in the transcript prefixed by a
command name — for example `auth status: invalid character 'N' looking
for beginning of value` or `model catalog: invalid character 'N' looking
for beginning of value`. Every subsequent RPC call shows the same error
until the TUI is restarted.

**What it means.** Something the Python backend wrote to its stdout is
not valid JSON. The Go TUI reads stdout line-by-line and parses each
line as a JSON-RPC envelope, so **one non-JSON line poisons the entire
client** — the dispatch loop fails all waiters with the same decode
error. The `N` (or whatever the first offending byte happens to be) is
the first character of that bad line.

**Immediate recovery.**

1. Upgrade to the latest release. Several classes of this bug were
   closed between `0.1.20` and `0.1.21`.

   ```bash
   uv tool upgrade just-another-coding-agent
   ```

2. Restart the TUI and try again.

3. If it still happens, capture the exact stdout the backend is
   producing and file a bug report (see below). Starting with `0.1.22`
   the TUI error itself includes a hex dump of the offending bytes —
   please paste the full error into the bug report, not just the first
   line.

**What we did about it.** Starting with `0.1.22` the Go decode error
carries the line length, a hex dump of the first 32 bytes, and a
printable preview, so a single user report identifies the offending
code path unambiguously. The Python backend also has a contract test
that asserts every line on its stdout is a valid JSON-RPC envelope
under all startup conditions — this is an invariant test, meaning any
future dependency or code path that accidentally prints to stdout
fails CI immediately.

## `jaca` processes are still running after I closed the terminal

**Symptom.** `ps aux | grep jaca` shows `jaca`, `jaca-go`, or `python
-m just_another_coding_agent` processes long after you closed the
terminal, shell tab, or recording session. In the worst case they sit
there for hours or days holding file handles and background resources.

**What it means.** The subprocess tree was orphaned — the parent
process (your terminal, shell, recorder, or SSH session) died in a way
that did not cleanly signal its children. This usually happens when:

- A terminal tab is closed without `/quit`-ing the TUI first.
- An SSH session drops mid-session.
- An `asciinema rec` session is abandoned without `Ctrl+D`.
- A `tmux` or `screen` window is killed while `jaca` is running inside.

**Immediate recovery.** Kill the stragglers by hand. The safest order
is the root of the process tree first; SIGTERM usually suffices, fall
back to SIGKILL for anything that will not exit.

```bash
pgrep -af "jaca|just_another_coding_agent"
# find the root PID of the chain you want to kill, then:
kill <root-pid>
# if they do not exit within a few seconds:
kill -9 <root-pid>
```

**What we did about it.** Starting with `0.1.22` the Python wrapper,
the Go TUI, and the Python backend all spawn child processes with
kernel-enforced parent-death propagation via `prctl(PR_SET_PDEATHSIG,
SIGTERM)` on Linux. When the direct parent of any of these subprocesses
dies for any reason — clean exit, crash, OOM, SIGKILL, abandoned PTY —
the kernel delivers SIGTERM to the child automatically. No userland
signal handler required. A regression test spawns an intermediate
process that holds a worker client, SIGKILLs the intermediate, and
asserts the worker is gone within 3 seconds; it verifies the kernel
guarantee is still wired up on every release.

Note that this guarantee is Linux-only. On macOS and Windows the
subprocess cleanup still depends on cooperative signal handling — if
you experience orphaned `jaca` processes on those platforms, please
file a bug.

## `uv tool install` on Windows: `WinError 216` or `WinError 225`

**Symptom.** `uv tool install just-another-coding-agent` or a launch
of `jaca` fails with an error mentioning Windows Application Control,
Smart App Control, Microsoft Defender, or "malicious binary reputation",
or a raw error code like `WinError 216` / `WinError 225`.

**What it means.** Windows is blocking the bundled `jaca-go.exe`
because it is unsigned or Defender does not recognize it. The Python
wrapper tries to launch the blocked executable and the `subprocess.run`
call raises an `OSError` with one of these WinError codes.

**Immediate recovery.**

1. **From inside a clone of the repository**: launch via the Go
   toolchain instead of the installed binary:

   ```bash
   JACA_GO_RUN=1 uv run jaca
   ```

   This builds and runs the Go TUI via `go run`, bypassing the
   Defender-quarantined bundled binary. Requires a Go toolchain
   installed locally.

2. **Reinstall**: run the install command the error message suggests,
   which rebuilds the Go TUI from source inside the uv tool venv and
   typically produces a binary Defender accepts.

3. If neither works, the signed-wheel release is the long-term fix.

**What the error looks like.** Starting with `0.1.21` the Python
wrapper catches these OSErrors and reformats them into a readable
message that lists the blocked executable, the repair command, and the
`JACA_GO_RUN=1` workaround. If you see a raw `WinError` instead, you
are on an old version — upgrade.

## How to file a useful bug report

A single well-formed bug report is worth a week of investigation. If
something went wrong, please include:

1. **Version**: output of `jaca --version` or `uv tool list | grep jaca`.
2. **OS and environment**: `uname -a` on Linux/macOS, `winver` on
   Windows. Whether you are inside WSL, a remote SSH, `tmux`, `screen`,
   `asciinema`, etc.
3. **The exact error text** from the TUI. For `invalid character`
   errors from `0.1.22` or later, the full error (which includes a hex
   preview) is the most useful single piece of data.
4. **A stdout capture of the backend** if the bug involves the backend
   crashing or emitting unexpected output. The one-liner:

   ```bash
   echo '{"id":"x","command":"auth.status","payload":{}}' \
     | jaca --headless --workspace-root . 2>/tmp/jaca-stderr.log \
     > /tmp/jaca-stdout.log
   # then attach /tmp/jaca-stdout.log and /tmp/jaca-stderr.log
   ```

5. **Whether clearing auth helps**. If the bug might be auth-related,
   see if `jaca` works after clearing `~/.jaca/oauth.json` and
   `~/.jaca/auth.json` (back them up first).
6. **Relevant `~/.jaca/config.json`** (redact any API keys).

If you suspect process lifecycle issues, please include `pgrep -af
jaca` output taken after the failure so we can see what parent tree the
orphans came from.
