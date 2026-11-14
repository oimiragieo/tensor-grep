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
