//go:build linux

package rpc

import (
	"os/exec"
	"syscall"
)

// setParentDeathSignal configures the given command so the kernel delivers
// SIGTERM to the child when this Go process (the parent) dies for any
// reason — clean exit, crash, OOM, SIGKILL, orphaned under an abandoned
// PTY. The guarantee is kernel-enforced via prctl(PR_SET_PDEATHSIG) and
// does not depend on cooperative signal handling on either side.
//
// This closes the observed failure mode where an abandoned Go TUI session
// (for example, a terminal closed without /quit, or an asciinema recorder
// holding the PTY open) left the Python backend running for hours or days
// after the Go TUI itself had exited.
func setParentDeathSignal(cmd *exec.Cmd) {
	if cmd.SysProcAttr == nil {
		cmd.SysProcAttr = &syscall.SysProcAttr{}
	}
	cmd.SysProcAttr.Pdeathsig = syscall.SIGTERM
}
