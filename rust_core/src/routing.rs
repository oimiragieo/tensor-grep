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
    cluster
        .chars()
        .all(|letter| PLAIN_TEXT_NATIVE_ALLOWED_SHORT_FLAGS.contains(&letter))
}

/// The facts `native_can_serve_plain_text` needs. Built by a thin adapter at each front door so
/// the POLICY lives in exactly one place (the predicate below) rather than being re-derived.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PlainTextNativeRequest {
    /// Number of search patterns after `-e`/positional resolution.
    pub pattern_count: usize,
    /// The single resolved pattern is the empty string. See refusal note (6).
    pub pattern_is_empty: bool,
    /// EXPENSIVE TIER (see the note on `single_path_renders_identically`). The pattern compiles
    /// with the native matcher AND cannot match a line terminator or NUL. See refusal note (7).
    pub pattern_is_native_renderable: bool,
    /// Number of PATH operands after resolution.
    pub path_count: usize,
    /// The caller supplied no PATH and one was defaulted in.
    pub path_was_implicit: bool,
    /// The single PATH operand resolves to an existing REGULAR FILE (not a directory).
    pub single_path_is_regular_file: bool,
    /// The single PATH operand is the `-` stdin sentinel. See refusal note (9).
    pub single_path_is_stdin_sentinel: bool,
    /// EXPENSIVE TIER. The single PATH operand passed the FULL-CONTENT probe: no NUL byte, no CR
    /// byte, valid UTF-8, and within the probe size cap. See refusal note (5) -- this one field
    /// carries three separate, independently-verified divergences.
    ///
    /// EXPENSIVE-TIER CONTRACT: this field and `pattern_is_native_renderable` are the only two
    /// that cost real work (a regex compile and a file read). Adapters MUST leave them `false`
    /// unless `plain_text_native_cheap_checks_pass` already returned true, so a request that a
    /// cheap clause would refuse anyway never pays for them. `false` therefore means EITHER
    /// "probed and refused" OR "not probed because a cheaper clause already refused" -- the
    /// predicate treats both identically, so the distinction is not observable.
    pub single_path_renders_identically: bool,
    /// `--json` or `--ndjson`.
    pub structured_output: bool,
    /// `--format` was supplied with any value.
    pub explicit_format: bool,
    /// stdout is attached to a terminal.
    pub stdout_is_terminal: bool,
    /// `$RIPGREP_CONFIG_PATH` is set to a non-empty value. See refusal note (8).
    pub rg_config_env_present: bool,
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
/// REFUSAL 1 -- `stdout_is_terminal` -- `execute_ripgrep_search` spawns `rg` with `Stdio::inherit()`, so on
///    a terminal `rg` auto-enables its heading/grouped layout AND color. The native engine's
///    `append_standard_match_bytes` always emits flat, uncolored `path:line:text`. Interactive
///    output would visibly change, so terminals keep the subprocess. (The measured ~16ms win is a
///    piped/agent-pipeline win anyway, which is exactly the non-terminal case.)
///
///    NOTE, because this clause reads narrower than it is: it models stdout only as
///    terminal-vs-not, i.e. as a passive byte destination. The sink's BEHAVIOR -- specifically a
///    consumer that closes the pipe early (`| head -1`, `| less`, an agent reading N lines) -- is
///    NOT a refusal here, because it cannot be known in advance and refusing every piped search
///    would delete the feature. It is handled instead where it actually lands, in
///    `run_native_search_with_optional_rg_fallback`'s `error_chain_has_broken_pipe` guard: this
///    route moves ownership of the write loop out of the `Stdio::inherit()` subprocess and into
///    tg's own process, and the two react oppositely to EPIPE. See that guard for the measurement.
///
/// REFUSAL 2 -- `path_was_implicit` -- with no PATH operand `rg` walks `./` but prints paths WITHOUT the
///    `./` prefix, while the native engine is handed the literal `"."` default and prints
///    `./name`. Verified with ripgrep 15.1.0: `rg needle` prints `a.txt:...` and `rg needle .`
///    prints `.\a.txt:...`. An implicit path would therefore change every output line.
///
/// REFUSAL 3 -- `path_count == 1 && single_path_is_regular_file` -- the native engine must not WALK. Two
///    independent divergences appear the moment it does:
///    (a) `rg` silently skips binary files discovered by a directory walk (verified: a matching
///    `binary.bin` under a walked root produced no output at all from ripgrep 15.1.0), while
///    `run_native_search_files` calls `emit_binary_match_warning` for any matching binary
///    file it reaches -- an extra line `rg` never prints, and one no flag can suppress
///    because it is a property of the DATA, not the request; and
///    (b) both engines walk in parallel, so file emission ORDER is unstable across the two
///    walkers while the parity oracle compares text output as an ordered sequence.
///    Restricting to one explicit regular file removes the walk entirely: no ignore-file
///    semantics, no hidden-file rules, no ordering, no walk ceiling, and
///    `should_print_with_filename` is false on both sides so neither prints a path prefix.
///
/// REFUSAL 4 -- `pattern_count == 1` -- multi-pattern requests take
///    `collect_native_multi_pattern_matches`/`emit_multi_pattern_native_results`, a different
///    emitter whose plain-text agreement with `rg -e A -e B` is not established.
///
/// REFUSAL 5 -- `single_path_renders_identically` -- a FULL-CONTENT probe of the single file, refusing on
///    any of three divergences the emitter USED TO have (this PR deliberately did NOT try to
///    fix them: the emitter is shared with `--json`/`--ndjson`/`--cpu` and its behavior is a
///    governed contract; changing shared rendering inside a perf PR is the wrong blast radius).
///    **UPDATE (task #266/#263): all three are now fixed in the shared emitter itself** (see
///    `native_search.rs`'s `strip_native_line_terminator`, `native_json_text_fields`, and
///    `emit_binary_match_warning`). This probe's clause still refuses all three cases -- that is
///    now a deliberate CONSERVATIVE MARGIN, not a live correctness gap, and relaxing it to
///    reclaim the `rg`-subprocess-skip perf win for CRLF/non-UTF-8/binary content is a
///    disclosed, not-yet-done follow-up (out of #266/#263's scope, which was the emitter only).
///    The divergences as originally measured, for history:
///    (a) CRLF -- `search_file_streaming_plain_sequential` did
///    `line.trim_end_matches(['\n', '\r'])` (`native_search.rs`), and `build_searcher` only
///    installs a CRLF line terminator when `config.crlf`, which nothing on this path ever
///    sets (`--crlf` is a Python-passthrough flag). Measured: `rg` emits
///    `b"needle alpha\r\n"` where the native engine emitted `b"needle alpha\n"`. On a Windows
///    or CRLF-normalized checkout this hit routine `tg search PATTERN file.py`, so ANY `\r`
///    byte refused.
///    (b) Non-UTF-8 -- the plain sink was `grep_searcher::sinks::Lossy`, which substituted
///    U+FFFD; `rg` writes the raw bytes. Measured: `rg` emits `b"cafe\xe9 needle here"` where
///    the native engine emitted `b"cafe\xef\xbf\xbd needle here"` -- silent corruption. A
///    PREFIX probe cannot bound this (an invalid byte at 1 MB diverges just as hard), so the
///    probe validates the WHOLE file and refuses anything that is not valid UTF-8.
///    (c) NUL/binary -- `rg` prints `binary file matches (found "\0" byte around offset 6)`
///    while `emit_binary_match_warning` used to print the same sentence with `"/0"`, a spelling
///    that was pinned by `tests/e2e/snapshots/.../native_binary_single_file.txt`,
///    `src/tensor_grep/backends/rust_backend.py`, `tests/unit/test_rust_core.py` and a
///    `rust_core/tests/test_routing.rs` assertion -- all corrected alongside the emitter fix.
///    The probe is bounded by `PLAIN_TEXT_NATIVE_MAX_PROBE_BYTES` in `main.rs`; see the measured
///    cost table on that constant for the cap's actual justification. Do NOT restate the cost
///    tradeoff here -- an earlier revision of this very comment asserted the probe "can never cost
///    more than it saves", which the measurements later disproved, leaving two load-bearing
///    comments in one PR claiming opposite facts about the same constant. One place owns it.
///
/// REFUSAL 6 -- `pattern_is_empty` -- `tg search "" file.txt` is a legal rg invocation (every line matches),
///    but `run_native_search` rejects an empty pattern outright, and the `allow_rg_fallback` net
///    then prints `warning: native CPU search failed, falling back to ripgrep: ...` to stderr
///    before the correct stdout. Correct results, but a stderr line that did not exist before.
///
/// REFUSAL 7 -- `pattern_is_native_renderable` -- the file probe validates the FILE; this clause covers the
///    divergences that are properties of the PATTERN, on an otherwise fully admitted request.
///    Two distinct failure shapes, both measured against ripgrep 15.1.0:
///    (a) EXIT-CODE REGRESSION 2 -> 1. `rg` refuses a pattern that can match a line terminator
///    (`needle\n`, `\n`, `[\n]`, `needle\r\n`) with rc=2 and "the literal `\n` is not allowed
///    in a regex ... consider --multiline", and a `\x00` pattern with rc=2 and "pattern
///    contains `\0` but it is impossible to match". The native matcher accepts both and
///    SUCCEEDS with zero matches -> rc=1 and empty stderr. `allow_rg_fallback` never fires
///    because nothing failed. An agent branching on 2-vs-1 would read "no matches" where the
///    truth is "bad query", and lose the diagnostic. Root cause: `build_matcher` never calls
///    `RegexMatcherBuilder::line_terminator`, which is what makes `rg` reject these -- fixing
///    that would change the matcher shared with `--json`/`--ndjson`/`--cpu`, so this route
///    refuses instead.
///    (b) EXTRA STDERR. A pattern that fails to COMPILE (`[`, `(`, `\Qx\E`,
///    `a{500}{500}{500}`) still exits 2, but only after `allow_rg_fallback` prints
///    `warning: native CPU search failed, falling back to ripgrep: ...` -- the same
///    extra-stderr class already treated as disqualifying for the empty pattern (note 6).
///    The adapter therefore refuses any pattern containing a line terminator or NUL (literal OR
///    escaped) and any pattern the native matcher cannot compile. Over-refusal is free here: a
///    refused pattern simply keeps today's `rg` behavior.
///
/// REFUSAL 8 -- `rg_config_env_present` -- every clause above validates the REQUEST, the PATTERN or the
///    FILE. None of them validates the process ENVIRONMENT that the `rg` subprocess inherits, and
///    `rg` has a first-class env config surface: `execute_ripgrep_search` never calls
///    `env_clear()`, and `rg_passthrough.rs` passes `--no-config` only when the user asks for it,
///    so today every plain-text search that reaches `rg` applies whatever `$RIPGREP_CONFIG_PATH`
///    points at. The in-process native engine reads no rg config at all.
///
///    Receipt on the shipped tg 1.98.3 + rg 15.1.0, stdout piped, a config file containing `-i`,
///    and `lf.txt` = `"needle alpha\nNEEDLE beta\nplain\n"`:
///    (a) `tg search NEEDLE lf.txt`                        -> `NEEDLE beta`
///    (b) `RIPGREP_CONFIG_PATH=cfg tg search NEEDLE lf.txt` -> `needle alpha` + `NEEDLE beta`
///    That argv is the canonical admitted shape (no flags, one pattern, one explicit LF/UTF-8
///    file, piped stdout), so without this clause the route would return SILENTLY WRONG RESULTS on
///    the most common request it admits. `-i` is only the cheapest demonstration: a config
///    `--vimgrep` changes the entire output format, `--color=always` injects escapes, and
///    `-w`/`-F`/`-U`/`-A`/`-B`/`-C`/`--sort`/`--hidden`/`--max-columns` are all equally invisible
///    to a predicate that only inspects argv. A DANGLING path is also observable: `rg` prints a
///    read-failure diagnostic the native route would omit. An EMPTY value is ignored by `rg`, so
///    the guard is "set AND non-empty", not merely "set".
///
///    No oracle can catch this class -- CI never sets the variable and the parity helpers copy
///    `os.environ` -- which is exactly why it is a predicate clause and not a test.
///
///    KNOWN GAP, deliberately a sentence rather than a clause: this models rg's CONFIG env but not
///    WHICH rg BINARY would have run. `resolve_ripgrep_binary` honors `TG_RG_PATH` (a documented
///    product env var), legacy `TG_RG_BINARY`, and a bundled `ripgrep-14.1.0`, so a user who
///    pinned a wrapped or patched rg silently stops getting it on this route. Not made a clause
///    for two reasons: rg's plain-text rendering is stable across versions, so the blast radius is
///    low; and `tests/helpers/rg_parity._command_env` SETS `TG_RG_PATH` on every parity run, so
///    refusing on it would make this PR's entire byte-parity oracle test nothing. Same axis-family
///    as note (9), one round later -- which is itself the point worth recording: this axis keeps
///    producing instances, so treat "no more found" as un-found rather than absent.
///
/// REFUSAL 9 -- `single_path_is_stdin_sentinel` -- THE AXIS THIS CLAUSE NAMES: everything `rg` resolves
///    SPECIALLY that is neither the request, the pattern, the file's bytes, the environment, nor
///    the consumer. `-` is the instance that bit. It is the one PATH operand ripgrep gives a
///    special meaning (read STDIN), and this predicate modelled it as an ordinary path:
///    `frontdoor_search_is_native_plain_text_eligible_with_terminal` deliberately excludes `-`
///    from flag parsing, so it lands in `positionals` and becomes the single PATH -- and
///    `Path::new("-").is_file()` is TRUE whenever a file literally named `-` exists in cwd.
///
///    Measured (file named `-` in cwd containing `needle in dashfile`, stdin piped
///    `needle from STDIN`), for `needle -`, `needle -- -` and `-e needle -` alike:
///    (a) rg 15.1.0        -> `needle from STDIN`
///    (b) shipped tg 1.98.3 -> `needle from STDIN`
///    (c) native emitter    -> `needle in dashfile`
///    rc=0, no stderr, plausible output -- from the WRONG DATA SOURCE. Confirmed POSIX-wide:
///    `grep needle -` with a file named `-` present also reads stdin.
///
///    The rest of the axis was probed on Linux (WSL) rather than reasoned about, because an
///    earlier round cleared `-` by reasoning and was wrong. `Path::is_file()` is `S_ISREG`, so
///    admission is exactly decidable:
///    (a) `/dev/null`, `/dev/zero`, FIFOs -- NOT `S_ISREG` -> already refused by clause 3.
///    (b) a symlink to a regular file -- admitted, and correctly so: both engines follow an
///    explicit symlink operand, size matches content, no divergence.
///    (c) `/proc/self/status`, `/proc/version` -- `S_ISREG` TRUE, `st_size` 0, and a read returns
///    1460 / 166 bytes of clean UTF-8. These were ADMITTED and are now refused by the
///    size-vs-bytes invariant in `plain_text_native_probe_file` (see there): a pseudo-file
///    whose reported size is a lie is exactly the shape where the probe cannot describe what
///    the search will later read, which is the memoization-staleness window becoming a
///    certainty by construction rather than a microsecond race.
///
///    `structured_output` and `explicit_format` are refused because those requests already have
///    their own routes (`--json`/`--ndjson` land on `native_cpu_json`; `--format rg` must reach real
///    `rg`), and this predicate must never redirect them.
pub const fn native_can_serve_plain_text(request: &PlainTextNativeRequest) -> bool {
    plain_text_native_cheap_checks_pass(request)
        && request.pattern_is_native_renderable
        && request.single_path_renders_identically
}

/// Every refusal that costs nothing to evaluate: flag policy, output mode, terminal state, and
/// operand cardinality/kind (one `stat`). Split out so an adapter can gate the EXPENSIVE tier --
/// a regex compile and a full file read -- behind it.
///
/// This is a real latency contract, not tidiness. The expensive fields are plain struct fields, so
/// a naive adapter computes them at construction time and every request pays, including the ones
/// this route never optimises: an interactive (terminal) single-file search, `--json`/`--ndjson`,
/// and `-A`/`-B`/`-C` all reach the clap-side adapter and would each burn a full file read on the
/// way to `rg` anyway. Callers MUST call this first and only then fill the expensive tier.
pub const fn plain_text_native_cheap_checks_pass(request: &PlainTextNativeRequest) -> bool {
    request.only_allowed_flags
        && !request.structured_output
        && !request.explicit_format
        && !request.stdout_is_terminal
        && !request.rg_config_env_present
        && !request.path_was_implicit
        && request.pattern_count == 1
        && !request.pattern_is_empty
        && request.path_count == 1
        && request.single_path_is_regular_file
        && !request.single_path_is_stdin_sentinel
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
