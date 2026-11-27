use anyhow::{Error, Result};

const JSON_OUTPUT_VERSION: u32 = 1;

fn emit_search_error_json(error: &str, detail: &str) {
    println!(
        "{}",
        serde_json::json!({
            "version": JSON_OUTPUT_VERSION,
            "ok": false,
            "error": error,
            "detail": detail,
        })
    );
}

fn exit_search_error_json(error: &str, detail: impl Into<String>) -> ! {
    emit_search_error_json(error, &detail.into());
    std::process::exit(2);
}

pub fn exit_structured_search_error_if_needed(
    json: bool,
    ndjson: bool,
    error: &str,
    detail: impl Into<String>,
) -> ! {
    let detail = detail.into();
    if json && !ndjson {
        exit_search_error_json(error, detail);
    }
    if ndjson {
        println!(
            "{}",
            serde_json::json!({
                "version": JSON_OUTPUT_VERSION,
                "type": "error",
                "error": error,
                "detail": detail,
            })
        );
        std::process::exit(2);
    }
    eprintln!("{detail}");
    std::process::exit(2);
}

pub fn refuse_structured_pcre2(pcre2: bool, json: bool, ndjson: bool, explicit_rg_format: bool) {
    if pcre2 && (ndjson || (json && !explicit_rg_format)) {
        exit_structured_search_error_if_needed(
            json,
            ndjson,
            "unsupported_pcre2_structured_output",
            "aggregate JSON and NDJSON do not support PCRE2; use --format rg --json for ripgrep's event protocol",
        );
    }
}

fn search_error_code_for_message(message: &str) -> Option<&'static str> {
    let lower = message.to_ascii_lowercase();
    if lower.contains("non-empty pattern") || lower.contains("pattern must not be empty") {
        Some("empty_pattern")
    } else if lower.contains("path does not exist") {
        Some("path_not_found")
    } else if lower.contains("failed to compile native search pattern")
        || lower.contains("regex parse error")
        || lower.contains("error parsing regex")
        || lower.contains("invalid regex")
    {
        Some("invalid_regex")
    } else {
        None
    }
}

fn normalize_search_error_detail(error: &str, detail: &str) -> String {
    if error == "invalid_regex" && !detail.to_ascii_lowercase().contains("invalid regex") {
        format!("invalid regex pattern: {detail}")
    } else {
        detail.to_string()
    }
}

pub fn exit_json_search_runtime_error_if_needed(json: bool, ndjson: bool, err: &Error) {
    if !json && !ndjson {
        return;
    }
    let detail = err.to_string();
    let code = search_error_code_for_message(&detail).unwrap_or("backend_error");
    exit_structured_search_error_if_needed(
        json,
        ndjson,
        code,
        normalize_search_error_detail(code, &detail),
    );
}

pub fn handle_search_runtime_error(
    json: bool,
    ndjson: bool,
    explicit_rg_format: bool,
    err: Error,
) -> Result<()> {
    if crate::broken_pipe::error_chain_has_broken_pipe(&err) {
        return Err(err);
    }
    if (json || ndjson) && !explicit_rg_format {
        exit_json_search_runtime_error_if_needed(json, ndjson, &err);
        std::process::exit(2);
    }
    crate::exit_codes::exit_with_run_error(err)
}

pub fn exit_gpu_sidecar_error_if_structured(
    json: bool,
    ndjson: bool,
    raw_detail: &str,
    exit_code: Option<i32>,
) {
    if !json && !ndjson {
        return;
    }
    let trimmed = raw_detail.trim();
    let detail = if trimmed.is_empty() {
        match exit_code {
            Some(code) => format!("GPU sidecar exited with code {code}"),
            None => "GPU sidecar execution failed".to_string(),
        }
    } else {
        trimmed.to_string()
    };
    exit_structured_search_error_if_needed(json, ndjson, "sidecar_error", detail);
}

pub fn exit_gpu_cpu_fallback_flag_unhonorable(
    flag_name: &str,
    rg_available: bool,
    json: bool,
    ndjson: bool,
) -> ! {
    let message = if rg_available {
        format!(
            "error: {flag_name} is not supported by native GPU-fallback CPU search, and ripgrep's \
             output shape cannot represent structured --json/--ndjson output here; refusing rather \
             than silently ignoring {flag_name}. Drop --json/--ndjson, or drop {flag_name}."
        )
    } else {
        format!(
            "error: {flag_name} is not supported by native GPU-fallback CPU search, and the \
             ripgrep (`rg`) backend is unavailable to honor it instead; refusing rather than \
             silently ignoring {flag_name}. Install `rg`, set TG_RG_PATH, or drop {flag_name}."
        )
    };
    exit_structured_search_error_if_needed(json, ndjson, "unsupported_flag", message);
}

pub fn classify_gpu_sidecar_unavailable(stderr: &str, message: &str) -> Option<String> {
    let mut raw = String::new();
    if !stderr.trim().is_empty() {
        raw.push_str(stderr.trim());
    }
    if !message.trim().is_empty() {
        if !raw.is_empty() {
            raw.push(' ');
        }
        raw.push_str(message.trim());
    }
    let lower = raw.to_ascii_lowercase();
    let unavailable = lower.contains("cuda_visible_devices is empty")
        || lower.contains("no gpus are visible")
        || lower.contains("cuda is unavailable")
        || lower.contains("no usable gpu devices")
        || lower.contains("no cuda devices")
        || lower.contains("available device ids: none")
        || lower.contains("could not initialize a gpu backend")
        || lower.contains("cudf and torch gpu backends were unavailable")
        || lower.contains("no routable gpu chunk plan was available")
        || lower.contains("torch backend imports failed")
        || (lower.contains("requested gpu device ids") && lower.contains("not available"));
    if unavailable {
        Some(if raw.is_empty() {
            "sidecar reported no usable GPU devices".to_string()
        } else {
            raw
        })
    } else {
        None
    }
}
