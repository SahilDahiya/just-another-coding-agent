"""Kernel-enforced parent-death propagation for subprocess spawning.

Used as a ``preexec_fn`` on Linux when spawning the Go TUI, the headless
backend, and the read-only worker. On any other platform it is a no-op,
so call sites can pass it unconditionally on POSIX or guard with an
``os.name != "nt"`` check.

The goal is a single guarantee: **if the spawning Python or Go process
dies for any reason — clean exit, crash, OOM, SIGKILL, orphaned under an
abandoned PTY — the spawned child receives SIGTERM from the kernel and
exits promptly.** No userland signal handler, no cleanup path, no
cooperation from the parent required.

On Linux this is implemented via ``prctl(PR_SET_PDEATHSIG, SIGTERM)``,
which the kernel honors when the caller's parent thread exits. The call
is made from the ``preexec_fn`` hook, which runs in the child after fork
and before exec, so the flag is set while the child's parent pointer
still refers to the spawning process.

There is a narrow race between ``fork`` and ``prctl``: if the parent
dies in that window, the child will never be told. We close the race by
checking ``getppid()`` after setting the flag; if the result is 1 (init)
the parent already died and we self-TERM immediately rather than
lingering as an orphan.
"""

from __future__ import annotations

import ctypes
import os
import signal
import sys

_PR_SET_PDEATHSIG = 1
"""Linux ``prctl`` option number for PR_SET_PDEATHSIG. See ``man 2 prctl``."""


def set_pdeathsig_in_child() -> None:
    """Set PR_SET_PDEATHSIG=SIGTERM on the calling process.

    Safe to use as ``preexec_fn`` on Linux. No-op on other platforms.
    Silently returns on any error so a failed prctl does not break
    child startup.
    """
    if sys.platform != "linux":
        return
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        return
    if libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
        return
    # Close the fork/prctl race: if the parent already died before we
    # called prctl, our parent is now init (pid 1) and the kernel will
    # never deliver PDEATHSIG. Self-TERM so we do not linger as an
    # orphan backend holding a stale stdin pipe to a dead Go TUI.
    if os.getppid() == 1:
        os.kill(os.getpid(), signal.SIGTERM)


__all__ = ["set_pdeathsig_in_child"]
