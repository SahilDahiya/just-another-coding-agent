//go:build windows

package main

import (
	"fmt"
	"os"

	"golang.org/x/sys/windows"
)

const utf8CodePage = 65001

func configureConsoleEncoding() error {
	if !stdoutIsConsole() {
		return nil
	}
	if err := windows.SetConsoleCP(utf8CodePage); err != nil {
		return fmt.Errorf("set Windows console input code page to UTF-8: %w", err)
	}
	if err := windows.SetConsoleOutputCP(utf8CodePage); err != nil {
		return fmt.Errorf("set Windows console output code page to UTF-8: %w", err)
	}
	return nil
}

func stdoutIsConsole() bool {
	handle := windows.Handle(os.Stdout.Fd())
	var mode uint32
	return windows.GetConsoleMode(handle, &mode) == nil
}
