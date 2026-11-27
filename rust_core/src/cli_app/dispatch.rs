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

pub(crate) fn run_command_cli(cli: CommandCli) -> anyhow::Result<()> {
    match cli.command {
        Commands::Search(args) => {
            let json = args.json;
            let ndjson = args.ndjson;
            let explicit_rg_format = args.format.as_deref() == Some("rg");
            handle_ripgrep_search(args)
                .or_else(|err| handle_search_runtime_error(json, ndjson, explicit_rg_format, err))
        }
        Commands::Calibrate(args) => handle_calibrate_command(args),
        Commands::Upgrade => handle_python_passthrough("upgrade", vec![]),
        Commands::Update => handle_python_passthrough("upgrade", vec![]),
        Commands::AuditVerify(args) => handle_audit_verify_command(args),
        Commands::Audit { args } => handle_python_passthrough("audit", args),
        Commands::Mcp => handle_python_passthrough("mcp", vec![]),
        Commands::Classify(args) => handle_classify_command(args),
        Commands::Run(args) => handle_ast_run(args).or_else(exit_with_run_error),
        Commands::Scan { args } => {
            let Some(config_path) = scan_routing::config_only_path(&args) else {
                return handle_python_passthrough("scan", args);
            };

            use tensor_grep_rs::backend_ast_workflow::{handle_ast_scan, SessionRequest};

            if let Some(exit_code) = try_resident_execution(SessionRequest::Scan {
                config_path: config_path.clone(),
            })? {
                std::process::exit(exit_code);
            }
            handle_ast_scan(config_path.as_deref())
        }
        Commands::Test { args } => {
            let Some(config_path) = scan_routing::config_only_path(&args) else {
                return handle_python_passthrough("test", args);
            };

            use tensor_grep_rs::backend_ast_workflow::{handle_ast_test, SessionRequest};

            if let Some(exit_code) = try_resident_execution(SessionRequest::Test {
                config_path: config_path.clone(),
            })? {
                std::process::exit(exit_code);
            }
            handle_ast_test(config_path.as_deref())
        }
        Commands::New { args } => {
            use tensor_grep_rs::backend_ast_workflow::handle_ast_new;
            if ast_new_requires_python_passthrough(&args) {
                return handle_python_passthrough("new", args);
            }
            handle_ast_new(args)
        }
        Commands::Worker { port, stop } => {
            use tensor_grep_rs::backend_ast_workflow::{handle_ast_worker_tcp, SessionRequest};
            if stop {
                match try_resident_execution(SessionRequest::Stop)? {
                    Some(0) => println!("Stopped resident worker."),
                    _ => println!("No resident worker found or failed to stop."),
                }
                Ok(())
            } else {
                handle_ast_worker_tcp(port)
            }
        }
        Commands::Lsp { args } => handle_python_passthrough("lsp", args),
        Commands::LspSetup { args } => handle_python_passthrough("lsp-setup", args),
        #[cfg(feature = "cuda")]
        Commands::GpuNativeStats(args) => handle_gpu_native_stats_command(args),
        #[cfg(feature = "cuda")]
        Commands::GpuTransferBench(args) => handle_gpu_transfer_benchmark_command(args),
        #[cfg(feature = "cuda")]
        Commands::GpuCudaGraphs(args) => handle_gpu_cuda_graph_benchmark_command(args),
        #[cfg(feature = "cuda")]
        Commands::GpuOomProbe(args) => handle_gpu_oom_probe_command(args),
        Commands::Map { args } => handle_python_passthrough("map", args),
        Commands::Orient { args } => handle_python_passthrough("orient", args),
        Commands::Codemap { args } => handle_python_passthrough("codemap", args),
        Commands::FileApi { args } => handle_python_passthrough("file-api", args),
        Commands::Freshness { args } => handle_python_passthrough("freshness", args),
        Commands::Coverage { args } => handle_python_passthrough("coverage", args),
        Commands::Inventory { args } => handle_python_passthrough("inventory", args),
        Commands::DocsCoverage { args } => handle_python_passthrough("docs-coverage", args),
        Commands::Session { args } => handle_python_passthrough("session", args),
        Commands::Doctor { args } => handle_python_passthrough("doctor", args),
        Commands::RepairLauncher { args } => handle_python_passthrough("repair-launcher", args),
        Commands::Checkpoint { args } => handle_python_passthrough("checkpoint", args),
        Commands::Dogfood { args } => handle_python_passthrough("dogfood", args),
        Commands::Defs { args } => handle_python_passthrough("defs", args),
        Commands::Refs { args } => handle_python_passthrough("refs", args),
        Commands::Source { args } => handle_python_passthrough("source", args),
        Commands::Impact { args } => handle_python_passthrough("impact", args),
        Commands::Callers { args } => handle_python_passthrough("callers", args),
        Commands::Imports { args } => handle_python_passthrough("imports", args),
        Commands::Importers { args } => handle_python_passthrough("importers", args),
        Commands::Find { args } => handle_python_passthrough("find", args),
        Commands::BlastRadius { args } => handle_python_passthrough("blast-radius", args),
        Commands::BlastRadiusRender { args } => {
            handle_python_passthrough("blast-radius-render", args)
        }
        Commands::BlastRadiusPlan { args } => handle_python_passthrough("blast-radius-plan", args),
        Commands::DiffImpact { args } => handle_python_passthrough("diff-impact", args),
        Commands::EditPlan { args } => handle_python_passthrough("edit-plan", args),
        Commands::Agent { args } => handle_python_passthrough("agent", args),
        Commands::ContextRender { args } => handle_python_passthrough("context-render", args),
        Commands::AstInfo { args } => handle_python_passthrough("ast-info", args),
        Commands::Rulesets { args } => handle_python_passthrough("rulesets", args),
        Commands::AuditHistory { args } => handle_python_passthrough("audit-history", args),
        Commands::AuditDiff { args } => handle_python_passthrough("audit-diff", args),
        Commands::ReviewBundle { args } => handle_python_passthrough("review-bundle", args),
        Commands::Evidence { args } => handle_python_passthrough("evidence", args),
        Commands::Ledger { args } => handle_python_passthrough("ledger", args),
        Commands::Devices { args } => handle_python_passthrough("devices", args),
        Commands::Context { args } => handle_python_passthrough("context", args),
        Commands::RouteTest { args } => handle_python_passthrough("route-test", args),
        Commands::InstallDense { args } => handle_python_passthrough("install-dense", args),
        Commands::Install { args } => handle_python_passthrough("install", args),
        Commands::Uninstall { args } => handle_python_passthrough("uninstall", args),
        Commands::Prepare { args } => handle_python_passthrough("prepare", args),
        Commands::RepairEnv { args } => handle_python_passthrough("repair-env", args),
        Commands::Sql { args } => handle_python_passthrough("sql", args),
        Commands::PythonPassthrough(args) => {
            let command = args[0].clone();
            let command_args = args[1..].to_vec();
            handle_python_passthrough(&command, command_args)
        }
    }
}

pub(crate) fn try_resident_execution(
    req: tensor_grep_rs::backend_ast_workflow::SessionRequest,
) -> anyhow::Result<Option<i32>> {
    use std::io::{BufRead, BufReader, Read, Write};
    use std::net::TcpStream;

    // Check if worker is requested or if we are stopping it
    let is_stop = matches!(
        req,
        tensor_grep_rs::backend_ast_workflow::SessionRequest::Stop
    );
    if !is_stop && std::env::var("TG_RESIDENT_AST").unwrap_or_default() != "1" {
        return Ok(None);
    }

    // Try to find the port
    let port_file = std::env::current_dir()?
        .join(".tg_cache")
        .join("ast")
        .join("worker_port.txt");
    if !port_file.exists() {
        return Ok(None);
    }

    let port_str = std::fs::read_to_string(&port_file)?;
    let port: u16 = port_str.trim().parse()?;

    // Connect
    let mut stream = match TcpStream::connect(format!("127.0.0.1:{}", port)) {
        Ok(s) => s,
        Err(_) => return Ok(None),
    };

    // Send request
    let req_json = serde_json::to_string(&req)?;
    stream.write_all(req_json.as_bytes())?;
    stream.flush()?;

    const MAX_WORKER_RESPONSE_HEADER_BYTES: usize = 64 * 1024;

    // Read response header with an explicit size bound before deserializing
    let mut reader = BufReader::new(stream);
    let mut line = String::new();
    let bytes_read =
        std::io::Read::take(&mut reader, (MAX_WORKER_RESPONSE_HEADER_BYTES + 1) as u64)
            .read_line(&mut line)?;
    if bytes_read > MAX_WORKER_RESPONSE_HEADER_BYTES || !line.ends_with('\n') {
        return Ok(None);
    }

    use tensor_grep_rs::backend_ast_workflow::SessionResponse;
    let resp: SessionResponse = match serde_json::from_str(&line) {
        Ok(r) => r,
        Err(_) => return Ok(None), // Protocol mismatch, fallback to cold
    };

    if !resp.success && resp.error.is_some() {
        if let Some(err) = resp.error {
            eprintln!("Worker error: {}", err);
        }
        return Ok(None); // Fallback to cold path for infrastructure/project errors
    }

    // Stream the rest of the output
    loop {
        let mut buf = [0; 4096];
        let n = reader.read(&mut buf)?;
        if n == 0 {
            break;
        }
        std::io::stdout().write_all(&buf[..n])?;
    }
    std::io::stdout().flush()?;

    if !resp.success {
        Ok(Some(1))
    } else {
        Ok(Some(0))
    }
}

pub(crate) fn ast_new_requires_python_passthrough(args: &[String]) -> bool {
    args.iter()
        .any(|arg| arg == "--config" || arg == "-c" || arg.starts_with("--config="))
}

pub(crate) fn handle_calibrate_command(args: CalibrateArgs) -> anyhow::Result<()> {
    let executable = env::current_exe().context("failed to resolve current tg executable")?;
    match run_crossover_calibration(&executable) {
        Ok(config) => {
            write_crossover_config(&config, None)?;
            // --json is a no-op on the success path: the config was already JSON before this
            // flag existed, so there is nothing additive to do here.
            println!("{}", serde_json::to_string_pretty(&config)?);
            Ok(())
        }
        Err(err) => {
            // Fail closed (exit 2) either way -- see the module-level comment above
            // `require_ripgrep_or_exit`. `--json` only changes WHAT is printed for the one
            // error kind a harness needs to tell apart from a genuine failure: this build
            // structurally cannot run GPU calibration (no CUDA compiled in). Any other error
            // (a bad device id on a cuda-enabled build, I/O, a failed benchmark subprocess,
            // ...) is a real failure, not a skip, so it must NOT be relabeled -- it keeps the
            // original human-readable eprintln even under --json (fail-closed honesty, no
            // mislabeling a genuine failure as "skipped").
            if args.json {
                if let Some(no_cuda) = err.downcast_ref::<NoCudaBuildError>() {
                    let payload = skip_signal_payload(no_cuda);
                    println!("{payload}");
                    std::process::exit(2);
                }
            }
            eprintln!("{err}");
            std::process::exit(2);
        }
    }
}

pub(crate) fn handle_audit_verify_command(args: AuditVerifyArgs) -> anyhow::Result<()> {
    let payload = verify_audit_manifest_payload(&args)?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&payload)?);
    } else {
        println!("Manifest: {}", payload.manifest_path);
        println!("valid={}", payload.valid);
        println!(
            "checks=digest:{} chain:{} signature:{}",
            payload.checks.digest_valid, payload.checks.chain_valid, payload.checks.signature_valid
        );
        for error in &payload.errors {
            println!("- {error}");
        }
    }
    if payload.valid {
        Ok(())
    } else {
        anyhow::bail!("audit manifest verification failed")
    }
}

pub(crate) fn run_positional_cli(cli: PositionalCli) -> anyhow::Result<()> {
    if cli.pattern.is_none() {
        use clap::CommandFactory;
        let mut cmd = PositionalCli::command();
        cmd.print_help()?;
        return Ok(());
    }

    let pattern = cli.pattern.clone().unwrap();
    let paths = implicit_search_paths(&cli.path, stdin_should_search_implicit_path());
    exit_json_search_input_error_if_needed(
        cli.json,
        cli.ndjson,
        std::slice::from_ref(&pattern),
        &paths,
    );
    let primary_path = paths.first().map(String::as_str).unwrap_or(".");

    refuse_structured_pcre2(cli.pcre2, cli.json, cli.ndjson, false);
    let rg_available = ripgrep_is_available();
    if cli.pcre2 {
        require_ripgrep_or_exit(rg_available, "--pcre2");
    }
    // P5·H2 (audit Finding 2 hoist): refuse the positional native count/files combos HERE, before
    // `count_search_corpus_bytes` walks the whole tree on CUDA builds. `PositionalCli` has no
    // `index` field, so there is no index-path refusal message to preserve -- a single,
    // unconditional choke point covering both the `--json`/`--ndjson` doors and the `--gpu-device-ids`
    // door (the in-arm calls in the NativeGpu/NativeCpu arms below were the original landing
    // spots; removed as redundant). The bare positional `--count-matches` (no json/ndjson/gpu)
    // routes to the Ripgrep arm and is NOT this gate's class (see
    // `positional_native_dropped_search_flags`).
    if let Some(dropped) = validate_positional_native_structured_refusals(&cli) {
        exit_native_structured_flag_dropped(&dropped, cli.json || cli.ndjson);
    }
    #[cfg_attr(not(feature = "cuda"), allow(unused_variables))]
    let structured_output = cli.json || cli.ndjson;
    let explicit_gpu = !cli.gpu_device_ids.is_empty();
    let auto_gpu_ids: [i32; 0] = [];
    if paths.len() != 1 && explicit_gpu {
        anyhow::bail!("GPU search currently supports exactly one path root");
    }

    #[cfg(feature = "cuda")]
    let (corpus_bytes, corpus_bytes_known) = match count_search_corpus_bytes(
        &paths.iter().map(PathBuf::from).collect::<Vec<_>>(),
        cli.no_ignore,
        &[],
    ) {
        Ok(bytes) => (bytes, true),
        Err(err) => {
            eprintln!("warning: corpus size probe failed: {err}");
            (0, false)
        }
    };
    #[cfg(not(feature = "cuda"))]
    let (corpus_bytes, corpus_bytes_known) = (0u64, false);

    #[cfg(feature = "cuda")]
    let gpu_auto_supported = paths.len() == 1
        && gpu_native_fallback_reason(&GpuSearchParams {
            patterns: std::slice::from_ref(&pattern),
            query: &pattern,
            path: primary_path,
            line_number: cli.line_number && !cli.no_line_number,
            ignore_case: cli.ignore_case,
            smart_case: false,
            fixed_strings: cli.fixed_strings,
            invert_match: cli.invert_match,
            count: cli.count,
            context: None,
            max_count: cli.max_count,
            word_regexp: cli.word_regexp,
            globs: Vec::new(),
            hidden: false,
            max_depth: None,
            text: false,
            no_ignore: cli.no_ignore,
            gpu_device_ids: &auto_gpu_ids,
            json: cli.json,
            ndjson: cli.ndjson,
            verbose: cli.verbose,
            replace: cli.replace.clone(),
            only_matching: cli.only_matching,
            max_filesize: cli.max_filesize.clone(),
            color: cli.color.clone(),
            no_ignore_vcs: cli.no_ignore_vcs,
            path_was_implicit: cli.path.is_empty(),
        })
        .is_none();

    #[cfg(not(feature = "cuda"))]
    let gpu_auto_supported = false;

    #[cfg(feature = "cuda")]
    let calibration = load_search_routing_calibration(Path::new(primary_path));
    #[cfg(not(feature = "cuda"))]
    let calibration: Option<SearchRoutingCalibration> = None;

    #[cfg(feature = "cuda")]
    let gpu_available = auto_gpu_available_for_routing();
    #[cfg(not(feature = "cuda"))]
    let gpu_available = false;

    let decision = route_search(
        &SearchRoutingConfig {
            explicit_index: false,
            explicit_gpu_device_ids: explicit_gpu,
            force_cpu: cli.force_cpu,
            ast_command: false,
            json: cli.json,
            ndjson: cli.ndjson,
            rg_available,
            corpus_bytes,
            corpus_bytes_known,
            gpu_auto_supported,
            prefer_rg_passthrough: false,
            pcre2: cli.pcre2,
            // The bare positional CLI (`tg PATTERN PATH` without the `search` subcommand) is a
            // separate front door with its own `PositionalCli` flag surface; this PR deliberately
            // does not change its routing. Explicitly false rather than derived, so the positional
            // path keeps today's behavior byte-for-byte.
            native_plain_text: false,
        },
        calibration.as_ref(),
        IndexRoutingState::default(),
        gpu_available,
    );

    match decision.selection {
        BackendSelection::NativeGpu => {
            let gpu_device_ids = if explicit_gpu {
                cli.gpu_device_ids.as_slice()
            } else {
                &auto_gpu_ids
            };
            let params = GpuSearchParams {
                patterns: std::slice::from_ref(&pattern),
                query: &pattern,
                path: primary_path,
                // N2 (task #131 F3): deliberately derive line_number the same way as the sibling
                // `native_search_config_for_positional` (its own `cli.line_number &&
                // !cli.no_line_number`), replacing the old hardcoded `line_number: true`. Since
                // `PositionalCli::line_number` is `#[arg(short='n')]` (default false), the positional
                // GPU path now defaults line numbers OFF, matching the native CPU fallback it
                // delegates to -- a user-visible alignment fix, locked by
                // `positional_gpu_path_defaults_line_number_off_aligned_with_native`.
                line_number: cli.line_number && !cli.no_line_number,
                ignore_case: cli.ignore_case,
                smart_case: false,
                fixed_strings: cli.fixed_strings,
                invert_match: cli.invert_match,
                count: cli.count,
                context: None,
                max_count: cli.max_count,
                word_regexp: cli.word_regexp,
                globs: Vec::new(),
                hidden: false,
                max_depth: None,
                text: false,
                no_ignore: cli.no_ignore,
                gpu_device_ids,
                json: cli.json,
                ndjson: cli.ndjson,
                verbose: cli.verbose,
                replace: cli.replace.clone(),
                only_matching: cli.only_matching,
                max_filesize: cli.max_filesize.clone(),
                color: cli.color.clone(),
                no_ignore_vcs: cli.no_ignore_vcs,
                path_was_implicit: cli.path.is_empty(),
            };

            #[cfg(feature = "cuda")]
            if decision.reason == RoutingDecision::native_gpu_auto().reason {
                let fallback_decision =
                    RoutingDecision::native_cpu_gpu_fallback(rg_available, structured_output);
                let rg_fallback = fallback_decision
                    .allow_rg_fallback
                    .then(|| positional_ripgrep_args(&cli, &pattern, &paths));
                return handle_auto_gpu_search(
                    params,
                    native_search_config_for_positional(&cli, &pattern, &paths, fallback_decision),
                    rg_fallback,
                );
            }

            handle_gpu_search(params)
        }
        BackendSelection::NativeCpu => {
            if decision.reason
                == RoutingDecision::native_cpu_gpu_fallback(rg_available, structured_output).reason
            {
                eprintln!(
                    "warning: CUDA is unavailable: no usable GPU devices were found; falling back to native CPU search; this CPU fallback output is not GPU acceleration proof"
                );
            }
            if cli.verbose {
                emit_verbose_metadata(decision);
            }

            let rg_fallback = decision
                .allow_rg_fallback
                .then(|| positional_ripgrep_args(&cli, &pattern, &paths));

            run_native_search_with_optional_rg_fallback(
                native_search_config_for_positional(&cli, &pattern, &paths, decision),
                rg_fallback,
            )
        }
        BackendSelection::Ripgrep => {
            if cli.verbose {
                emit_verbose_metadata(decision);
            }

            let exit_code =
                execute_ripgrep_search(&positional_ripgrep_args(&cli, &pattern, &paths))?;
            if exit_code != 0 {
                std::process::exit(exit_code.max(1));
            }
            Ok(())
        }
        _ => anyhow::bail!(
            "unsupported positional routing decision: {}",
            decision.reason
        ),
    }
}

pub(crate) fn should_use_positional_cli(raw_args: &[OsString]) -> bool {
    for arg in raw_args.iter().skip(1) {
        let token = arg.to_string_lossy();
        if token == "--help" || token == "-h" || token == "--version" || token == "-V" {
            return false;
        }
        if token.starts_with('-') {
            continue;
        }
        return !is_known_python_command(&token);
    }

    false
}

/// A90: unknown-command-shaped refusal on the NATIVE door. Mirrors the Python
/// `_top_level_command_refusal` contract exactly (parity-pinned):
/// - first arg is a RESERVED (roadmap, not-yet-registered) name AND any later token starts with
///   '-'  -> refusal; or
/// - first arg is unknown (not known, not reserved) AND any later token is `--help`/`-h`
///   (a nonexistent command has no help) -> refusal.
///
/// Everything else (bare patterns, pattern+path, unreserved pattern+flag) stays search.
pub(crate) fn top_level_unknown_command_refusal(raw_args: &[OsString]) -> bool {
    let Some(first) = raw_args.get(1).map(|a| a.to_string_lossy()) else {
        return false;
    };
    if first == "search" || is_known_python_command(&first) || first.starts_with('-') {
        return false;
    }
    let rest: Vec<String> = raw_args
        .iter()
        .skip(2)
        .map(|a| a.to_string_lossy().into_owned())
        .collect();
    if rest.is_empty() {
        return false;
    }
    let has_flag = rest.iter().any(|t| t.starts_with('-'));
    if !has_flag {
        return false;
    }
    let is_reserved = is_reserved_python_command(&first);
    let has_help = rest.iter().any(|t| t == "--help" || t == "-h");
    is_reserved || has_help
}

pub(crate) fn is_known_python_command(token: &str) -> bool {
    python_set_members("KNOWN_COMMANDS")
        .iter()
        .any(|name| name == token)
}

/// A90: is `token` in the RESERVED (roadmap, not-yet-registered) top-level command set?
/// Parsed from the RESERVED_TOP_LEVEL_COMMANDS block of commands.py — SCOPED to that block,
/// never a bare quoted-literal scan (the unscoped `include_str!` match made every quoted
/// literal line look "known", which would have made reserved names pass the NOT-known gate).
pub(crate) fn is_reserved_python_command(token: &str) -> bool {
    python_set_members("RESERVED_TOP_LEVEL_COMMANDS")
        .iter()
        .any(|name| name == token)
}

/// Extract the members of one top-level set literal block from commands.py by its variable
/// name (`KNOWN_COMMANDS = { ... }` / `RESERVED_TOP_LEVEL_COMMANDS = { ... }`). Bracedepth
/// aware, quote/escape aware (a `#` inside a string is NOT a comment; braces inside strings do
/// not count), and trailing-comma tolerant — `"agent",` is a member named `agent`. Returns the
/// parsed string literals in block order, or an empty Vec if the block is absent.
pub(crate) fn python_set_members(set_name: &str) -> Vec<String> {
    const RAW_PY: &str = include_str!("../../../src/tensor_grep/cli/commands.py");
    let needle_open = format!("{set_name} = {{");
    let mut members: Vec<String> = Vec::new();
    let mut depth: i32 = 0;
    let mut in_block = false;
    for line in RAW_PY.lines() {
        let t = line.trim();
        if !in_block {
            if t.starts_with(&needle_open) {
                in_block = true;
                depth += 1;
            }
            continue;
        }
        // Inside the target block: tokenize char-by-char with a small quote-aware scanner so a
        // '#' inside a string is data, not a comment, and braces inside strings are data too.
        let mut in_str = false;
        let mut in_str_esc = false;
        let mut current: String = String::new();
        let mut collected: Vec<String> = Vec::new();
        for c in t.chars() {
            if in_str {
                if in_str_esc {
                    // Decode the two escapes that can appear INSIDE a double-quoted Python
                    // string literal between the outer quotes: `\"` -> `"` and `\\` -> `\`.
                    // Any other escape is copied verbatim (commands.py set members are plain
                    // identifiers today; this only guards future edits — Python would decode
                    // e.g. `\n` to a newline, which cannot occur in a command name).
                    match c {
                        '"' => current.push('"'),
                        '\\' => current.push('\\'),
                        other => {
                            current.push('\\');
                            current.push(other);
                        }
                    }
                    in_str_esc = false;
                } else if c == '\\' {
                    in_str_esc = true;
                } else if c == '"' {
                    if depth >= 1 {
                        collected.push(current.clone());
                    }
                    current.clear();
                    in_str = false;
                } else {
                    current.push(c);
                }
                continue;
            }
            match c {
                '"' => {
                    in_str = true;
                    current.clear();
                }
                '#' => break, // comment to end of line
                '{' => depth += 1,
                '}' => {
                    depth -= 1;
                    if depth == 0 {
                        in_block = false;
                    }
                }
                _ => {}
            }
        }
        if !collected.is_empty() {
            members.extend(collected);
        }
        if !in_block {
            break;
        }
    }
    members
}

/// A90 nearest[]: normalized, max edit distance 3, excludes internal `__` names, RANKED by edit
/// distance (closest first, alphabetical tie-break) BEFORE the cap of 5, empty when nothing is
/// close. Mirrors `_nearest_commands` (A-04). Uses the same scoped member extractor as
/// membership so nearest and known can never disagree.
pub(crate) fn nearest_commands(token: &str) -> Vec<String> {
    let norm = token.to_lowercase();
    let mut ranked: Vec<(usize, String)> = python_set_members("KNOWN_COMMANDS")
        .into_iter()
        .filter(|name| !name.starts_with("__"))
        .map(|name| (levenshtein_distance(&norm, &name), name))
        .filter(|(distance, _)| *distance <= 3)
        .collect();
    ranked.sort(); // (distance, name): closest first, alphabetical tie-break
    ranked.into_iter().take(5).map(|(_, name)| name).collect()
}

/// Classic Wagner–Fischer Levenshtein distance over chars. Deterministic, bounded — the
/// command names and the input token are short, so the O(n*m) DP is trivial here.
pub(crate) fn levenshtein_distance(a: &str, b: &str) -> usize {
    let aa: Vec<char> = a.chars().collect();
    let bb: Vec<char> = b.chars().collect();
    if aa.is_empty() {
        return bb.len();
    }
    if bb.is_empty() {
        return aa.len();
    }
    let mut prev: Vec<usize> = (0..=bb.len()).collect();
    let mut curr: Vec<usize> = vec![0; bb.len() + 1];
    for (i, ca) in aa.iter().enumerate() {
        curr[0] = i + 1;
        for (j, cb) in bb.iter().enumerate() {
            let cost = if ca == cb { 0 } else { 1 };
            curr[j + 1] = (prev[j + 1] + 1).min(curr[j] + 1).min(prev[j] + cost);
        }
        std::mem::swap(&mut prev, &mut curr);
    }
    prev[bb.len()]
}
