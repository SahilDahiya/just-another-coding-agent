//go:build windows

package main

import (
	"fmt"
	"os"
)

func readFileNoFollow(path string) ([]byte, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if info.Mode()&os.ModeSymlink != 0 {
		return nil, fmt.Errorf("symlinks are not supported on native Windows: %s", path)
	}
	return os.ReadFile(path)
}
