use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;

const PROTOCOL_VERSION: i64 = 1;
const WORKER_KIND: &str = "read_only";
const SUPPORTED_OPERATIONS: [&str; 2] = ["read", "ls"];

#[derive(Debug, Deserialize)]
struct RequestEnvelope {
    #[serde(rename = "type")]
    request_type: String,
}

#[derive(Debug, Deserialize)]
struct HelloRequest {
    request_id: String,
    protocol_version: i64,
    worker_kind: String,
}

#[derive(Debug, Serialize)]
struct HelloResponse<'a> {
    request_id: &'a str,
    #[serde(rename = "type")]
    response_type: &'static str,
    protocol_version: i64,
    worker_kind: &'static str,
    supported_operations: [&'static str; 2],
    supports_cancel: bool,
    supports_parallel_calls: bool,
}

#[derive(Debug, Deserialize)]
struct ReadRequest {
    request_id: String,
    workspace_root: String,
    path: String,
    offset: Option<usize>,
    limit: Option<usize>,
    max_lines: usize,
    max_bytes: usize,
}

#[derive(Debug, Serialize)]
struct ReadResult {
    request_id: String,
    #[serde(rename = "type")]
    response_type: &'static str,
    window_text: String,
    total_lines: usize,
    start_line: usize,
    end_line: usize,
    truncated: bool,
    next_offset: Option<usize>,
    first_line_exceeds_max_bytes: bool,
}

#[derive(Debug, Deserialize)]
struct LsRequest {
    request_id: String,
    workspace_root: String,
    path: Option<String>,
    limit: usize,
    max_bytes: usize,
}

#[derive(Debug, Serialize)]
struct LsEntry {
    name: String,
    is_dir: bool,
}

#[derive(Debug, Serialize)]
struct LsResult {
    request_id: String,
    #[serde(rename = "type")]
    response_type: &'static str,
    entries: Vec<LsEntry>,
    total_entries: usize,
    limit_hit: bool,
    byte_limit_hit: bool,
}

#[derive(Debug, Deserialize)]
struct CancelRequest {
    target_request_id: String,
}

#[derive(Debug, Serialize)]
struct ErrorResponse {
    request_id: String,
    #[serde(rename = "type")]
    response_type: &'static str,
    error_code: &'static str,
    message: String,
}

type CancelMap = Arc<Mutex<HashMap<String, Arc<AtomicBool>>>>;
type Writer = Arc<Mutex<io::Stdout>>;

fn write_json<T: Serialize>(writer: &Writer, value: &T) -> io::Result<()> {
    let mut handle = writer.lock().expect("stdout lock poisoned");
    serde_json::to_writer(&mut *handle, value)?;
    handle.write_all(b"\n")?;
    handle.flush()
}

fn write_error(
    writer: &Writer,
    request_id: impl Into<String>,
    error_code: &'static str,
    message: impl Into<String>,
) -> io::Result<()> {
    write_json(
        writer,
        &ErrorResponse {
            request_id: request_id.into(),
            response_type: "error",
            error_code,
            message: message.into(),
        },
    )
}

fn normalize_workspace_root(root: &str) -> Result<PathBuf, String> {
    let resolved = fs::canonicalize(root).map_err(|error| error.to_string())?;
    let metadata = fs::metadata(&resolved).map_err(|error| error.to_string())?;
    if !metadata.is_dir() {
        return Err(format!(
            "workspace root is not a directory: {}",
            resolved.display()
        ));
    }
    Ok(resolved)
}

fn resolve_workspace_path(workspace_root: &str, tool_path: &str) -> Result<PathBuf, String> {
    let root = normalize_workspace_root(workspace_root)?;
    let candidate = Path::new(tool_path);
    let joined = if candidate.is_absolute() {
        candidate.to_path_buf()
    } else {
        root.join(candidate)
    };
    fs::canonicalize(&joined).map_err(|error| error.to_string())
}

fn split_lines_keep_ends(text: &str) -> Vec<String> {
    if text.is_empty() {
        return Vec::new();
    }
    text.split_inclusive('\n')
        .map(std::string::ToString::to_string)
        .collect()
}

fn truncate_head_line_window(
    lines: &[String],
    max_lines: usize,
    max_bytes: usize,
) -> (String, usize, bool, bool) {
    let mut output = String::new();
    let mut output_bytes = 0usize;
    let mut line_count = 0usize;

    for line in lines {
        if line_count >= max_lines {
            return (output, line_count, true, false);
        }
        let line_bytes = line.as_bytes().len();
        if line_count == 0 && line_bytes > max_bytes {
            return (String::new(), 0, true, true);
        }
        if output_bytes + line_bytes > max_bytes {
            return (output, line_count, true, false);
        }
        output.push_str(line);
        output_bytes += line_bytes;
        line_count += 1;
    }

    (output, line_count, false, false)
}

fn execute_read(req: ReadRequest) -> Result<ReadResult, ErrorResponse> {
    let workspace_root = normalize_workspace_root(&req.workspace_root).map_err(|message| {
        ErrorResponse {
            request_id: req.request_id.clone(),
            response_type: "error",
            error_code: "path_error",
            message,
        }
    })?;
    let resolved_path = resolve_workspace_path(workspace_root.to_string_lossy().as_ref(), &req.path)
        .map_err(|message| ErrorResponse {
            request_id: req.request_id.clone(),
            response_type: "error",
            error_code: "path_error",
            message,
        })?;

    let text = fs::read_to_string(&resolved_path).map_err(|error| ErrorResponse {
        request_id: req.request_id.clone(),
        response_type: "error",
        error_code: "path_error",
        message: error.to_string(),
    })?;
    let lines = split_lines_keep_ends(&text);

    if lines.is_empty() {
        let start_line = req.offset.unwrap_or(1);
        if start_line != 1 {
            return Err(ErrorResponse {
                request_id: req.request_id,
                response_type: "error",
                error_code: "operational_error",
                message: format!("offset {} is beyond end of file (0 lines total)", start_line),
            });
        }
        return Ok(ReadResult {
            request_id: req.request_id,
            response_type: "read_result",
            window_text: String::new(),
            total_lines: 0,
            start_line: 1,
            end_line: 1,
            truncated: false,
            next_offset: None,
            first_line_exceeds_max_bytes: false,
        });
    }

    let start_line = req.offset.unwrap_or(1);
    let start_index = start_line.saturating_sub(1);
    if start_index >= lines.len() {
        return Err(ErrorResponse {
            request_id: req.request_id,
            response_type: "error",
            error_code: "operational_error",
            message: format!(
                "offset {} is beyond end of file ({} lines total)",
                start_line,
                lines.len()
            ),
        });
    }

    let mut selected = lines[start_index..].to_vec();
    if let Some(limit) = req.limit {
        if limit < selected.len() {
            selected.truncate(limit);
        }
    }

    let (window_text, line_count, truncated, first_line_exceeds_max_bytes) =
        truncate_head_line_window(&selected, req.max_lines, req.max_bytes);
    let end_line = std::cmp::max(start_line, start_line + line_count.saturating_sub(1));
    let next_offset = if truncated
        || req
            .limit
            .map(|_| start_index + line_count < lines.len())
            .unwrap_or(false)
    {
        Some(start_index + line_count + 1)
    } else {
        None
    };

    Ok(ReadResult {
        request_id: req.request_id,
        response_type: "read_result",
        window_text,
        total_lines: lines.len(),
        start_line,
        end_line,
        truncated,
        next_offset,
        first_line_exceeds_max_bytes,
    })
}

fn execute_ls(req: LsRequest) -> Result<LsResult, ErrorResponse> {
    let workspace_root = normalize_workspace_root(&req.workspace_root).map_err(|message| {
        ErrorResponse {
            request_id: req.request_id.clone(),
            response_type: "error",
            error_code: "path_error",
            message,
        }
    })?;
    let target = req.path.unwrap_or_else(|| ".".to_string());
    let resolved_path = resolve_workspace_path(workspace_root.to_string_lossy().as_ref(), &target)
        .map_err(|message| ErrorResponse {
            request_id: req.request_id.clone(),
            response_type: "error",
            error_code: "path_error",
            message,
        })?;
    let metadata = fs::metadata(&resolved_path).map_err(|error| ErrorResponse {
        request_id: req.request_id.clone(),
        response_type: "error",
        error_code: "path_error",
        message: error.to_string(),
    })?;
    if !metadata.is_dir() {
        return Err(ErrorResponse {
            request_id: req.request_id,
            response_type: "error",
            error_code: "path_error",
            message: format!("not a directory: {}", resolved_path.display()),
        });
    }

    let mut entries: Vec<_> = fs::read_dir(&resolved_path)
        .map_err(|error| ErrorResponse {
            request_id: req.request_id.clone(),
            response_type: "error",
            error_code: "path_error",
            message: error.to_string(),
        })?
        .filter_map(Result::ok)
        .collect();
    entries.sort_by_key(|entry| entry.file_name().to_string_lossy().to_lowercase());

    let mut result_entries = Vec::new();
    let mut output_bytes = 0usize;
    let mut limit_hit = false;
    let mut byte_limit_hit = false;

    for entry in &entries {
        if result_entries.len() >= req.limit {
            limit_hit = true;
            break;
        }
        let name = entry.file_name().to_string_lossy().to_string();
        let item_bytes = name.as_bytes().len() + 1;
        if output_bytes + item_bytes > req.max_bytes {
            byte_limit_hit = true;
            break;
        }
        let is_dir = entry.file_type().map(|file_type| file_type.is_dir()).unwrap_or(false);
        result_entries.push(LsEntry { name, is_dir });
        output_bytes += item_bytes;
    }

    Ok(LsResult {
        request_id: req.request_id,
        response_type: "ls_result",
        entries: result_entries,
        total_entries: entries.len(),
        limit_hit,
        byte_limit_hit,
    })
}

fn main() -> io::Result<()> {
    let stdin = io::stdin();
    let writer = Arc::new(Mutex::new(io::stdout()));
    let cancellations: CancelMap = Arc::new(Mutex::new(HashMap::new()));

    for line in stdin.lock().lines() {
        let line = line?;
        let envelope: RequestEnvelope = match serde_json::from_str(&line) {
            Ok(value) => value,
            Err(error) => {
                write_error(&writer, "unknown", "invalid_request", error.to_string())?;
                continue;
            }
        };

        match envelope.request_type.as_str() {
            "hello" => {
                let request: HelloRequest = match serde_json::from_str(&line) {
                    Ok(value) => value,
                    Err(error) => {
                        write_error(&writer, "unknown", "invalid_request", error.to_string())?;
                        continue;
                    }
                };
                if request.protocol_version != PROTOCOL_VERSION
                    || request.worker_kind != WORKER_KIND
                {
                    write_error(
                        &writer,
                        request.request_id,
                        "protocol_error",
                        "unsupported protocol or worker kind",
                    )?;
                    continue;
                }
                write_json(
                    &writer,
                    &HelloResponse {
                        request_id: &request.request_id,
                        response_type: "hello_ok",
                        protocol_version: PROTOCOL_VERSION,
                        worker_kind: WORKER_KIND,
                        supported_operations: SUPPORTED_OPERATIONS,
                        supports_cancel: true,
                        supports_parallel_calls: true,
                    },
                )?;
            }
            "call_read" => {
                let request: ReadRequest = match serde_json::from_str(&line) {
                    Ok(value) => value,
                    Err(error) => {
                        write_error(&writer, "unknown", "invalid_request", error.to_string())?;
                        continue;
                    }
                };
                let writer = Arc::clone(&writer);
                let cancellations = Arc::clone(&cancellations);
                let request_id = request.request_id.clone();
                let cancelled = Arc::new(AtomicBool::new(false));
                cancellations
                    .lock()
                    .expect("cancel map lock poisoned")
                    .insert(request_id.clone(), Arc::clone(&cancelled));
                thread::spawn(move || {
                    let response = execute_read(request);
                    let _ = if cancelled.load(Ordering::SeqCst) {
                        write_error(&writer, request_id.clone(), "cancelled", "request cancelled")
                    } else {
                        match response {
                            Ok(result) => write_json(&writer, &result),
                            Err(error) => write_json(&writer, &error),
                        }
                    };
                    cancellations
                        .lock()
                        .expect("cancel map lock poisoned")
                        .remove(&request_id);
                });
            }
            "call_ls" => {
                let request: LsRequest = match serde_json::from_str(&line) {
                    Ok(value) => value,
                    Err(error) => {
                        write_error(&writer, "unknown", "invalid_request", error.to_string())?;
                        continue;
                    }
                };
                let writer = Arc::clone(&writer);
                let cancellations = Arc::clone(&cancellations);
                let request_id = request.request_id.clone();
                let cancelled = Arc::new(AtomicBool::new(false));
                cancellations
                    .lock()
                    .expect("cancel map lock poisoned")
                    .insert(request_id.clone(), Arc::clone(&cancelled));
                thread::spawn(move || {
                    let response = execute_ls(request);
                    let _ = if cancelled.load(Ordering::SeqCst) {
                        write_error(&writer, request_id.clone(), "cancelled", "request cancelled")
                    } else {
                        match response {
                            Ok(result) => write_json(&writer, &result),
                            Err(error) => write_json(&writer, &error),
                        }
                    };
                    cancellations
                        .lock()
                        .expect("cancel map lock poisoned")
                        .remove(&request_id);
                });
            }
            "cancel" => {
                let request: CancelRequest = match serde_json::from_str(&line) {
                    Ok(value) => value,
                    Err(error) => {
                        write_error(&writer, "unknown", "invalid_request", error.to_string())?;
                        continue;
                    }
                };
                if let Some(flag) = cancellations
                    .lock()
                    .expect("cancel map lock poisoned")
                    .get(&request.target_request_id)
                    .cloned()
                {
                    flag.store(true, Ordering::SeqCst);
                }
            }
            "shutdown" => {
                return Ok(());
            }
            other => {
                write_error(
                    &writer,
                    "unknown",
                    "unsupported_operation",
                    format!("unsupported request type: {}", other),
                )?;
            }
        }
    }

    Ok(())
}
