mod ast_run_remediation;
#[allow(dead_code)]
mod cli_invocations;
mod main_env;
mod native_checkpoint_publication;
mod native_multi_pattern;
mod scan_routing;
mod search_citations_routing;
mod search_files;
mod search_flag_registry;
mod top_level_help;
mod validation_rollback;
mod version_info;
use clap::{CommandFactory, Parser};
use std::ffi::OsString;
use std::io;
pub(crate) use tensor_grep_rs::native_search::{native_json_text_fields, NativeSearchConfig};
use tensor_grep_rs::python_sidecar::execute_python_passthrough_command;
use tensor_grep_rs::rg_passthrough::{
    apply_last_ignore_wins, execute_ripgrep_pcre2_version, execute_ripgrep_type_list,
    ripgrep_is_available,
};
use tensor_grep_rs::search_error::handle_search_runtime_error;
mod cli_app;
pub(crate) use cli_app::*;

fn main() -> anyhow::Result<()> {
    let handle = std::thread::Builder::new()
        .name("tg-main".to_string())
        .stack_size(16 * 1024 * 1024)
        .spawn(main_inner)?;
    handle
        .join()
        .unwrap_or_else(|panic| std::panic::resume_unwind(panic))
}

fn main_inner() -> anyhow::Result<()> {
    let (raw_args, _managed_runtime) = main_env::collect_main_args()?;
    if let Some(args) = scan_routing::scan_args_with_separator(&raw_args) {
        return handle_python_passthrough("scan", args);
    }

    if raw_args.len() <= 1 {
        if let Some(exit_code) = try_public_help_passthrough(&raw_args)? {
            if exit_code != 0 {
                std::process::exit(exit_code.max(1));
            }
            return Ok(());
        }
        return print_native_top_level_help();
    }

    if top_level_unknown_command_refusal(&raw_args) {
        let first = &raw_args[1].to_string_lossy();
        let nearest = nearest_commands(first);
        let has_help = raw_args.iter().skip(2).any(|a| {
            let t = a.to_string_lossy();
            t == "--help" || t == "-h"
        });
        if has_help {
            if nearest.is_empty() {
                eprintln!("error: unknown command '{first}'");
            } else {
                eprintln!(
                    "error: unknown command '{first}' (did you mean {}?)",
                    nearest.join(", ")
                );
            }
        } else {
            let payload = serde_json::json!({
                "error": {
                    "code": "unknown_command",
                    "nearest": nearest,
                    "command": first,
                },
            });
            eprintln!("{payload}");
        }
        std::process::exit(2);
    }

    if let Some(verbose) = version_info::version_invocation_verbose(&raw_args) {
        version_info::print_version(verbose);
        return Ok(());
    }

    if is_top_level_pcre2_version_invocation(&raw_args)
        || is_search_pcre2_version_invocation(&raw_args)
    {
        require_ripgrep_or_exit(ripgrep_is_available(), "--pcre2-version");
        let exit_code = execute_ripgrep_pcre2_version()?;
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }
    if is_top_level_type_list_invocation(&raw_args) || is_search_type_list_invocation(&raw_args) {
        require_ripgrep_or_exit(ripgrep_is_available(), "--type-list");
        let exit_code = execute_ripgrep_type_list()?;
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    if let Some(exit_code) = try_public_help_passthrough(&raw_args)? {
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }
    if top_level_help::is_root_help_all_invocation(&raw_args) {
        return print_native_top_level_help_all();
    }
    if top_level_help::is_root_help_invocation(&raw_args) {
        return print_native_top_level_help();
    }

    // C3: plain `--json` combined with a render-only flag (e.g. -b/--passthru/--heading)
    // cannot be honored by the aggregate JSON path and must be rejected by the native
    // binary itself — NEVER delegated to the Python sidecar, which deadlocks/fork-bombs
    // the native<->python re-exec chain when the resolved Python is a stale tensor-grep
    // lacking the launcher guard. Fail fast and deterministically before spawning anything.
    let json_render_conflicts = json_aggregate_render_flag_conflicts(&raw_args);
    if !json_render_conflicts.is_empty() {
        let detail = format!(
            "flag(s) {} not supported with plain --json; use --format rg --json for ripgrep \
             JSON Lines that carry render metadata, or drop the flag(s).",
            json_render_conflicts.join(", ")
        );
        let payload = serde_json::json!({
            "version": JSON_OUTPUT_VERSION,
            "schema_version": JSON_OUTPUT_VERSION,
            "ok": false,
            "error": "unsupported_flag",
            "detail": detail,
        });
        println!("{payload}");
        std::process::exit(2);
    }

    let normalized_search_tokens = normalize_top_level_search_args(&raw_args).map(|search_args| {
        search_args
            .iter()
            .skip(2)
            .map(|arg| arg.to_string_lossy().to_string())
            .collect::<Vec<_>>()
    });

    if search_citations_routing::search_citations_flag_conflicts(normalized_search_tokens.clone()) {
        eprintln!("--citations cannot be combined with --ndjson or --format rg");
        std::process::exit(2);
    }

    if search_citations_routing::search_invocation_requests_citations(
        &raw_args,
        normalized_search_tokens,
    ) {
        if let Some(search_args) = search_format_python_passthrough_args(&raw_args) {
            let exit_code = match execute_python_passthrough_command("search", search_args) {
                Ok(exit_code) => exit_code,
                Err(err) => return exit_with_sidecar_error(err),
            };
            if exit_code != 0 {
                std::process::exit(exit_code.max(1));
            }
            return Ok(());
        }
    }

    if let Some(exit_code) = try_early_ripgrep_passthrough(&raw_args)? {
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    if let Some(exit_code) = try_default_search_frontdoor_passthrough(&raw_args)? {
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    if let Some(search_args) = search_format_python_passthrough_args(&raw_args) {
        let exit_code = match execute_python_passthrough_command("search", search_args) {
            Ok(exit_code) => exit_code,
            Err(err) => return exit_with_sidecar_error(err),
        };
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    if raw_args.get(1).and_then(|arg| arg.to_str()) != Some("search") {
        if let Some(search_args) = normalize_top_level_search_args(&raw_args) {
            return run_command_cli(parse_command_cli_with_ignore(&search_args));
        }
    }
    if let Some(exit_code) = try_early_positional_ripgrep_passthrough(&raw_args)? {
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }
    if should_use_positional_cli(&raw_args) {
        let mut cli = PositionalCli::parse_from(&raw_args);
        if let Ok(m) = PositionalCli::command().try_get_matches_from(&raw_args) {
            apply_last_ignore_wins(&m, &mut cli.no_ignore, &mut cli.ignore);
        }
        let (json, ndjson) = (cli.json, cli.ndjson);
        return run_positional_cli(cli)
            .or_else(|err| handle_search_runtime_error(json, ndjson, false, err));
    }
    run_command_cli(parse_command_cli_with_ignore(&raw_args))
}

fn parse_command_cli_with_ignore(args: &[OsString]) -> CommandCli {
    let mut cli = CommandCli::parse_from(args);
    if let (Commands::Search(ref mut s), Ok(m)) = (
        &mut cli.command,
        CommandCli::command().try_get_matches_from(args),
    ) {
        if let Some(sub) = m.subcommand_matches("search") {
            apply_last_ignore_wins(sub, &mut s.no_ignore, &mut s.ignore);
        }
    }
    cli
}

fn print_native_top_level_help() -> anyhow::Result<()> {
    use clap::CommandFactory;

    let cmd = CommandCli::command();
    top_level_help::print_help(&cmd, &mut io::stdout().lock())?;
    Ok(())
}

fn print_native_top_level_help_all() -> anyhow::Result<()> {
    use clap::CommandFactory;

    let cmd = CommandCli::command();
    top_level_help::print_help_all(&cmd, &mut io::stdout().lock())?;
    Ok(())
}
