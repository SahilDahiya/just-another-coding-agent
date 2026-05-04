//go:build !windows

package main

import (
	"io"
	"os"
	"path/filepath"

	"golang.org/x/sys/unix"
)

func readFileNoFollow(path string) ([]byte, error) {
	parent := filepath.Dir(path)
	name := filepath.Base(path)

	dirFD, err := unix.Open(parent, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	defer unix.Close(dirFD)

	fileFD, err := unix.Openat(
		dirFD,
		name,
		unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_CLOEXEC,
		0,
	)
	if err != nil {
		return nil, err
	}

	file := os.NewFile(uintptr(fileFD), path)
	defer file.Close()
	return io.ReadAll(file)
}
