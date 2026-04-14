//go:build linux

package rpc

import (
	"os/exec"
	"syscall"
	"testing"
)

func TestSetParentDeathSignalLinux(t *testing.T) {
	cmd := exec.Command("/bin/true")
	setParentDeathSignal(cmd)

	if cmd.SysProcAttr == nil {
		t.Fatal("SysProcAttr should be populated")
	}
	if cmd.SysProcAttr.Pdeathsig != syscall.SIGTERM {
		t.Fatalf(
			"Pdeathsig=%d, want SIGTERM (%d)",
			cmd.SysProcAttr.Pdeathsig, syscall.SIGTERM,
		)
	}
}

func TestSetParentDeathSignalPreservesExistingSysProcAttr(t *testing.T) {
	cmd := exec.Command("/bin/true")
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	setParentDeathSignal(cmd)

	if cmd.SysProcAttr.Setsid != true {
		t.Error("setParentDeathSignal clobbered pre-existing Setsid flag")
	}
	if cmd.SysProcAttr.Pdeathsig != syscall.SIGTERM {
		t.Error("setParentDeathSignal did not set Pdeathsig when SysProcAttr existed")
	}
}
