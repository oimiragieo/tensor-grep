#![allow(unused_imports, dead_code)]
use crate::native_multi_pattern::collect_native_multi_pattern_matches;
use crate::validation_rollback::{
    collect_validation_rollback_snapshots, emit_rollback_status,
    restore_validation_rollback_snapshots, retained_validation_targets, ValidationRollbackSummary,
};
use anyhow::Context;
#[cfg(feature = "cuda")]
use clap::ValueEnum;
use clap::{Args, CommandFactory, Parser, Subcommand};
use hmac::{Hmac, Mac};
use tensor_grep_rs::rg_passthrough::generated_scan_dir_names;
// Bug #88/#480/#100: `implicit_search_walk_exceeds_ceiling` (the WALK-ceiling probe) moved to
// `rg_passthrough.rs` (a library module, not this binary crate root) -- see the breadcrumb
// comment above `parse_early_ripgrep_args`. `WalkBuilder`'s only remaining consumer in THIS file
// is `count_search_corpus_bytes`, which is cuda-gated, so both imports are cuda-gated now.
use crate::main_env::env_flag_enabled;
use crate::search_files::format_broad_generated_scan_error;
use crate::search_flag_registry::{
    raw_args_contain_any_flag, search_args_contain_any_flag, SEARCH_OPTION_FIRST_FLAGS,
    SEARCH_PYTHON_PASSTHROUGH_FLAGS,
};
#[cfg(feature = "cuda")]
use ignore::overrides::OverrideBuilder;
#[cfg(feature = "cuda")]
use ignore::WalkBuilder;
use process_control::{ChildExt, Control};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::ffi::{OsStr, OsString};
#[cfg(feature = "cuda")]
use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Mutex, OnceLock};
use std::time::Instant;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tensor_grep_rs::backend_ast::{
    AstBackend, AstMatch, AstMetaVariables, BatchRewritePlan, BatchRewriteRule,
};
use tensor_grep_rs::broken_pipe::error_chain_has_broken_pipe;
use tensor_grep_rs::crossover::{
    run_crossover_calibration, skip_signal_payload, write_crossover_config, NoCudaBuildError,
};
use tensor_grep_rs::exit_codes::exit_with_run_error;
#[cfg(feature = "cuda")]
use tensor_grep_rs::gpu_native::{
    benchmark_cuda_graph_search_paths, benchmark_pageable_transfer_throughput,
    benchmark_pinned_transfer_throughput, enumerate_cuda_devices, gpu_native_search_paths_multi,
    probe_device_allocation, GpuNativeSearchConfig, GpuNativeSearchStats, GpuPipelineStats,
};
use tensor_grep_rs::index::TrigramIndex;
use tensor_grep_rs::index_mmap::{mmap_index_path, LoadedIndex, MmapIndex};
use tensor_grep_rs::native_search::{
    defaulted_scope_fields, emit_index_binary_match_warning, native_json_text_fields,
    native_search_pattern_compiles, run_native_search, smart_case_pattern_is_case_insensitive,
    NativeSearchConfig, SearchStats,
};
use tensor_grep_rs::python_sidecar::{
    execute_python_passthrough_command, execute_python_passthrough_command_captured,
    execute_python_passthrough_command_with_stdin, execute_sidecar_command, SidecarError,
};
use tensor_grep_rs::rg_passthrough::{
    apply_last_ignore_wins, execute_ripgrep_pcre2_version, execute_ripgrep_search,
    execute_ripgrep_type_list, extract_search_tail_with_matches, implicit_search_paths,
    is_unbounded_implicit_search_walk_refusal, ripgrep_is_available, RipgrepSearchArgs,
};
use tensor_grep_rs::routing::{
    gpu_proof_fields, native_can_serve_plain_text, plain_text_native_cheap_checks_pass,
    plain_text_native_flag_token_is_allowed, route_search, BackendSelection, IndexRoutingState,
    PlainTextNativeRequest, RoutingDecision, SearchRoutingCalibration, SearchRoutingConfig,
};
use tensor_grep_rs::search_error::{
    self, exit_json_search_runtime_error_if_needed, exit_structured_search_error_if_needed,
    handle_search_runtime_error, refuse_structured_pcre2,
};

// Root identity also appears in positional help generated from this binary.
use crate::cli_app::*;
use crate::*;

pub(crate) fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    let digest = hasher.finalize();
    let mut output = String::with_capacity(digest.len() * 2);
    for byte in digest {
        output.push_str(&format!("{byte:02x}"));
    }
    output
}

pub(crate) fn canonical_manifest_bytes(manifest: &RewriteAuditManifest) -> anyhow::Result<Vec<u8>> {
    let mut value = serde_json::to_value(manifest)?;
    value
        .as_object_mut()
        .expect("rewrite audit manifest should serialize as an object")
        .remove("manifest_sha256");
    value
        .as_object_mut()
        .expect("rewrite audit manifest should serialize as an object")
        .remove("signature");
    Ok(serde_json::to_vec_pretty(&value)?)
}

pub(crate) fn previous_manifest_digest(path: &Path) -> anyhow::Result<String> {
    let previous_bytes = std::fs::read(path)
        .with_context(|| format!("failed to read previous audit manifest {}", path.display()))?;
    let previous_value: Option<serde_json::Value> = serde_json::from_slice(&previous_bytes).ok();
    Ok(previous_value
        .as_ref()
        .and_then(|value| value.get("manifest_sha256"))
        .and_then(|value| value.as_str())
        .map(ToOwned::to_owned)
        .unwrap_or_else(|| sha256_hex(&previous_bytes)))
}

pub(crate) fn collect_pre_apply_hashes(
    edits: &[tensor_grep_rs::backend_ast::RewriteEdit],
) -> anyhow::Result<BTreeMap<String, String>> {
    let mut hashes = BTreeMap::new();
    for file in edits
        .iter()
        .map(|edit| edit.file.clone())
        .collect::<std::collections::BTreeSet<_>>()
    {
        let bytes = std::fs::read(&file)
            .with_context(|| format!("failed to read {} for audit manifest", file.display()))?;
        hashes.insert(file.to_string_lossy().to_string(), sha256_hex(&bytes));
    }
    Ok(hashes)
}

/// Delegates to the single implementation in the lib crate
/// (`tensor_grep_rs::safe_write`). It lived HERE, in the binary crate, which is why
/// `backend_ast::direct_write_file` (lib crate) could not reach it and used a bare
/// `std::fs::write` -- the `--apply`-writes-through-a-symlink hole. Kept as a thin alias so the
/// existing audit-manifest / checkpoint / rollback call sites read unchanged.
pub(crate) fn write_bytes_refuse_symlink(path: &Path, bytes: &[u8]) -> anyhow::Result<()> {
    tensor_grep_rs::safe_write::write_bytes_refuse_symlink(path, bytes)
}

pub(crate) struct AuditManifestWriteInput<'a> {
    path: &'a Path,
    lang: &'a str,
    root_path: &'a str,
    edits: &'a [tensor_grep_rs::backend_ast::RewriteEdit],
    plan_total_edits: usize,
    checkpoint: Option<&'a CheckpointCreateSummary>,
    validation: Option<&'a ValidationSummary>,
    before_hashes: &'a BTreeMap<String, String>,
    signing_key_path: Option<&'a Path>,
}

pub(crate) fn write_audit_manifest_for_plan(
    input: AuditManifestWriteInput<'_>,
) -> anyhow::Result<AuditManifestSummary> {
    let AuditManifestWriteInput {
        path,
        lang,
        root_path,
        edits,
        plan_total_edits,
        checkpoint,
        validation,
        before_hashes,
        signing_key_path,
    } = input;
    let previous_manifest_sha256 = if path.exists() {
        Some(previous_manifest_digest(path)?)
    } else {
        None
    };

    let mut by_file: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for edit in edits {
        by_file
            .entry(edit.file.to_string_lossy().to_string())
            .or_default()
            .push(edit.id.clone());
    }

    let mut files = Vec::with_capacity(by_file.len());
    for (file, edit_ids) in by_file {
        let after_bytes = std::fs::read(&file)
            .with_context(|| format!("failed to read {} for audit manifest", file))?;
        let before_sha256 = before_hashes
            .get(&file)
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("missing pre-apply hash for {file}"))?;
        files.push(RewriteAuditManifestFile {
            path: file.clone(),
            edit_ids,
            before_sha256,
            after_sha256: sha256_hex(&after_bytes),
        });
    }

    let manifest = RewriteAuditManifest {
        version: JSON_OUTPUT_VERSION,
        kind: "rewrite-audit-manifest",
        // ISO-8601 UTC (audit C1/M5): matches the Python checkpoint `created_at` format and
        // keeps `audit-history` time-ordering working (`_parse_timestamp`). NOT a bare epoch.
        created_at: audit_manifest_timestamp_string(),
        lang: lang.to_string(),
        path: root_path.to_string(),
        plan_total_edits,
        applied_edit_ids: edits.iter().map(|edit| edit.id.clone()).collect(),
        previous_manifest_sha256,
        checkpoint: checkpoint.cloned(),
        validation: validation.cloned(),
        files,
        manifest_sha256: None,
        signature: None,
    };

    let mut manifest = manifest;
    let canonical_bytes = canonical_manifest_bytes(&manifest)?;
    manifest.manifest_sha256 = Some(sha256_hex(&canonical_bytes));
    if let Some(signing_key_path) = signing_key_path {
        let key_bytes = std::fs::read(signing_key_path).with_context(|| {
            format!(
                "failed to read audit signing key {}",
                signing_key_path.display()
            )
        })?;
        let mut mac = Hmac::<Sha256>::new_from_slice(&key_bytes)
            .map_err(|_| anyhow::anyhow!("invalid audit signing key"))?;
        mac.update(&canonical_bytes);
        let signature_bytes = mac.finalize().into_bytes();
        let mut signature_value = String::with_capacity(signature_bytes.len() * 2);
        for byte in signature_bytes {
            signature_value.push_str(&format!("{byte:02x}"));
        }
        manifest.signature = Some(AuditManifestSignature {
            kind: "hmac-sha256",
            key_path: signing_key_path.to_string_lossy().to_string(),
            value: signature_value,
        });
    }

    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("failed to create audit manifest dir {}", parent.display()))?;
    }
    write_bytes_refuse_symlink(path, &serde_json::to_vec_pretty(&manifest)?)
        .with_context(|| format!("failed to write audit manifest {}", path.display()))?;

    Ok(AuditManifestSummary {
        path: path.to_string_lossy().to_string(),
        file_count: manifest.files.len(),
        applied_edit_count: manifest.applied_edit_ids.len(),
        signed: manifest.signature.is_some(),
        signature_kind: manifest.signature.as_ref().map(|signature| signature.kind),
    })
}

pub(crate) fn verify_audit_manifest_payload(
    args: &AuditVerifyArgs,
) -> anyhow::Result<AuditManifestVerifyJson> {
    let p = &args.manifest_path;
    let manifest_path = p
        .canonicalize()
        .with_context(|| format!("failed to resolve audit manifest {}", p.display()))?;
    let manifest_bytes = std::fs::read(&manifest_path)
        .with_context(|| format!("failed to read audit manifest {}", manifest_path.display()))?;
    let manifest: RewriteAuditManifestRead = serde_json::from_slice(&manifest_bytes)
        .with_context(|| format!("failed to parse audit manifest {}", manifest_path.display()))?;

    let mut manifest_value: serde_json::Value = serde_json::from_slice(&manifest_bytes)
        .with_context(|| format!("failed to parse audit manifest {}", manifest_path.display()))?;
    let object = manifest_value
        .as_object_mut()
        .ok_or_else(|| anyhow::anyhow!("audit manifest must be a JSON object"))?;
    object.remove("manifest_sha256");
    object.remove("signature");
    let canonical_bytes = serde_json::to_vec_pretty(&manifest_value)?;

    let expected_digest = sha256_hex(&canonical_bytes);
    let digest_valid = manifest
        .manifest_sha256
        .as_ref()
        .map(|digest| digest == &expected_digest)
        .unwrap_or(false);

    let previous_manifest_path = args
        .previous_manifest
        .as_ref()
        .map(|path| path.canonicalize())
        .transpose()
        .with_context(|| {
            args.previous_manifest
                .as_ref()
                .map(|path| format!("failed to resolve previous manifest {}", path.display()))
                .unwrap_or_default()
        })?;
    let mut chain_valid = true;
    let mut errors = Vec::new();
    if let Some(previous_digest) = manifest.previous_manifest_sha256.as_ref() {
        if let Some(previous_path) = previous_manifest_path.as_ref() {
            let actual_previous_digest = previous_manifest_digest(previous_path)?;
            if previous_digest != &actual_previous_digest {
                chain_valid = false;
                errors.push(
                    "Previous manifest digest does not match previous_manifest_sha256.".to_string(),
                );
            }
        } else {
            chain_valid = false;
            errors.push(
                "Manifest chain digest is present but no previous manifest was provided."
                    .to_string(),
            );
        }
    }

    let signing_key_path = args
        .signing_key
        .as_ref()
        .map(|path| path.canonicalize())
        .transpose()
        .with_context(|| {
            args.signing_key
                .as_ref()
                .map(|path| format!("failed to resolve signing key {}", path.display()))
                .unwrap_or_default()
        })?;
    let mut signature_valid = true;
    let signature_kind = manifest
        .signature
        .as_ref()
        .map(|signature| signature.kind.clone());
    if let Some(signature) = manifest.signature.as_ref() {
        if signature.kind != "hmac-sha256" {
            signature_valid = false;
            errors.push(format!("Unsupported signature kind: {}", signature.kind));
        } else if let Some(key_path) = signing_key_path.as_deref() {
            let key_bytes = std::fs::read(key_path).with_context(|| {
                format!("failed to read audit signing key {}", key_path.display())
            })?;
            let mut mac = Hmac::<Sha256>::new_from_slice(&key_bytes)
                .map_err(|_| anyhow::anyhow!("invalid audit signing key"))?;
            mac.update(&canonical_bytes);
            let actual_signature = mac.finalize().into_bytes();
            let mut actual_signature_hex = String::with_capacity(actual_signature.len() * 2);
            for byte in actual_signature {
                actual_signature_hex.push_str(&format!("{byte:02x}"));
            }
            if actual_signature_hex != signature.value {
                signature_valid = false;
                errors.push(
                    "Manifest signature does not match the supplied signing key.".to_string(),
                );
            }
        } else {
            // Never derive the verification key from inside the manifest being verified:
            // a tampered manifest could point key_path at an attacker-controlled key and
            // forge a matching HMAC, defeating tamper-evidence for the default (no
            // --signing-key) invocation. Require an out-of-band key; treat
            // signature.key_path as informational only (audit S2).
            signature_valid = false;
            errors.push(
                "Manifest is hmac-sha256 signed but no --signing-key was provided; refusing to \
                 trust the key_path embedded in the manifest."
                    .to_string(),
            );
        }
    } else if signing_key_path.is_some() {
        signature_valid = false;
        errors.push("Signing key was provided but the manifest is unsigned.".to_string());
    }

    if !digest_valid {
        errors.insert(
            0,
            "Manifest digest does not match manifest_sha256.".to_string(),
        );
    }

    Ok(AuditManifestVerifyJson {
        version: JSON_OUTPUT_VERSION,
        routing_backend: "AuditManifest",
        routing_reason: "audit-manifest-verify",
        sidecar_used: false,
        manifest_path: tensor_grep_rs::clean_windows_extended_path(&manifest_path),
        signing_key_path: signing_key_path
            .as_deref()
            .map(tensor_grep_rs::clean_windows_extended_path),
        previous_manifest_path: previous_manifest_path
            .as_deref()
            .map(tensor_grep_rs::clean_windows_extended_path),
        kind: Some(manifest.kind),
        manifest_sha256: manifest.manifest_sha256,
        previous_manifest_sha256: manifest.previous_manifest_sha256,
        checks: AuditManifestVerifyChecks {
            digest_valid,
            chain_valid,
            signature_valid,
        },
        signature_kind,
        valid: digest_valid && chain_valid && signature_valid,
        errors,
    })
}

pub(crate) fn emit_validation_status(summary: &ValidationSummary) {
    if summary.validation_targets_truncated {
        eprintln!(
            "[validation] {} edited-file validation target(s) found; only the first --max-validation-targets were run. Rerun with a higher --max-validation-targets to validate the rest.",
            summary.validation_targets_total
        );
    }
    for result in &summary.commands {
        if result.success {
            eprintln!(
                "[validation:{}] passed{}",
                result.kind,
                result
                    .exit_code
                    .map(|code| format!(" (exit code {code})"))
                    .unwrap_or_default()
            );
        } else {
            eprintln!(
                "[validation:{}] failed{}",
                result.kind,
                result
                    .exit_code
                    .map(|code| format!(" (exit code {code})"))
                    .unwrap_or_else(|| " (no exit code)".to_string())
            );
        }
    }
}

pub(crate) fn handle_ast_run(mut args: RunArgs) -> anyhow::Result<()> {
    if args.update_all {
        if args.rewrite.is_none() {
            anyhow::bail!("tg run --update-all requires --rewrite");
        }
        args.apply = true;
    }
    validate_run_args(&args)?;
    if ast_run_requires_python_passthrough(&args) {
        let passthrough_args = ast_run_python_passthrough_args(&args)?;
        if args.stdin_flag {
            let mut stdin_bytes = Vec::new();
            io::stdin().read_to_end(&mut stdin_bytes)?;
            return handle_python_passthrough_with_stdin("run", passthrough_args, stdin_bytes);
        }
        return handle_python_passthrough("run", passthrough_args);
    }
    let backend = AstBackend::new();

    if let Some(config_path) = &args.batch_rewrite {
        let config = load_batch_rewrite_config(config_path)?;
        let path = run_batch_path(&args)?;
        if args.apply && !args.diff {
            return handle_ast_batch_rewrite_apply(&backend, &args, &config, path);
        }
        return handle_ast_batch_rewrite(&backend, &args, &config, path);
    }

    let (path, path_was_implicit) = run_search_path_with_origin(&args);

    if let Some(replacement) = &args.rewrite {
        if args.apply && !args.diff {
            return handle_ast_rewrite_apply(&backend, &args, replacement, path);
        }
        return handle_ast_rewrite(&backend, &args, replacement, path);
    }

    let pattern = run_pattern(&args)?;

    if args.json {
        let matches = backend.search(pattern, &args.lang, path)?;
        let match_count = matches.len();
        let mut source_contexts = BTreeMap::new();
        emit_json_search_results(
            RoutingDecision::ast(),
            pattern,
            path,
            &[],
            matches
                .iter()
                .map(|matched| ast_match_to_search_json(matched, &mut source_contexts))
                .collect::<anyhow::Result<Vec<_>>>()?,
            // Task 276: this route observed no walk of its own, so it cannot report a
            // count. `None` means "cannot report", NEVER "complete".
            None,
            path_was_implicit,
            None,
            Some((&args.lang, args.selector.as_deref())),
        )?;
        if match_count == 0 {
            ast_run_remediation::warn_windows_single_quote_ast_pattern(pattern);
            std::process::exit(1);
        }
        return Ok(());
    }

    let matches = backend.search_for_cli(pattern, &args.lang, path)?;
    let match_count: usize = matches
        .iter()
        .map(|file_matches| file_matches.matches.len())
        .sum();

    if args.verbose {
        emit_verbose_metadata(RoutingDecision::ast());
    }
    ast_run_remediation::emit_cli_run_advisories(
        &args.lang,
        args.selector.as_deref(),
        pattern,
        path,
        &matches,
        match_count,
    );
    if match_count == 0 {
        std::process::exit(1);
    }
    let stdout = io::stdout();
    let mut stdout = io::BufWriter::new(stdout.lock());
    if args.files_with_matches {
        for file_matches in matches {
            if !file_matches.matches.is_empty() {
                writeln!(stdout, "{}", file_matches.file.display())?;
            }
        }
        return Ok(());
    }

    for file_matches in matches {
        for matched in file_matches.matches {
            writeln!(
                stdout,
                "{}:{}:{}",
                file_matches.file.display(),
                matched.line,
                matched.matched_text
            )?;
        }
    }

    Ok(())
}

pub(crate) fn ast_run_python_passthrough_args(args: &RunArgs) -> anyhow::Result<Vec<String>> {
    let mut passthrough_args = vec!["--lang".to_string(), args.lang.clone()];
    let pattern = run_pattern(args)?.to_string();
    passthrough_args.push("--pattern".to_string());
    passthrough_args.push(pattern);

    if let Some(path) = run_optional_path(args) {
        passthrough_args.push(path.to_string());
    }
    if args.json {
        passthrough_args.push("--json".to_string());
    }
    if args.files_with_matches {
        passthrough_args.push("--files-with-matches".to_string());
    }
    if let Some(selector) = &args.selector {
        passthrough_args.push("--selector".to_string());
        passthrough_args.push(selector.clone());
    }
    if let Some(strictness) = &args.strictness {
        passthrough_args.push("--strictness".to_string());
        passthrough_args.push(strictness.clone());
    }
    if args.stdin_flag {
        passthrough_args.push("--stdin".to_string());
    }
    for glob in &args.globs {
        passthrough_args.push("--globs".to_string());
        passthrough_args.push(glob.clone());
    }
    Ok(passthrough_args)
}

pub(crate) fn run_optional_path(args: &RunArgs) -> Option<&str> {
    if args.pattern_option.is_some() {
        return args.positional.first().map(String::as_str);
    }
    args.positional.get(1).map(String::as_str)
}

pub(crate) fn handle_ast_rewrite(
    backend: &AstBackend,
    args: &RunArgs,
    replacement: &str,
    path: &str,
) -> anyhow::Result<()> {
    if args.verbose {
        emit_verbose_metadata(RoutingDecision::ast());
    }

    let pattern = run_pattern(args)?;
    let plan = backend.plan_rewrites(pattern, replacement, &args.lang, path)?;
    let plan = filter_rewrite_plan(&plan, args)?;

    if !plan.rejected_overlaps.is_empty() {
        eprintln!(
            "[rewrite] {} overlapping edit(s) rejected",
            plan.rejected_overlaps.len()
        );
    }

    if plan.edits.is_empty() {
        if args.diff && args.json {
            let payload = RewriteDiffJson {
                version: plan.version,
                routing_backend: plan.routing_backend,
                routing_reason: plan.routing_reason,
                sidecar_used: plan.sidecar_used,
                plan: &plan,
                diff: String::new(),
            };
            println!("{}", serde_json::to_string_pretty(&payload)?);
            return Ok(());
        }
        if args.json {
            println!("{}", serde_json::to_string_pretty(&plan)?);
            return Ok(());
        }
        eprintln!("[rewrite] no matches found, nothing to rewrite");
        return Ok(());
    }

    if !args.apply && !args.diff && args.json {
        println!("{}", serde_json::to_string_pretty(&plan)?);
        return Ok(());
    }
    if args.diff || !args.apply {
        let diff = plan.generate_diff()?;
        if args.json {
            let payload = RewriteDiffJson {
                version: plan.version,
                routing_backend: plan.routing_backend,
                routing_reason: plan.routing_reason,
                sidecar_used: plan.sidecar_used,
                plan: &plan,
                diff,
            };
            println!("{}", serde_json::to_string_pretty(&payload)?);
        } else {
            print!("{diff}");
        }
        return Ok(());
    }

    let files_written = AstBackend::apply_rewrites(&plan)?;
    eprintln!(
        "[rewrite] applied {} edit(s) across {} file(s)",
        plan.edits.len(),
        files_written
    );

    Ok(())
}

pub(crate) fn handle_ast_rewrite_apply(
    backend: &AstBackend,
    args: &RunArgs,
    replacement: &str,
    path: &str,
) -> anyhow::Result<()> {
    if args.verbose {
        emit_verbose_metadata(RoutingDecision::ast());
    }

    let pattern = run_pattern(args)?;
    let apply_mode = select_rewrite_apply_mode(args);
    let plan = match apply_mode {
        RewriteApplyMode::OneShotFastPath => {
            backend.plan_and_apply(pattern, replacement, &args.lang, path)?
        }
        RewriteApplyMode::PlanThenApply => {
            let plan = backend.plan_rewrites(pattern, replacement, &args.lang, path)?;
            filter_rewrite_plan(&plan, args)?
        }
    };

    if plan.edits.is_empty() && plan.rejected_overlaps.is_empty() {
        if args.json {
            let payload = ApplyVerifyJson {
                version: plan.version,
                routing_backend: plan.routing_backend,
                routing_reason: plan.routing_reason,
                sidecar_used: plan.sidecar_used,
                checkpoint: None,
                audit_manifest: None,
                plan: &plan,
                verification: None,
                validation: None,
                rollback: None,
            };
            println!("{}", serde_json::to_string_pretty(&payload)?);
        } else {
            eprintln!("[rewrite] no matches found, nothing to rewrite");
        }
        return Ok(());
    }

    let checkpoint = if args.checkpoint {
        let checkpoint = create_checkpoint(path)?;
        if !args.json {
            eprintln!(
                "[checkpoint] created {} ({}, files={})",
                checkpoint.checkpoint_id, checkpoint.mode, checkpoint.file_count
            );
        }
        Some(checkpoint)
    } else {
        None
    };

    let rollback_snapshots = if args.lint_cmd.is_some() || args.test_cmd.is_some() {
        Some(collect_validation_rollback_snapshots(&plan.edits)?)
    } else {
        None
    };

    let before_hashes = if args.audit_manifest.is_some() {
        Some(collect_pre_apply_hashes(&plan.edits)?)
    } else {
        None
    };

    if apply_mode == RewriteApplyMode::PlanThenApply {
        match rollback_snapshots.as_ref() {
            Some(snapshots) => AstBackend::apply_rewrites_with_targets(
                &plan,
                &retained_validation_targets(snapshots),
            )?,
            None => AstBackend::apply_rewrites(&plan)?,
        };
    }

    if !plan.rejected_overlaps.is_empty() && !args.json {
        eprintln!(
            "[rewrite] {} overlapping edit(s) rejected",
            plan.rejected_overlaps.len()
        );
    }

    if !args.json {
        eprintln!("[rewrite] applied {} edit(s)", plan.edits.len(),);
    }

    let verification = if args.verify {
        let v = plan.verify(backend)?;
        if !args.json {
            if v.mismatches.is_empty() {
                eprintln!("[verify] {}/{} edits verified", v.verified, v.total_edits);
            } else {
                eprintln!(
                    "[verify] {}/{} edits verified, {} mismatches",
                    v.verified,
                    v.total_edits,
                    v.mismatches.len()
                );
            }
        }
        Some(v)
    } else {
        None
    };

    let validation = run_post_apply_validation(args, path, &plan.edits);
    if !args.json {
        if let Some(summary) = &validation {
            emit_validation_status(summary);
        }
    }

    let rollback = if let Some(summary) = &validation {
        if !summary.success {
            let rollback = rollback_snapshots
                .as_ref()
                .map(restore_validation_rollback_snapshots);
            if !args.json {
                if let Some(rollback_summary) = &rollback {
                    emit_rollback_status(rollback_summary);
                }
            }
            rollback
        } else {
            None
        }
    } else {
        None
    };

    let audit_manifest = if let Some(audit_manifest_path) = &args.audit_manifest {
        Some(write_audit_manifest_for_plan(AuditManifestWriteInput {
            path: audit_manifest_path,
            lang: &args.lang,
            root_path: path,
            edits: &plan.edits,
            plan_total_edits: plan.total_edits,
            checkpoint: checkpoint.as_ref(),
            validation: validation.as_ref(),
            before_hashes: before_hashes
                .as_ref()
                .expect("pre-apply hashes should exist when audit manifest requested"),
            signing_key_path: args.audit_signing_key.as_deref(),
        })?)
    } else {
        None
    };

    if args.json {
        let payload = ApplyVerifyJson {
            version: plan.version,
            routing_backend: plan.routing_backend,
            routing_reason: plan.routing_reason,
            sidecar_used: plan.sidecar_used,
            checkpoint: checkpoint.as_ref(),
            audit_manifest: audit_manifest.as_ref(),
            plan: &plan,
            verification: verification.as_ref(),
            validation: validation.as_ref(),
            rollback: rollback.as_ref(),
        };
        println!("{}", serde_json::to_string_pretty(&payload)?);
    }

    if let Some(summary) = &validation {
        if !summary.success {
            anyhow::bail!("post-apply validation failed");
        }
    }

    Ok(())
}

pub(crate) fn handle_ast_batch_rewrite(
    backend: &AstBackend,
    args: &RunArgs,
    config: &BatchRewriteConfig,
    path: &str,
) -> anyhow::Result<()> {
    if args.verbose {
        emit_verbose_metadata(RoutingDecision::ast());
    }

    let plan = backend.plan_batch_rewrites(&config.rewrites, path)?;
    let plan = filter_batch_rewrite_plan(&plan, args)?;

    if !plan.rejected_overlaps.is_empty() {
        eprintln!(
            "[rewrite] {} overlapping edit(s) rejected",
            plan.rejected_overlaps.len()
        );
    }

    if plan.edits.is_empty() && plan.rejected_overlaps.is_empty() {
        if args.diff && args.json {
            let payload = BatchRewriteDiffJson {
                version: plan.version,
                routing_backend: plan.routing_backend,
                routing_reason: plan.routing_reason,
                sidecar_used: plan.sidecar_used,
                plan: &plan,
                diff: String::new(),
            };
            println!("{}", serde_json::to_string_pretty(&payload)?);
            return Ok(());
        }
        if args.json {
            println!("{}", serde_json::to_string_pretty(&plan)?);
            return Ok(());
        }
        eprintln!("[rewrite] no matches found, nothing to rewrite");
        return Ok(());
    }

    if args.diff {
        if plan.edits.is_empty() {
            eprintln!("[rewrite] no non-overlapping matches found, nothing to diff");
            return Ok(());
        }
        let diff = plan.generate_diff()?;
        if args.json {
            let payload = BatchRewriteDiffJson {
                version: plan.version,
                routing_backend: plan.routing_backend,
                routing_reason: plan.routing_reason,
                sidecar_used: plan.sidecar_used,
                plan: &plan,
                diff,
            };
            println!("{}", serde_json::to_string_pretty(&payload)?);
        } else {
            print!("{diff}");
        }
        return Ok(());
    }

    if !args.apply {
        println!("{}", serde_json::to_string_pretty(&plan)?);
        return Ok(());
    }

    let files_written = AstBackend::apply_batch_rewrites(&plan)?;
    if plan.edits.is_empty() {
        eprintln!("[rewrite] no non-overlapping edits applied");
    } else {
        eprintln!(
            "[rewrite] applied {} edit(s) across {} file(s)",
            plan.edits.len(),
            files_written
        );
    }

    Ok(())
}

pub(crate) fn handle_ast_batch_rewrite_apply(
    backend: &AstBackend,
    args: &RunArgs,
    config: &BatchRewriteConfig,
    path: &str,
) -> anyhow::Result<()> {
    if args.verbose {
        emit_verbose_metadata(RoutingDecision::ast());
    }

    let plan = backend.plan_batch_rewrites(&config.rewrites, path)?;
    let plan = filter_batch_rewrite_plan(&plan, args)?;

    if plan.edits.is_empty() && plan.rejected_overlaps.is_empty() {
        if args.json {
            let payload = BatchApplyVerifyJson {
                version: plan.version,
                routing_backend: plan.routing_backend,
                routing_reason: plan.routing_reason,
                sidecar_used: plan.sidecar_used,
                checkpoint: None,
                audit_manifest: None,
                plan: &plan,
                verification: None,
                validation: None,
                rollback: None,
            };
            println!("{}", serde_json::to_string_pretty(&payload)?);
        } else {
            eprintln!("[rewrite] no matches found, nothing to rewrite");
        }
        return Ok(());
    }

    let checkpoint = if args.checkpoint {
        let checkpoint = create_checkpoint(path)?;
        if !args.json {
            eprintln!(
                "[checkpoint] created {} ({}, files={})",
                checkpoint.checkpoint_id, checkpoint.mode, checkpoint.file_count
            );
        }
        Some(checkpoint)
    } else {
        None
    };

    let rollback_snapshots = if args.lint_cmd.is_some() || args.test_cmd.is_some() {
        Some(collect_validation_rollback_snapshots(&plan.edits)?)
    } else {
        None
    };

    let before_hashes = if args.audit_manifest.is_some() {
        Some(collect_pre_apply_hashes(&plan.edits)?)
    } else {
        None
    };

    match rollback_snapshots.as_ref() {
        Some(snapshots) => AstBackend::apply_batch_rewrites_with_targets(
            &plan,
            &retained_validation_targets(snapshots),
        )?,
        None => AstBackend::apply_batch_rewrites(&plan)?,
    };

    if !plan.rejected_overlaps.is_empty() && !args.json {
        eprintln!(
            "[rewrite] {} overlapping edit(s) rejected",
            plan.rejected_overlaps.len()
        );
    }

    if !args.json {
        if plan.edits.is_empty() {
            eprintln!("[rewrite] no non-overlapping edits applied");
        } else {
            eprintln!("[rewrite] applied {} edit(s)", plan.edits.len());
        }
    }

    let verification = if config.verify || args.verify {
        let result = plan.verify(backend)?;
        if !args.json {
            if result.mismatches.is_empty() {
                eprintln!(
                    "[verify] {}/{} edits verified",
                    result.verified, result.total_edits
                );
            } else {
                eprintln!(
                    "[verify] {}/{} edits verified, {} mismatches",
                    result.verified,
                    result.total_edits,
                    result.mismatches.len()
                );
            }
        }
        Some(result)
    } else {
        None
    };

    let validation = run_post_apply_validation(args, path, &plan.edits);
    if !args.json {
        if let Some(summary) = &validation {
            emit_validation_status(summary);
        }
    }

    let rollback = if let Some(summary) = &validation {
        if !summary.success {
            let rollback = rollback_snapshots
                .as_ref()
                .map(restore_validation_rollback_snapshots);
            if !args.json {
                if let Some(rollback_summary) = &rollback {
                    emit_rollback_status(rollback_summary);
                }
            }
            rollback
        } else {
            None
        }
    } else {
        None
    };

    let audit_manifest = if let Some(audit_manifest_path) = &args.audit_manifest {
        Some(write_audit_manifest_for_plan(AuditManifestWriteInput {
            path: audit_manifest_path,
            lang: &args.lang,
            root_path: path,
            edits: &plan.edits,
            plan_total_edits: plan.total_edits,
            checkpoint: checkpoint.as_ref(),
            validation: validation.as_ref(),
            before_hashes: before_hashes
                .as_ref()
                .expect("pre-apply hashes should exist when audit manifest requested"),
            signing_key_path: args.audit_signing_key.as_deref(),
        })?)
    } else {
        None
    };

    if args.json {
        let payload = BatchApplyVerifyJson {
            version: plan.version,
            routing_backend: plan.routing_backend,
            routing_reason: plan.routing_reason,
            sidecar_used: plan.sidecar_used,
            checkpoint: checkpoint.as_ref(),
            audit_manifest: audit_manifest.as_ref(),
            plan: &plan,
            verification: verification.as_ref(),
            validation: validation.as_ref(),
            rollback: rollback.as_ref(),
        };
        println!("{}", serde_json::to_string_pretty(&payload)?);
    }

    if let Some(summary) = &validation {
        if !summary.success {
            anyhow::bail!("post-apply validation failed");
        }
    }

    Ok(())
}
