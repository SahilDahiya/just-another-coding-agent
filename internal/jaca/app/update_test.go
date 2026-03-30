package app

import "testing"

func TestIsNewerReleaseVersion(t *testing.T) {
	newer, ok := isNewerReleaseVersion("0.1.0", "0.1.1")
	if !ok || !newer {
		t.Fatalf("isNewerReleaseVersion() = (%v, %v), want (true, true)", newer, ok)
	}

	newer, ok = isNewerReleaseVersion("0.1.1", "0.1.1")
	if !ok || newer {
		t.Fatalf("equal versions = (%v, %v), want (false, true)", newer, ok)
	}

	newer, ok = isNewerReleaseVersion("dev", "0.1.1")
	if ok || newer {
		t.Fatalf("invalid current version = (%v, %v), want (false, false)", newer, ok)
	}
}
