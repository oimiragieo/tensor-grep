#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BackendSelection {
    NativeCpu,
    NativeGpu,
    TrigramIndex,
    AstBackend,
    Ripgrep,
    GpuSidecar,
}

impl BackendSelection {
    pub const fn routing_backend(self) -> &'static str {
        match self {
            Self::NativeCpu => "NativeCpuBackend",
            Self::NativeGpu => "NativeGpuBackend",
            Self::TrigramIndex => "TrigramIndex",
            Self::AstBackend => "AstBackend",
            Self::Ripgrep => "RipgrepBackend",
            Self::GpuSidecar => "GpuSidecar",
        }
    }

    pub const fn sidecar_used(self) -> bool {
        matches!(self, Self::GpuSidecar)
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct IndexRoutingState {
    pub exists: bool,
    pub is_stale: bool,
    pub pattern_compatible: bool,
}

impl IndexRoutingState {
    pub const fn should_route_to_index(self) -> bool {
        self.exists && !self.is_stale && self.pattern_compatible
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SearchRoutingConfig {
    pub explicit_index: bool,
    pub explicit_gpu_device_ids: bool,
    pub force_cpu: bool,
    pub ast_command: bool,
    pub json: bool,
    pub ndjson: bool,
    pub rg_available: bool,
    pub corpus_bytes: u64,
    pub corpus_bytes_known: bool,
    pub gpu_auto_supported: bool,
    pub prefer_rg_passthrough: bool,
    pub pcre2: bool,
    /// Result of `native_can_serve_plain_text` for this request: the request is a plain-text
    /// search the in-process native CPU engine is PROVEN to render exactly the way spawning
    /// `rg` would. When true the router skips the `rg` subprocess (see the `!config
    /// .native_plain_text` guard in `route_search`). Always compute it through
    /// `native_can_serve_plain_text` -- never hand-roll the condition at a call site.
    pub native_plain_text: bool,
}

/// The EXACT CLI flag spellings a plain-text search may carry and still stay eligible for the
/// in-process native CPU engine. This list is an ALLOW-list, deliberately tiny, and is the single
/// source of truth for both eligibility adapters in `main.rs`
/// (`frontdoor_search_is_native_plain_text_eligible`, which matches raw argv tokens, and
/// `search_args_allow_plain_text_native`, which exhaustively destructures the parsed `SearchArgs`).
/// Anything not listed here keeps today's behavior -- the `rg` subprocess -- which is why an
/// omission is always safe and an over-inclusion is not.
///
/// Why each admitted flag is admitted (all verified by reading `native_search.rs`):
/// - `-i`/`--ignore-case`: `build_matcher` feeds it to `grep_regex::RegexMatcherBuilder
///   ::case_insensitive`, the same knob ripgrep itself sets.
/// - `-F`/`--fixed-strings`: same builder, `.fixed_strings(...)`.
/// - `-w`/`--word-regexp`: same builder, `.word(...)`.
/// - `-n`/`--line-number`: pure render toggle; `append_standard_match_bytes` emits
///   `{line}:{text}` vs `{text}`, matching `rg -n` / `rg` on a single explicit file.
/// - `--verbose`: tg-only routing metadata on **stderr**, never forwarded to `rg` and never part
///   of stdout. Its content DOES change on an admitted request
///   (`RipgrepBackend`/`rg_passthrough` -> `NativeCpuBackend`/`plain-text-native`) -- that is the
///   flag's entire purpose: it reports which backend ran. Reporting the true backend is correct
///   behavior, not a parity break, and keeping `--verbose` admitted is what makes the new route
///   observable to tests and to benchmark/launcher attribution.
///
/// Deliberately EXCLUDED, with the reason (each keeps spawning `rg`, i.e. zero behavior change):
/// - `-N`/`--no-line-number`: listed in `SEARCH_PYTHON_PASSTHROUGH_FLAGS`, so it is claimed by the
///   Python sidecar before routing is reached; admitting it here would be dead policy at best.
/// - `-c`/`--count`: `run_native_search_files` calls `emit_count_output_from_matches` for EVERY
///   searched file, including zero-match files; `rg -c` prints only files with >0 matches.
/// - `-v`, `-o`, `-r`/`--replace`, `--column`, `--vimgrep`, `--passthru`, `-l`,
///   `--files-without-match`, `--count-matches`: alternate render shapes whose byte-level
///   agreement with `rg` is not established by any current test; unproven, therefore excluded.
/// - `-A`/`-B`/`-C` (context): the codebase already knows these need `rg`
///   (`prefer_rg_passthrough` in `handle_ripgrep_search` is exactly `search_has_context(...)`).
/// - `-m`/`--max-count`, `--max-depth`, `-t`/`--type`, `-g`/`--glob`, `--hidden`/`--no-hidden`,
///   `-L`/`--follow`, `-a`/`--text`, `--max-filesize`, the `--no-ignore*`/`--ignore*` family,
///   `--require-git`, `--sort`/`--sortr`/`--sort-files`: walk/selection semantics. The admitted
///   subset never walks a directory at all (see `single_path_is_regular_file` below), so these
///   have nothing to be correct about here -- excluded rather than reasoned about.
/// - `-S`/`--smart-case`, `--null`, `--null-data`, `-U`/`--multiline*`, `--path-separator`,
///   `--color`, `-P`/`--pcre2`, `--auto-hybrid-regex`, `--unicode`/`--pcre2-unicode`,
///   `--messages`, `--no-config`, `--format`, `--index`, `--cpu`, `--gpu-device-ids`: each either
///   selects a different engine, is already routed elsewhere, or has no native equivalent.
pub const PLAIN_TEXT_NATIVE_ALLOWED_FLAGS: &[&str] = &[
    "-i",
    "--ignore-case",
    "-F",
    "--fixed-strings",
    "-w",
    "--word-regexp",
    "-n",
    "--line-number",
    "--verbose",
];

/// The short-flag letters of `PLAIN_TEXT_NATIVE_ALLOWED_FLAGS`, used to accept clap's COMBINED
/// short clusters (`-in` == `-i -n`). Without this the raw-argv adapter and the parsed-`SearchArgs`
/// adapter disagree: clap expands `-in` into two admitted flags, so the parsed adapter admits it
/// while a naive exact-token match would not.
pub const PLAIN_TEXT_NATIVE_ALLOWED_SHORT_FLAGS: &[char] = &['i', 'n', 'F', 'w'];

/// Is a raw argv flag token inside the admitted policy? Accepts an exact spelling from
/// `PLAIN_TEXT_NATIVE_ALLOWED_FLAGS` or a combined short cluster whose every letter is in
/// `PLAIN_TEXT_NATIVE_ALLOWED_SHORT_FLAGS`. Fail-closed on everything else, including
/// `--flag=value` spellings (none of the admitted flags take a value, so such a token is a
/// different flag or a clap error either way).
pub fn plain_text_native_flag_token_is_allowed(token: &str) -> bool {
    if PLAIN_TEXT_NATIVE_ALLOWED_FLAGS.contains(&token) {
        return true;
    }
    let Some(cluster) = token.strip_prefix('-') else {
        return false;
    };
    if cluster.is_empty() || cluster.starts_with('-') {
        return false;
    }
    cluster.chars().all(|letter| PLAIN_TEXT_NATIVE_ALLOWED_SHORT_FLAGS.contains(&letter))
}

/// The facts `native_can_serve_plain_text` needs. Built by a thin adapter at each front door so
/// the POLICY lives in exactly one place (the predicate below) rather than being re-derived.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PlainTextNativeRequest {
    /// Number of search patterns after `-e`/positional resolution.
    pub pattern_count: usize,
    /// The single resolved pattern is the empty string. See refusal note (6).
    pub pattern_is_empty: bool,
    /// Number of PATH operands after resolution.
    pub path_count: usize,
    /// The caller supplied no PATH and one was defaulted in.
    pub path_was_implicit: bool,
    /// The single PATH operand resolves to an existing REGULAR FILE (not a directory).
    pub single_path_is_regular_file: bool,
    /// The single PATH operand passed the FULL-CONTENT probe: no NUL byte, no CR byte, valid
    /// UTF-8, and within the probe size cap. See refusal note (5) -- this one field carries three
    /// separate, independently-verified divergences.
    pub single_path_renders_identically: bool,
    /// `--json` or `--ndjson`.
    pub structured_output: bool,
    /// `--format` was supplied with any value.
    pub explicit_format: bool,
    /// stdout is attached to a terminal.
    pub stdout_is_terminal: bool,
    /// Every flag on the command line is in `PLAIN_TEXT_NATIVE_ALLOWED_FLAGS`.
    pub only_allowed_flags: bool,
}

/// Fail-closed predicate: may this plain-text search skip the `rg` subprocess and be answered by
/// the in-process native CPU engine with output `rg` would have produced byte-for-byte?
///
/// The danger this guards against is NOT slowness, it is a wrong or differently-formatted result,
/// so every clause below is a REFUSAL and the default answer is "no, keep spawning rg".
///
/// The four structural refusals, each traced against real behavior rather than assumed:
///
/// 1. `stdout_is_terminal` -- `execute_ripgrep_search` spawns `rg` with `Stdio::inherit()`, so on
///    a terminal `rg` auto-enables its heading/grouped layout AND color. The native engine's
///    `append_standard_match_bytes` always emits flat, uncolored `path:line:text`. Interactive
///    output would visibly change, so terminals keep the subprocess. (The measured ~16ms win is a
///    piped/agent-pipeline win anyway, which is exactly the non-terminal case.)
///
/// 2. `path_was_implicit` -- with no PATH operand `rg` walks `./` but prints paths WITHOUT the
///    `./` prefix, while the native engine is handed the literal `"."` default and prints
///    `./name`. Verified with ripgrep 15.1.0: `rg needle` prints `a.txt:...` and `rg needle .`
///    prints `.\a.txt:...`. An implicit path would therefore change every output line.
///
/// 3. `path_count == 1 && single_path_is_regular_file` -- the native engine must not WALK. Two
///    independent divergences appear the moment it does:
///    (a) `rg` silently skips binary files discovered by a directory walk (verified: a matching
///        `binary.bin` under a walked root produced no output at all from ripgrep 15.1.0), while
///        `run_native_search_files` calls `emit_binary_match_warning` for any matching binary
///        file it reaches -- an extra line `rg` never prints, and one no flag can suppress
///        because it is a property of the DATA, not the request; and
///    (b) both engines walk in parallel, so file emission ORDER is unstable across the two
///        walkers while the parity oracle compares text output as an ordered sequence.
///    Restricting to one explicit regular file removes the walk entirely: no ignore-file
///    semantics, no hidden-file rules, no ordering, no walk ceiling, and
///    `should_print_with_filename` is false on both sides so neither prints a path prefix.
///
/// 4. `pattern_count == 1` -- multi-pattern requests take
///    `collect_native_multi_pattern_matches`/`emit_multi_pattern_native_results`, a different
///    emitter whose plain-text agreement with `rg -e A -e B` is not established.
///
/// 5. `single_path_renders_identically` -- a FULL-CONTENT probe of the single file, refusing on
///    any of three divergences the emitter has and that this PR deliberately does NOT try to fix
///    (the emitter is shared with `--json`/`--ndjson`/`--cpu` and its behavior is a governed
///    contract; changing shared rendering inside a perf PR is the wrong blast radius):
///    (a) CRLF -- `search_file_streaming_plain_sequential` does
///        `line.trim_end_matches(['\n', '\r'])` (`native_search.rs`), and `build_searcher` only
///        installs a CRLF line terminator when `config.crlf`, which nothing on this path ever
///        sets (`--crlf` is a Python-passthrough flag). Measured: `rg` emits
///        `b"needle alpha\r\n"` where the native engine emits `b"needle alpha\n"`. On a Windows
///        or CRLF-normalized checkout this hits routine `tg search PATTERN file.py`, so ANY `\r`
///        byte refuses.
///    (b) Non-UTF-8 -- the plain sink is `grep_searcher::sinks::Lossy`, which substitutes U+FFFD;
///        `rg` writes the raw bytes. Measured: `rg` emits `b"cafe\xe9 needle here"` where the
///        native engine emits `b"cafe\xef\xbf\xbd needle here"` -- silent corruption. A PREFIX
///        probe cannot bound this (an invalid byte at 1 MB diverges just as hard), so the probe
///        validates the WHOLE file and refuses anything that is not valid UTF-8.
///    (c) NUL/binary -- `rg` prints `binary file matches (found "\0" byte around offset 6)` while
///        `emit_binary_match_warning` prints the same sentence with `"/0"`, a spelling pinned by
///        `tests/e2e/snapshots/.../native_binary_single_file.txt`,
///        `src/tensor_grep/backends/rust_backend.py`, `tests/unit/test_rust_core.py` and a
///        `rust_core/tests/test_routing.rs` assertion.
///    The probe is bounded by `PLAIN_TEXT_NATIVE_MAX_PROBE_BYTES` in `main.rs`: a file larger
///    than the cap is refused rather than read twice, so the probe can never cost more than it
///    saves. Reading the file is affordable precisely because the admitted subset is ONE file --
///    the engine is about to read it anyway.
///
/// 6. `pattern_is_empty` -- `tg search "" file.txt` is a legal rg invocation (every line matches),
///    but `run_native_search` rejects an empty pattern outright, and the `allow_rg_fallback` net
///    then prints `warning: native CPU search failed, falling back to ripgrep: ...` to stderr
///    before the correct stdout. Correct results, but a stderr line that did not exist before.
///
/// `structured_output` and `explicit_format` are refused because those requests already have
/// their own routes (`--json`/`--ndjson` land on `native_cpu_json`; `--format rg` must reach real
/// `rg`), and this predicate must never redirect them.
pub const fn native_can_serve_plain_text(request: &PlainTextNativeRequest) -> bool {
    request.only_allowed_flags
        && !request.structured_output
        && !request.explicit_format
        && !request.stdout_is_terminal
        && !request.path_was_implicit
        && request.pattern_count == 1
        && !request.pattern_is_empty
        && request.path_count == 1
        && request.single_path_is_regular_file
        && request.single_path_renders_identically
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SearchRoutingCalibration {
    pub threshold_bytes: u64,
    pub gpu_positive: bool,
}

impl SearchRoutingCalibration {
    pub const fn should_route_to_gpu(self, corpus_bytes: u64) -> bool {
        self.gpu_positive && corpus_bytes > self.threshold_bytes
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RoutingDecision {
    pub selection: BackendSelection,
    pub reason: &'static str,
    pub allow_rg_fallback: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GpuProofFields {
    pub gpu_evidence_status: Option<&'static str>,
    pub gpu_proof: Option<bool>,
    pub native_gpu_unavailable: Option<bool>,
    pub not_gpu_proof_reason: Option<String>,
}

pub fn gpu_proof_fields(
    requested_gpu_device_ids: &[i32],
    routing_backend: &str,
    sidecar_used: bool,
) -> GpuProofFields {
    if requested_gpu_device_ids.is_empty() {
        return GpuProofFields {
            gpu_evidence_status: None,
            gpu_proof: None,
            native_gpu_unavailable: None,
            not_gpu_proof_reason: None,
        };
    }

    let native_gpu_proof = routing_backend == "NativeGpuBackend" && !sidecar_used;
    if native_gpu_proof {
        return GpuProofFields {
            gpu_evidence_status: Some("native"),
            gpu_proof: Some(true),
            native_gpu_unavailable: Some(false),
            not_gpu_proof_reason: None,
        };
    }

    GpuProofFields {
        gpu_evidence_status: Some("unsupported"),
        gpu_proof: Some(false),
        native_gpu_unavailable: Some(true),
        not_gpu_proof_reason: Some(format!(
            "Requested GPU execution did not produce NativeGpuBackend with sidecar_used=false \
             (routing_backend={routing_backend}, sidecar_used={sidecar_used}); this is \
             CPU/sidecar compatibility output, not GPU acceleration proof."
        )),
    }
}

impl RoutingDecision {
    pub const fn routing_backend(self) -> &'static str {
        self.selection.routing_backend()
    }

    pub const fn sidecar_used(self) -> bool {
        self.selection.sidecar_used()
    }

    const fn new(
        selection: BackendSelection,
        reason: &'static str,
        allow_rg_fallback: bool,
    ) -> Self {
        Self {
            selection,
            reason,
            allow_rg_fallback,
        }
    }

    pub const fn native_cpu_force(rg_available: bool, structured_output: bool) -> Self {
        Self::new(
            BackendSelection::NativeCpu,
            "force_cpu",
            rg_available && !structured_output,
        )
    }

    pub const fn native_cpu_json(_rg_available: bool) -> Self {
        Self::new(BackendSelection::NativeCpu, "json_output", false)
    }

    /// The admitted plain-text native route (see `native_can_serve_plain_text`). Replaces the
    /// former `native_cpu_auto` / `"cpu-auto-size-threshold"` constructor, whose name and reason
    /// string described a GPU corpus-size decision that has nothing to do with this route --
    /// misleading in `--verbose` output and to benchmark/launcher attribution, which read
    /// `routing_reason`. That constructor was also the arm this route revived, and it was
    /// logically unreachable before, so nothing else produced the old string.
    ///
    /// `allow_rg_fallback` is true: `run_native_search_with_optional_rg_fallback` still hands the
    /// request to the real `rg` subprocess if the native engine errors.
    pub const fn native_cpu_plain_text() -> Self {
        Self::new(BackendSelection::NativeCpu, "plain-text-native", true)
    }

    pub const fn native_cpu_gpu_fallback(rg_available: bool, structured_output: bool) -> Self {
        Self::new(
            BackendSelection::NativeCpu,
            "gpu-auto-fallback-cpu",
            rg_available && !structured_output,
        )
    }

    pub const fn native_cpu_rg_unavailable() -> Self {
        Self::new(BackendSelection::NativeCpu, "rg_unavailable", false)
    }

    pub const fn explicit_index() -> Self {
        Self::new(BackendSelection::TrigramIndex, "index-accelerated", false)
    }

    pub const fn warm_index() -> Self {
        Self::new(BackendSelection::TrigramIndex, "index-accelerated", false)
    }

    pub const fn ast() -> Self {
        Self::new(BackendSelection::AstBackend, "ast-native", false)
    }

    pub const fn native_gpu_explicit() -> Self {
        Self::new(
            BackendSelection::NativeGpu,
            "gpu-device-ids-explicit-native",
            false,
        )
    }

    pub const fn native_gpu_auto() -> Self {
        Self::new(
            BackendSelection::NativeGpu,
            "gpu-auto-size-threshold",
            false,
        )
    }

    pub const fn ripgrep() -> Self {
        Self::new(BackendSelection::Ripgrep, "rg_passthrough", false)
    }

    pub const fn ripgrep_force() -> Self {
        Self::new(BackendSelection::Ripgrep, "force-cpu", false)
    }

    pub const fn ripgrep_pcre2() -> Self {
        Self::new(BackendSelection::Ripgrep, "pcre2-required", false)
    }

    pub const fn gpu_sidecar() -> Self {
        Self::new(
            BackendSelection::GpuSidecar,
            "gpu-device-ids-explicit",
            false,
        )
    }
}

pub const fn route_search(
    config: &SearchRoutingConfig,
    calibration_data: Option<&SearchRoutingCalibration>,
    index_state: IndexRoutingState,
    gpu_available: bool,
) -> RoutingDecision {
    let structured_output = config.json || config.ndjson;

    if config.pcre2 && config.rg_available {
        return RoutingDecision::ripgrep_pcre2();
    }

    if config.explicit_index {
        return RoutingDecision::explicit_index();
    }

    if config.explicit_gpu_device_ids {
        return RoutingDecision::native_gpu_explicit();
    }

    if config.force_cpu {
        if config.rg_available && (config.prefer_rg_passthrough || !structured_output) {
            return RoutingDecision::ripgrep_force();
        }
        return RoutingDecision::native_cpu_force(config.rg_available, structured_output);
    }

    if config.ast_command {
        return RoutingDecision::ast();
    }

    if index_state.should_route_to_index() {
        return RoutingDecision::warm_index();
    }

    let auto_gpu_candidate = config.gpu_auto_supported
        && config.corpus_bytes_known
        && matches!(
            calibration_data,
            Some(calibration) if calibration.should_route_to_gpu(config.corpus_bytes)
        );

    if auto_gpu_candidate {
        if gpu_available {
            return RoutingDecision::native_gpu_auto();
        }

        return RoutingDecision::native_cpu_gpu_fallback(config.rg_available, structured_output);
    }

    // `!config.native_plain_text` is the ONLY thing standing between a plain-text search and the
    // `rg` subprocess. When `native_can_serve_plain_text` admitted the request (a single explicit
    // regular-file path, a single pattern, no flag outside `PLAIN_TEXT_NATIVE_ALLOWED_FLAGS`, not
    // a terminal), fall through instead of spawning `rg` -- the native CPU engine renders the same
    // bytes in-process and saves the whole subprocess round trip.
    if config.rg_available
        && (config.prefer_rg_passthrough || !structured_output)
        && !config.native_plain_text
    {
        return RoutingDecision::ripgrep();
    }

    if structured_output {
        return RoutingDecision::native_cpu_json(config.rg_available);
    }

    if !config.rg_available {
        RoutingDecision::native_cpu_rg_unavailable()
    } else {
        // REACHABILITY NOTE: before the `native_plain_text` guard above, this arm was
        // LOGICALLY UNREACHABLE. Getting here requires `rg_available == true`, and the guard
        // above then required `structured_output == true` to fall through -- which the
        // `structured_output` branch immediately above would have caught. So the only way in was
        // a contradiction. It is now the live plain-text native route, and
        // `test_route_search_routes_admitted_plain_text_to_native_cpu` pins that.
        RoutingDecision::native_cpu_plain_text()
    }
}
