#![allow(unused_imports)]
use super::*;

pub(super) fn should_print_with_filename(
    config: &NativeSearchConfig,
    inputs: &SearchInputs,
) -> bool {
    config.with_filename || !inputs.roots.is_empty() || inputs.files.len() > 1
}

pub(super) fn split_search_inputs(config: &NativeSearchConfig) -> Result<SearchInputs> {
    let mut inputs = SearchInputs::default();

    for path in &config.paths {
        let selected_root = resolve_explicit_search_root(path)?;
        let selected_meta = selected_root.metadata().with_context(|| {
            crate::search_file_open::NativeSearchOpenFailure(path.to_path_buf())
        })?;
        if selected_meta.is_file() {
            inputs.files.push(selected_root);
        } else {
            crate::search_file_open::verify_directory_ancestry_nofollow(&selected_root)?;
            inputs.roots.push(selected_root);
        }
    }

    inputs.files.sort_unstable();
    inputs.files.dedup();
    Ok(inputs)
}

pub(super) fn should_use_parallel_walk_search(config: &NativeSearchConfig) -> bool {
    !config.quiet
        && config.before_context == 0
        && config.after_context == 0
        && !config.only_matching
        && config.max_count.is_none()
}

/// Bounded refuse-before-enumerate gate for the native-CPU engine's own root walk -- the
/// native-CPU sibling of `rg_passthrough::check_implicit_walk_ceiling` (audit #100). Audit #105
/// found #100's hoist covered only `execute_ripgrep_search`'s callers (the rg-passthrough
/// engine); `run_native_search` (reached via `--json`, `--force-cpu`, single-pattern
/// `--fixed-strings`, and rg-unavailable routing) had NO ceiling at all, so a bare implicit-path
/// search on a huge root still walked unbounded through this engine.
///
/// Only meaningful when `config.path_was_implicit` -- an explicit, deliberately-scoped PATH is
/// never refused regardless of size. Called as the FIRST statement of both
/// `search_walk_roots_parallel` and `collect_walked_files`: those are the only two functions
/// that ever hand a root to `WalkBuilder` in this module (`build_walk_builder`'s only two
/// callers), and `collect_walked_files` is also called directly by
/// `run_native_fixed_multi_pattern_search` (the AhoCorasick multi-pattern fast path) -- so
/// gating at this shared low-level pair, rather than in `run_native_search` alone, protects
/// every native-CPU walk entry point in one place instead of relying on each of main.rs's
/// several dispatch sites (positional CLI, `tg search`, GPU-CPU-fallback) to remember it.
pub(super) fn check_native_implicit_walk_ceiling(
    config: &NativeSearchConfig,
    roots: &[PathBuf],
) -> Option<String> {
    if !config.path_was_implicit {
        return None;
    }
    let probe_roots: Vec<String> = roots
        .iter()
        .map(|root| root.to_string_lossy().into_owned())
        .collect();
    if crate::rg_passthrough::implicit_search_walk_exceeds_ceiling(
        &probe_roots,
        config.max_depth,
        config.no_ignore,
        config.hidden,
        crate::rg_passthrough::IMPLICIT_SEARCH_WALK_FILE_CEILING,
    ) {
        Some(
            crate::rg_passthrough::format_unbounded_implicit_search_walk_error(
                crate::rg_passthrough::IMPLICIT_SEARCH_WALK_FILE_CEILING,
            ),
        )
    } else {
        None
    }
}

pub(super) fn search_walk_roots_parallel(
    config: &NativeSearchConfig,
    roots: &[PathBuf],
) -> Result<SearchStats> {
    if let Some(refusal) = check_native_implicit_walk_ceiling(config, roots) {
        return Err(anyhow!(refusal));
    }
    let shared_stats = Arc::new(Mutex::new(SearchStats::default()));
    let shared_error = Arc::new(Mutex::new(None));
    let should_quit = Arc::new(AtomicBool::new(false));
    let config = Arc::new(config.clone());
    let walker = build_walk_builder(config.as_ref(), roots)?;

    walker.build_parallel().run(|| {
        let config = Arc::clone(&config);
        let shared_stats = Arc::clone(&shared_stats);
        let shared_error = Arc::clone(&shared_error);
        let should_quit = Arc::clone(&should_quit);
        let mut worker = ParallelWalkWorker::new(config, shared_stats);
        Box::new(move |entry| {
            if should_quit.load(Ordering::Relaxed) {
                return WalkState::Quit;
            }

            let entry = match entry {
                Ok(entry) => entry,
                Err(err) => {
                    // A per-entry walk error (e.g. a permission-denied subdirectory) used to
                    // abort the ENTIRE parallel walk here, where real `rg` logs one stderr line
                    // and keeps searching every other file (task #263's third defect --
                    // confirmed by direct comparison against `rg.exe` 15.1.0: `rg needle .`
                    // over a tree with one access-denied subdirectory still reports every match
                    // outside it, plus one `rg: <path>: Access is denied.` stderr line; the old
                    // abort-on-first-error behavior would have silently returned ZERO matches
                    // for the exact same tree depending on walk order). Logging and continuing
                    // matches `rg`'s own `ignore`-crate-backed walker -- the same crate this
                    // engine uses -- so this is real parity, not a new guess at rg's behavior.
                    // Task #276 slice B: count it as well as printing it. The stderr line is
                    // rg-parity (#263) and stays; the COUNT is what lets the --json envelope stop
                    // claiming a complete result on an incomplete walk. Flows to the aggregate
                    // via the existing local->shared merge, so no new lock on the hot path.
                    // `worker` is still the `Result<ParallelWalkWorker>` from
                    // `ParallelWalkWorker::new` here -- the shadowing `let worker = match
                    // worker.as_mut()` happens BELOW this arm, so a bare `worker.local_stats`
                    // is field access on a Result and does not compile. (It did not compile;
                    // an audit caught it because every Rust CI leg on the commit that
                    // introduced it was CANCELLED, never green.)
                    //
                    // The `Err` arm is deliberately silent: a thread whose matcher failed to
                    // build owns no worker and therefore no stats channel. It quits on its
                    // first real entry and the whole search returns Err, so there is no
                    // "complete" envelope for a lost count to corrupt.
                    // ALLOW-LIST the count (#282: fail-closed guidance enumerates the SAFE
                    // cases, never the unsafe ones). `ignore::Error` is NOT only "path was
                    // unreadable" -- the parallel walker also reports a failure to PARSE an
                    // ancestor/global gitignore (`Error::Glob` / `WithLineNumber`, surfaced via
                    // `add_parents`) and `UnrecognizedFileType` / `InvalidDefinition`. Counting
                    // those would label a COMPLETE walk `result_incomplete` with a
                    // budget-non-remediable cause: one malformed glob in a user's
                    // ~/.config/git/ignore would make EVERY `tg search --json` on that machine
                    // claim it could not finish. A false "incomplete" is worse than the silence
                    // #276 set out to fix, because it teaches an agent to distrust a true answer.
                    //
                    // `is_io()` is the crate's own predicate for "exclusively an I/O error": it
                    // recurses through WithPath/WithDepth/WithLineNumber, treats a single-element
                    // Partial as its inner error, and returns FALSE for Glob, UnrecognizedFileType,
                    // InvalidDefinition and Loop. Loop cannot fire here anyway -- `build_walk_builder`
                    // never calls `follow_links`.
                    //
                    // Non-I/O errors still PRINT (rg-parity, #263); they just do not claim the
                    // answer is incomplete.
                    if err.is_io() {
                        if let Ok(worker) = worker.as_mut() {
                            worker.local_stats.walk_errors += 1;
                        }
                    }
                    eprintln!("tg: {err}");
                    return WalkState::Continue;
                }
            };

            if !entry
                .file_type()
                .map(|kind| kind.is_file())
                .unwrap_or(false)
            {
                return WalkState::Continue;
            }

            let worker = match worker.as_mut() {
                Ok(worker) => worker,
                Err(err) => {
                    should_quit.store(true, Ordering::Relaxed);
                    if let Ok(mut guard) = shared_error.lock() {
                        if guard.is_none() {
                            *guard = Some(anyhow!(err.to_string()));
                        }
                    }
                    return WalkState::Quit;
                }
            };

            if let Err(err) = worker.search_path(entry.path()) {
                // A closed output pipe (`tg ... | head -1`) is a real reason to stop the whole
                // walk -- every remaining file would just hit the same error -- so that case
                // still aborts. Any OTHER per-file search error (e.g. the file itself became
                // unreadable between being listed and opened) now logs and continues, matching
                // `rg`'s own behavior for exactly this case (task #263's third defect; verified
                // against `rg.exe` 15.1.0 the same way as the entry-error case above).
                // Task 321: an OUTPUT-write failure aborts on exactly the same reasoning as the
                // broken pipe beside it -- every remaining file would hit the same dead target,
                // so continuing burns the whole walk to produce nothing. `ENOSPC`/`EIO` were
                // previously NOT caught here (only `BrokenPipe` was), so they fell through to the
                // `walk_errors` counter below and the envelope reported
                // `incomplete_reason_class: "unreadable_path"` for a file it had read perfectly.
                // Wrong in the direction that matters: it sends the reader to check permissions on
                // an input that was fine, and hides that the OUTPUT is the thing that failed.
                if search_path_error_is_broken_pipe(&err)
                    || worker.output_write_failed
                    || is_native_search_open_failure(&err)
                {
                    should_quit.store(true, Ordering::Relaxed);
                    if let Ok(mut guard) = shared_error.lock() {
                        if guard.is_none() {
                            *guard = Some(err);
                        }
                    }
                    return WalkState::Quit;
                }
                // Task #276 slice B: same reasoning as the entry-error arm above -- a file we
                // could not read is a hole in the answer, and the envelope has to be able to say
                // so. Counted here, emitted by slice B2. Reached ONLY for genuine INPUT failures
                // now -- the output-write arm above returns before this.
                worker.local_stats.walk_errors += 1;
                eprintln!("tg: {err}");
                return WalkState::Continue;
            }

            WalkState::Continue
        })
    });

    if let Some(err) = shared_error
        .lock()
        .map_err(|_| anyhow!("failed to inspect native search worker errors"))?
        .take()
    {
        return Err(err);
    }

    let mut stats = std::mem::take(
        &mut *shared_stats
            .lock()
            .map_err(|_| anyhow!("failed to collect native search worker stats"))?,
    );
    sort_search_matches(&mut stats.matches);
    Ok(stats)
}

pub(super) fn merge_search_stats(target: &mut SearchStats, source: SearchStats) {
    target.searched_files += source.searched_files;
    target.matched_files += source.matched_files;
    target.total_matches += source.total_matches;
    target.skipped_binary_files += source.skipped_binary_files;
    target.binary_match_files += source.binary_match_files;
    target.binary_match_paths.extend(source.binary_match_paths);
    // Task #276 slice A. Missing this line is the whole failure mode: every worker would count
    // its own walk errors and the aggregate would report zero, so the envelope would claim a
    // complete result on an incomplete walk -- the exact defect #276 exists to fix.
    target.walk_errors += source.walk_errors;
    target.matches.extend(source.matches);
}

pub(super) fn sort_search_matches(matches: &mut [NativeSearchMatch]) {
    matches.sort_by(|left, right| {
        left.path
            .cmp(&right.path)
            .then_with(|| left.line_number.cmp(&right.line_number))
            .then_with(|| left.raw.cmp(&right.raw))
    });
}

pub(super) fn search_file_streaming_standard(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
    flush_first_match_immediately: bool,
) -> Result<FileSearchResult> {
    search_file_streaming_standard_sequential(config, matcher, path, flush_first_match_immediately)
}

pub(super) fn search_file_streaming_standard_sequential(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
    flush_first_match_immediately: bool,
) -> Result<FileSearchResult> {
    if can_stream_plain_matches(config) {
        return search_file_streaming_plain_sequential(
            config,
            matcher,
            path,
            flush_first_match_immediately,
        );
    }

    let writer = AtomicLineWriter::new(config.output_target.clone());
    let mut builder = StandardBuilder::new();
    builder.path(config.with_filename);
    builder.only_matching(config.only_matching);

    let mut printer = builder.build_no_color(writer);
    let mut searcher = build_searcher(config, config.line_number);
    let (matches, binary_detected, binary_byte_offset) = {
        let sink = CollectingSink::new(printer.sink_with_path(matcher, path), path.to_path_buf());
        let mut sink = BinaryAwareSink::new(sink);
        search_opened_path(
            &mut searcher,
            matcher,
            path,
            &mut sink,
            "native standard output search failed",
        )?;
        let binary_detected = sink.saw_binary();
        let binary_byte_offset = sink.binary_byte_offset();
        let matches = sink.into_inner().into_matches();
        (matches, binary_detected, binary_byte_offset)
    };
    printer.get_mut().get_mut().finish()?;

    let binary_match_detected = binary_file_matches_pattern(matcher, path, binary_detected)?;
    let (matches, match_count) = if binary_detected {
        (Vec::new(), 0)
    } else {
        let match_count = matches.len();
        (matches, match_count)
    };

    Ok(FileSearchResult {
        match_count,
        matches,
        binary_detected,
        binary_match_detected,
        binary_byte_offset,
    })
}

pub(super) fn search_file_streaming_plain_sequential(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
    flush_first_match_immediately: bool,
) -> Result<FileSearchResult> {
    let streaming_output_flush_bytes = if cfg!(debug_assertions) {
        STREAMING_OUTPUT_FLUSH_BYTES_DEBUG
    } else {
        STREAMING_OUTPUT_FLUSH_BYTES
    };
    let retain_matches =
        matches!(config.output_target, NativeOutputTarget::Buffer(_)) || cfg!(debug_assertions);
    let mut matches = Vec::new();
    let mut match_count = 0usize;
    let mut pending_output = Vec::with_capacity(streaming_output_flush_bytes);
    let mut emitted_first_chunk = false;
    let path_buf = path.to_path_buf();
    let path_display = path.display().to_string();
    let mut searcher = build_searcher(config, true);
    let mut sink = BinaryAwareSink::new(Bytes(|line_number, line| {
        let trimmed_line = strip_native_line_terminator(line);
        let rendered_bytes = render_matched_line_bytes(config, trimmed_line)
            .map_err(sink_io_error)?
            .into_owned();
        append_standard_match_bytes(
            &mut pending_output,
            config,
            &path_display,
            line_number,
            &rendered_bytes,
        )
        .map_err(sink_io_error)?;
        if flush_first_match_immediately && !emitted_first_chunk {
            config
                .output_target
                .write_all(&pending_output)
                .map_err(sink_io_error)?;
            pending_output.clear();
            emitted_first_chunk = true;
        } else if pending_output.len() >= streaming_output_flush_bytes {
            config
                .output_target
                .write_all(&pending_output)
                .map_err(sink_io_error)?;
            pending_output.clear();
        }
        match_count = match_count.saturating_add(1);
        if retain_matches {
            matches.push(NativeSearchMatch {
                path: path_buf.clone(),
                line_number: Some(line_number),
                raw: rendered_bytes,
            });
        }
        Ok(true)
    }));
    search_opened_path(
        &mut searcher,
        matcher,
        path,
        &mut sink,
        "native standard output search failed",
    )?;

    let binary_detected = sink.saw_binary();
    let binary_match_detected = binary_file_matches_pattern(matcher, path, binary_detected)?;
    let binary_byte_offset = sink.binary_byte_offset();
    if binary_detected {
        matches.clear();
        match_count = 0;
        pending_output.clear();
    }

    if !pending_output.is_empty() {
        config.output_target.write_all(&pending_output)?;
    }

    Ok(FileSearchResult {
        matches,
        match_count,
        binary_detected,
        binary_match_detected,
        binary_byte_offset,
    })
}

pub(super) fn search_file_streaming_ndjson(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    search_file_streaming_ndjson_sequential(config, matcher, path)
}

pub(super) fn search_file_streaming_ndjson_sequential(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    let mut searcher = build_searcher(config, true);
    search_file_ndjson_with_searcher(config, matcher, path, &mut searcher)
}

pub(super) fn search_file(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    if should_use_chunk_parallel_search(config, path)? {
        return search_file_chunk_parallel(config, matcher, path);
    }
    let mut searcher = build_searcher(config, true);
    search_file_collect_matches_with_searcher(config, matcher, path, &mut searcher)
}

pub(super) fn search_file_count(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    if should_use_chunk_parallel_search(config, path)? {
        return search_file_chunk_parallel(config, matcher, path);
    }
    let mut searcher = build_searcher(config, true);
    search_file_count_with_searcher(matcher, path, &mut searcher)
}

pub(super) fn build_matcher(config: &NativeSearchConfig) -> Result<RegexMatcher> {
    let mut builder = RegexMatcherBuilder::new();
    builder.case_insensitive(effective_ignore_case(
        &config.pattern,
        config.ignore_case,
        config.smart_case,
    ));
    builder.fixed_strings(config.fixed_strings);
    builder.word(config.word_boundary);
    if config.crlf {
        builder.crlf(true);
    }
    builder.build(&config.pattern).with_context(|| {
        crate::regex_hint::native_pattern_error_detail(&config.pattern, config.fixed_strings)
    })
}

/// Fail-closed pre-flight for the plain-text native route: can `build_matcher` -- the EXACT
/// matcher `run_native_search` will construct for this request -- compile this pattern under
/// these flags?
///
/// This exists because a `run_native_search` failure is NOT free. `allow_rg_fallback` does catch
/// it and hand the request to real `rg`, but only after printing
/// `warning: native CPU search failed, falling back to ripgrep: failed to compile native search
/// pattern '...'` to stderr -- a line `rg` never emits. So an uncompilable pattern (`[`, `(`,
/// `\Qx\E`, `a{500}{500}{500}`, ...) must be refused BEFORE routing, not discovered mid-request.
///
/// Only the inputs `build_matcher` actually reads are parameters. It also reads `config.crlf`,
/// which no caller on this path ever sets (`--crlf` is a Python-passthrough flag), so the
/// `Default` value is the truthful one here.
pub fn native_search_pattern_compiles(
    pattern: &str,
    ignore_case: bool,
    smart_case: bool,
    fixed_strings: bool,
    word_boundary: bool,
) -> bool {
    let config = NativeSearchConfig {
        pattern: pattern.to_string(),
        ignore_case,
        smart_case,
        fixed_strings,
        word_boundary,
        ..NativeSearchConfig::default()
    };
    build_matcher(&config).is_ok()
}

pub fn effective_ignore_case(pattern: &str, ignore_case: bool, smart_case: bool) -> bool {
    ignore_case || (smart_case && smart_case_pattern_is_case_insensitive(pattern))
}

pub fn smart_case_pattern_is_case_insensitive(pattern: &str) -> bool {
    !pattern.chars().any(|ch| ch.is_uppercase())
}

pub(super) fn build_searcher(config: &NativeSearchConfig, line_number: bool) -> Searcher {
    let mut builder = SearcherBuilder::new();
    builder.line_number(line_number);
    builder.invert_match(config.invert_match);
    builder.before_context(config.before_context);
    builder.after_context(config.after_context);
    builder.max_matches(config.max_count);
    if config.text {
        builder.binary_detection(BinaryDetection::none());
    } else {
        builder.binary_detection(BinaryDetection::quit(b'\x00'));
    }

    if config.null_data {
        builder.line_terminator(LineTerminator::byte(b'\0'));
    } else if config.crlf {
        builder.line_terminator(LineTerminator::crlf());
    }

    if config.mmap {
        // SAFETY: This is the intended opt-in API from grep-searcher for mmap-backed search.
        builder.memory_map(unsafe { MmapChoice::auto() });
    } else {
        builder.memory_map(MmapChoice::never());
    }

    builder.build()
}

/// A completed walk: the files, plus how many entries the walker could not read.
///
/// Task 276 slice B3. `collect_walked_files` used to return a bare `Vec<PathBuf>`, which gave it
/// no way to tell its caller that the list is INCOMPLETE -- the Err arm printed to stderr and the
/// count died there. Its sibling `search_walk_roots_parallel` never had this problem: its worker
/// owns a `SearchStats` and increments `walk_errors` directly (:1330).
pub(super) struct WalkedFiles {
    pub(super) files: Vec<PathBuf>,
    /// Entries the walker reported an I/O error for. Same `is_io()` filter as the streaming path
    /// (:1328) -- a malformed global gitignore must NOT make a complete walk claim incompleteness.
    pub(super) walk_errors: usize,
}

pub(super) fn collect_walked_files(
    config: &NativeSearchConfig,
    roots: &[PathBuf],
) -> Result<WalkedFiles> {
    if let Some(refusal) = check_native_implicit_walk_ceiling(config, roots) {
        return Err(anyhow!(refusal));
    }
    let builder = build_walk_builder(config, roots)?;
    let walked_files = Arc::new(Mutex::new(Vec::new()));
    let shared_files = Arc::clone(&walked_files);
    // Task 276 slice B3. AtomicUsize rather than a Mutex<usize>: this is a pure counter on the
    // hot walker, incremented under contention by every worker thread. Relaxed ordering is
    // sufficient -- no other memory is published through it, and the value is only read after
    // `run()` has joined every worker, which is itself the synchronisation point.
    let walk_errors = Arc::new(AtomicUsize::new(0));
    let shared_walk_errors = Arc::clone(&walk_errors);
    builder.build_parallel().run(|| {
        let shared_files = Arc::clone(&shared_files);
        let shared_walk_errors = Arc::clone(&shared_walk_errors);
        Box::new(move |entry| {
            let entry = match entry {
                Ok(entry) => entry,
                Err(err) => {
                    // Report the per-entry walk error, exactly as the streaming walker in
                    // `search_walk_roots_parallel` already does (task #263). This collector
                    // used to drop the Err arm on the floor with NO output at all, so a
                    // permission-denied subtree vanished from the file list COMPLETELY
                    // silently -- strictly worse than the streaming path, which at least
                    // prints one line. Real `rg` prints one stderr line per unreadable path
                    // and keeps walking every readable file; both tg walkers now match that.
                    //
                    // NOTE (task 280 -> 276 slice A -> 276 slice B3/task 315, CLOSED here).
                    // Printing is only half the contract: `rg` also exits 2 on an unreadable path
                    // while still emitting its matches, and the JSON envelope carries
                    // `result_incomplete` + `incomplete_reason_class` the way the Python routes
                    // have since 276 slice 1 (c0c3404). The rest of that chain lives at:
                    //     :91                     `walk_errors: usize` on SearchStats
                    //     :2436-2438              the envelope emits result_incomplete /
                    //                             incomplete_reason_class / incomplete_paths_count
                    //     main.rs:8388            exit(2) when walk_errors > 0
                    // Do NOT re-derive any of those as missing; earlier revisions of this comment
                    // said they were, and that sent readers off to rebuild what already shipped.
                    //
                    // What WAS missing until slice B3 is the link below: `collect_walked_files`
                    // returned a bare Vec<PathBuf>, so it owned no channel back to its caller and
                    // the count died at this `eprintln!` -- unlike `search_walk_roots_parallel`,
                    // whose worker owns `local_stats` and increments directly (:1330). It now
                    // returns `WalkedFiles`, and `run_native_search` folds the count into `stats`.
                    //
                    // Same `is_io()` gate as the streaming walker (:1328), for the same reason
                    // recorded there: `ignore::Error` also covers a malformed ancestor/global
                    // gitignore, and counting those would label a COMPLETE walk incomplete with a
                    // budget-non-remediable cause. Non-I/O errors still PRINT; they just do not
                    // claim the answer is partial.
                    if err.is_io() {
                        shared_walk_errors.fetch_add(1, Ordering::Relaxed);
                    }
                    eprintln!("tg: {err}");
                    return WalkState::Continue;
                }
            };
            if entry
                .file_type()
                .map(|kind| kind.is_file())
                .unwrap_or(false)
            {
                if let Ok(mut guard) = shared_files.lock() {
                    guard.push(entry.path().to_path_buf());
                }
            }
            WalkState::Continue
        })
    });

    let mut walked_files = walked_files
        .lock()
        .map_err(|_| anyhow!("failed to collect native search walk results"))?
        .clone();
    walked_files.sort_unstable();
    walked_files.dedup();
    Ok(WalkedFiles {
        files: walked_files,
        walk_errors: walk_errors.load(Ordering::Relaxed),
    })
}

pub(super) fn build_walk_builder(
    config: &NativeSearchConfig,
    roots: &[PathBuf],
) -> Result<WalkBuilder> {
    let first_root = roots[0].clone();
    let mut builder = WalkBuilder::new(&first_root);
    for root in roots.iter().skip(1) {
        builder.add(root);
    }
    builder.hidden(!config.hidden);
    builder.max_depth(config.max_depth);
    builder.threads(0);

    if config.no_ignore {
        builder.ignore(false);
        builder.git_ignore(false);
        builder.git_global(false);
        builder.git_exclude(false);
        builder.parents(false);
    } else {
        // Task #267 BLOCKING-1 (independent gate on the first cut of this fix): the `add_ignore`
        // trio below exists SOLELY to compensate for the `ignore` crate's own `require_git(true)`
        // default OUTSIDE a git repository (see `index.rs`'s identical `collect_file_entries`
        // comment) -- INSIDE a git repo, `WalkBuilder`'s own git machinery
        // (`git_ignore`/`git_global`/`git_exclude`, all `true` by default) already applies
        // `.gitignore` natively, so the per-filename `.gitignore` skip immediately below this
        // comment is a NO-OP there: nothing ever called `add_ignore(".gitignore")` to skip in the
        // first place. The first cut of this fix only touched that no-op half and left the
        // walker's own git knobs untouched, so `--no-ignore-vcs` kept doing nothing inside a git
        // repo (execution-verified on the byte-identical walker: a root `.gitignore` + a child
        // dir with no ignore files of its own still excluded the git-ignored file under
        // `--json --no-ignore-vcs`, because the native git path -- not `add_ignore` -- was the
        // one honoring it). Flipping these three knobs is the actual fix for that path; it must
        // NOT also flip `parents(false)` -- unlike blanket `--no-ignore`, `--no-ignore-vcs` is
        // scoped to VCS-sourced ignore files only, and `.ignore`/`.rgignore` parent-directory
        // ascent must keep working. `git_exclude(false)` is also required for
        // `.git/info/exclude`, which real `rg --no-ignore-vcs` re-includes too (verified live)
        // and which is a VCS-scoped ignore source distinct from `.gitignore` proper.
        if config.no_ignore_vcs {
            builder.git_ignore(false);
            builder.git_global(false);
            builder.git_exclude(false);
        }
        for root in roots {
            for ignore_name in [".ignore", ".gitignore", ".rgignore"] {
                // Mirrors `rg_passthrough::root_ignore_file_args`'s per-filename `no_ignore_vcs`
                // scope for the NON-git-repo case. NOT rg-parity: real rg deliberately never
                // honors a root `.gitignore` outside a git repo at all (that's `--no-require-git`
                // territory, rejected on purpose -- see `rg_passthrough.rs::root_ignore_file_args`'s
                // own doc comment); this `add_ignore` trio is tg's own deliberate DIVERGENCE from
                // that rg behavior (#127), not compensation for a gap in rg itself. `.gitignore`
                // is the only VCS-sourced filename in this trio, so it alone is skipped here when
                // `no_ignore_vcs` is set; `.ignore`/`.rgignore` stay honored exactly like the
                // rg-passthrough engine, both inside and outside a git repo.
                if ignore_name == ".gitignore" && config.no_ignore_vcs {
                    continue;
                }
                let ignore_path = root.join(ignore_name);
                if std::fs::symlink_metadata(&ignore_path).is_ok_and(|meta| meta.is_file()) {
                    builder.add_ignore(ignore_path);
                }
            }
        }
    }

    if !config.glob.is_empty() {
        let mut overrides = OverrideBuilder::new(&first_root);
        for glob in &config.glob {
            overrides
                .add(glob)
                .with_context(|| format!("failed to add glob override '{glob}'"))?;
        }
        builder.overrides(
            overrides
                .build()
                .context("failed to build ignore override matcher")?,
        );
    }

    Ok(builder)
}

pub(super) fn should_use_chunk_parallel_search(
    config: &NativeSearchConfig,
    path: &Path,
) -> Result<bool> {
    if !config.parallel_large_files
        || !config.mmap
        || config.null_data
        || config.ndjson
        || (!config.json && !config.count && !config.quiet)
        || config.only_matching
        || config.before_context > 0
        || config.after_context > 0
        || config.max_count.is_some()
        || configured_chunk_parallelism_threads(config) < 2
    {
        return Ok(false);
    }

    let file_len = open_search_file(path)?
        .metadata()
        .with_context(|| {
            format!(
                "failed to read native search metadata for {}",
                path.display()
            )
        })?
        .len();
    Ok(file_len >= config.large_file_chunk_threshold_bytes as u64)
}

pub(super) fn configured_chunk_parallelism_threads(config: &NativeSearchConfig) -> usize {
    config.chunk_parallelism_threads.unwrap_or_else(|| {
        std::thread::available_parallelism()
            .map(|count| count.get())
            .unwrap_or(1)
    })
}

/// Detects binary content the same way `build_searcher` configures every serial-path `Searcher`
/// to: presence of a NUL byte within the guaranteed-detection prefix
/// (`BINARY_DETECTION_PREFIX_BYTES`) means binary, UNLESS `config.text` is set (mirrors
/// `BinaryDetection::none()` -- `--text` never treats input as binary). Returns the offset of the
/// first NUL byte found (relative to the start of `contents`), or `None` if the prefix is clean.
/// Deliberately does NOT scan past the guaranteed prefix -- doing so would make this path detect
/// binary content the serial path would miss for the same file, which is its own divergent-
/// detection bug.
pub(super) fn detect_binary_prefix(config: &NativeSearchConfig, contents: &[u8]) -> Option<u64> {
    if config.text {
        return None;
    }
    let prefix_len = contents.len().min(BINARY_DETECTION_PREFIX_BYTES);
    memchr(b'\x00', &contents[..prefix_len]).map(|offset| offset as u64)
}

pub(super) fn search_file_chunk_parallel(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
) -> Result<FileSearchResult> {
    let file = open_search_file(path)?;
    let mmap = {
        // SAFETY: The file handle remains alive for the lifetime of the mmap, and the mapping is read-only.
        unsafe { MmapOptions::new().map(&file) }
    }
    .with_context(|| format!("failed to memory-map native search path {}", path.display()))?;

    let requested_chunk_count = configured_chunk_parallelism_threads(config);
    let chunk_plan = plan_file_chunks(&mmap, requested_chunk_count, config.count);
    if chunk_plan.len() <= 1 {
        if config.count {
            let mut searcher = build_searcher(config, true);
            return search_file_count_with_searcher(matcher, path, &mut searcher);
        }
        return search_file_json(config, matcher, path);
    }

    // The per-chunk searches below run on raw `&[u8]` slices via `search_slice` with a bare
    // `Bytes` sink (not wrapped in `BinaryAwareSink`), so any `binary_data` callback a per-chunk
    // `Searcher` fires internally never reaches this function. Detect binary content over the
    // whole file up front -- mirroring the serial path's GUARANTEED detection floor (see
    // `detect_binary_prefix`; grep_searcher's mmap `BinaryDetection::quit` also opportunistically
    // scans bytes inside matched/context lines beyond that floor, which this check does not
    // reproduce -- a conservative gap, since it can only under-flag relative to the serial path,
    // never over-flag) -- so a binary file above the chunk-parallel threshold is flagged/skipped
    // like the serial path instead of falling through to the parallel scan and emitting raw byte
    // "matches" (mojibake).
    if let Some(binary_byte_offset) = detect_binary_prefix(config, &mmap) {
        let binary_match_detected = binary_file_matches_pattern(matcher, path, true)?;
        return Ok(FileSearchResult {
            matches: Vec::new(),
            match_count: 0,
            binary_detected: true,
            binary_match_detected,
            binary_byte_offset: Some(binary_byte_offset),
        });
    }

    if config.verbose {
        emit_chunk_parallel_debug(path, mmap.len(), requested_chunk_count, &chunk_plan);
    }

    if config.count {
        let chunk_counts = chunk_plan
            .par_iter()
            .map(|chunk| {
                search_chunk_count(
                    config,
                    matcher,
                    path,
                    &mmap[chunk.byte_start..chunk.byte_end],
                )
            })
            .collect::<Vec<_>>();

        let mut match_count = 0usize;
        for count_result in chunk_counts {
            match_count = match_count.saturating_add(count_result?);
        }

        return Ok(FileSearchResult {
            matches: Vec::new(),
            match_count,
            // Confirmed non-binary by the `detect_binary_prefix` early return above.
            binary_detected: false,
            binary_match_detected: false,
            binary_byte_offset: None,
        });
    }

    let chunk_matches = chunk_plan
        .par_iter()
        .map(|chunk| {
            search_chunk(
                config,
                matcher,
                path,
                &mmap[chunk.byte_start..chunk.byte_end],
                chunk.first_line_number,
            )
        })
        .collect::<Vec<_>>();

    let mut matches = Vec::new();
    for chunk_result in chunk_matches {
        matches.extend(chunk_result?);
    }

    Ok(FileSearchResult {
        match_count: matches.len(),
        matches,
        // Confirmed non-binary by the `detect_binary_prefix` early return above.
        binary_detected: false,
        binary_match_detected: false,
        binary_byte_offset: None,
    })
}

pub(super) fn plan_file_chunks(
    contents: &[u8],
    requested_chunk_count: usize,
    count_only: bool,
) -> Vec<FileChunkPlan> {
    if contents.is_empty() || requested_chunk_count == 0 {
        return Vec::new();
    }

    let target_chunk_size = contents.len().div_ceil(requested_chunk_count);
    let mut ranges = Vec::new();
    let mut byte_start = 0usize;

    while byte_start < contents.len() {
        let minimum_end = byte_start
            .saturating_add(target_chunk_size)
            .min(contents.len());
        let byte_end = if minimum_end >= contents.len() {
            contents.len()
        } else {
            align_chunk_end_to_newline(contents, minimum_end)
        };
        if byte_end <= byte_start {
            break;
        }
        ranges.push((byte_start, byte_end));
        byte_start = byte_end;
    }

    let mut chunks = Vec::with_capacity(ranges.len());
    let mut first_line_number = 1u64;
    for (byte_start, byte_end) in ranges {
        chunks.push(FileChunkPlan {
            byte_start,
            byte_end,
            first_line_number,
        });
        if !count_only {
            first_line_number =
                first_line_number.saturating_add(count_lines(&contents[byte_start..byte_end]));
        }
    }
    chunks
}

pub(super) fn align_chunk_end_to_newline(contents: &[u8], minimum_end: usize) -> usize {
    if minimum_end == 0 || minimum_end >= contents.len() {
        return contents.len();
    }
    if contents[minimum_end - 1] == b'\n' {
        return minimum_end;
    }
    match memchr(b'\n', &contents[minimum_end..]) {
        Some(relative_offset) => minimum_end + relative_offset + 1,
        None => contents.len(),
    }
}

pub(super) fn count_lines(contents: &[u8]) -> u64 {
    if contents.is_empty() {
        return 0;
    }
    let newline_count = memchr_iter(b'\n', contents).count() as u64;
    if contents.last() == Some(&b'\n') {
        newline_count
    } else {
        newline_count + 1
    }
}

pub(super) fn emit_chunk_parallel_debug(
    path: &Path,
    file_len: usize,
    requested_chunk_count: usize,
    chunk_plan: &[FileChunkPlan],
) {
    eprintln!(
        "[native-search] chunk_parallel file={} size_bytes={} requested_chunk_count={} chunk_count={}",
        path.display(),
        file_len,
        requested_chunk_count,
        chunk_plan.len()
    );
    for (index, chunk) in chunk_plan.iter().enumerate() {
        eprintln!(
            "[native-search] chunk[{index}] byte_start={} byte_end={} first_line={}",
            chunk.byte_start, chunk.byte_end, chunk.first_line_number
        );
    }
}

pub(super) fn search_chunk(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
    contents: &[u8],
    first_line_number: u64,
) -> Result<Vec<NativeSearchMatch>> {
    let mut matches = Vec::new();
    let mut searcher = build_searcher(config, true);
    let path_buf = path.to_path_buf();
    searcher
        .search_slice(
            matcher,
            contents,
            Bytes(|line_number, line| {
                let trimmed_line = strip_native_line_terminator(line);
                let rendered_bytes = render_matched_line_bytes(config, trimmed_line)
                    .map_err(sink_io_error)?
                    .into_owned();
                matches.push(NativeSearchMatch {
                    path: path_buf.clone(),
                    line_number: Some(first_line_number + line_number - 1),
                    raw: rendered_bytes,
                });
                Ok(true)
            }),
        )
        .with_context(|| format!("native chunk-parallel search failed for {}", path.display()))?;
    Ok(matches)
}

pub(super) fn search_chunk_count(
    config: &NativeSearchConfig,
    matcher: &RegexMatcher,
    path: &Path,
    contents: &[u8],
) -> Result<usize> {
    let mut match_count = 0usize;
    let mut searcher = build_searcher(config, true);
    searcher
        .search_slice(
            matcher,
            contents,
            Bytes(|_, _| {
                match_count = match_count.saturating_add(1);
                Ok(true)
            }),
        )
        .with_context(|| {
            format!(
                "native chunk-parallel count search failed for {}",
                path.display()
            )
        })?;
    Ok(match_count)
}
