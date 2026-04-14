//go:build !linux

package rpc

import "os/exec"

// setParentDeathSignal is a no-op on non-Linux platforms. Windows uses Job
// Objects for similar parent-death enforcement, and macOS does not expose
// a comparable primitive; neither is wired here. Without pdeathsig the
// Go TUI relies on cooperative signal handling and the bubbletea exit
// path to clean up the backend on TUI termination.
func setParentDeathSignal(_ *exec.Cmd) {}
