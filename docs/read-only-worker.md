# Read-Only Worker

read_when: you are designing, implementing, or benchmarking the persistent read-only worker

## Purpose

The read-only worker is the internal execution seam for high-frequency
read-only tools:

- `read`
- `ls`
- `find`
- `grep`

It exists to replace per-call Python subprocess execution with one persistent
helper process and now serves as the canonical backend implementation for
read-only tool semantics.

This is not a public API and not a second backend runtime.

The current implementation ships as a separate Go helper binary:

- `cmd/jaca-read-only-worker`
- installed as `jaca-read-only-worker`

Source installs in constrained environments such as Harbor task containers use
an explicit prebuilt-helper path instead of running `go build` during package
installation. The packaging seam is still explicit and fail-fast:
`JACA_PREBUILT_READ_ONLY_WORKER` must point at the uploaded helper binary or
installation fails.

## Boundary

Python remains the owner of:

- tool schemas and validation
- activity semantics
- session and RPC meaning
- contract tests

The worker canonically owns read-only tool behavior for:

- error wording
- truncation behavior
- continuation hints
- relative path rendering
- match formatting

The worker must not own session or RPC behavior or become a silent fallback
path.

## Transport

The protocol is UTF-8 JSON Lines over stdio.

Rules:

- one JSON object per line
- every message has a non-empty `request_id`
- the client sends an initial `hello`
- operation responses may arrive out of order, so `request_id` is the routing key
- each operation request must end in exactly one terminal response
- cancellation is best-effort and targets a prior `request_id`
- shutdown is explicit

The current internal protocol version is `1`.

## Message Types

Requests:

- `hello`
- `call_read`
- `call_ls`
- `call_find`
- `call_grep`
- `cancel`
- `shutdown`

Responses:

- `hello_ok`
- `read_result`
- `ls_result`
- `find_result`
- `grep_result`
- `error`

## Structured Results

The worker returns structured protocol payloads that encode the canonical
read-only tool semantics.

Examples:

- `read_result` returns a bounded `window_text` plus line metadata such as
  `total_lines`, `start_line`, `end_line`, `truncated`, and `next_offset`
- `ls_result` returns structured entries with `name` and `is_dir`
- `find_result` returns matched relative paths plus limit/byte-limit flags
- `grep_result` returns structured matches with `path`, `line_number`, `text`,
  and truncation flags

Python is responsible for exposing those canonical semantics through tool
registration, activity metadata, and runtime/session/RPC surfaces without
keeping a second semantic implementation locally.

## Error Codes

Tool-domain worker errors:

- `command_error`
- `encoding_error`
- `operational_error`
- `path_error`

Worker/protocol failures:

- `invalid_request`
- `unsupported_operation`
- `protocol_error`
- `cancelled`

Only the first group maps back into canonical tool-domain failures. The second
group is a hard runtime failure path.

## Lifecycle

Expected client flow:

1. Start the helper process.
2. Send `hello`.
3. Verify protocol version, worker kind, operations, and capabilities.
4. Send operation requests.
5. On client-side timeout or abandonment, send `cancel` for the in-flight
   request, then kill the worker if it does not terminate the request cleanly.
6. On normal shutdown, send `shutdown` and wait briefly before force-killing if
   needed.

## Design Intent

This contract is intentionally language-neutral.

The same Python caller and the same internal protocol were intentionally used
for the Go and Rust spikes before the repo chose the separate Go helper as the
current implementation.
