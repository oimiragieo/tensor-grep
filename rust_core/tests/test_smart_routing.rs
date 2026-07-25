use tensor_grep_rs::routing::{
    native_can_serve_plain_text, plain_text_native_cheap_checks_pass,
    plain_text_native_flag_token_is_allowed, route_search, BackendSelection, IndexRoutingState,
    PlainTextNativeRequest, SearchRoutingCalibration, SearchRoutingConfig,
};

fn base_config() -> SearchRoutingConfig {
    SearchRoutingConfig {
        explicit_index: false,
        explicit_gpu_device_ids: false,
        force_cpu: false,
        ast_command: false,
        json: false,
        ndjson: false,
        rg_available: true,
        corpus_bytes: 0,
        corpus_bytes_known: true,
        gpu_auto_supported: true,
        prefer_rg_passthrough: false,
        pcre2: false,
        native_plain_text: false,
    }
}

/// The canonical ADMITTED request: single pattern, single explicit regular-file path, plain text,
/// piped stdout, no flag outside `PLAIN_TEXT_NATIVE_ALLOWED_FLAGS`.
fn admitted_plain_text_request() -> PlainTextNativeRequest {
    PlainTextNativeRequest {
        pattern_count: 1,
        pattern_is_empty: false,
        pattern_is_native_renderable: true,
        path_count: 1,
        path_was_implicit: false,
        single_path_is_regular_file: true,
        single_path_is_stdin_sentinel: false,
        single_path_renders_identically: true,
        structured_output: false,
        explicit_format: false,
        stdout_is_terminal: false,
        rg_config_env_present: false,
        only_allowed_flags: true,
    }
}

fn warm_index_state() -> IndexRoutingState {
    IndexRoutingState {
        exists: true,
        is_stale: false,
        pattern_compatible: true,
    }
}

#[test]
fn test_route_search_prioritizes_explicit_index_over_all_other_inputs() {
    let mut config = base_config();
    config.explicit_index = true;
    config.explicit_gpu_device_ids = true;
    config.force_cpu = true;
    config.ast_command = true;
    config.corpus_bytes = 512 * 1024 * 1024;

    let decision = route_search(
        &config,
        Some(&SearchRoutingCalibration {
            threshold_bytes: 32 * 1024 * 1024,
            gpu_positive: true,
        }),
        warm_index_state(),
        true,
    );

    assert_eq!(decision.selection, BackendSelection::TrigramIndex);
    assert_eq!(decision.reason, "index-accelerated");
}

#[test]
fn test_route_search_prioritizes_explicit_gpu_over_force_cpu_and_warm_index() {
    let mut config = base_config();
    config.explicit_gpu_device_ids = true;
    config.force_cpu = true;
    config.corpus_bytes = 256 * 1024 * 1024;

    let decision = route_search(
        &config,
        Some(&SearchRoutingCalibration {
            threshold_bytes: 32 * 1024 * 1024,
            gpu_positive: true,
        }),
        warm_index_state(),
        true,
    );

    assert_eq!(decision.selection, BackendSelection::NativeGpu);
    assert_eq!(decision.reason, "gpu-device-ids-explicit-native");
}

#[test]
fn test_route_search_prioritizes_force_cpu_over_auto_gpu() {
    let mut config = base_config();
    config.force_cpu = true;
    config.corpus_bytes = 256 * 1024 * 1024;

    let decision = route_search(
        &config,
        Some(&SearchRoutingCalibration {
            threshold_bytes: 32 * 1024 * 1024,
            gpu_positive: true,
        }),
        IndexRoutingState::default(),
        true,
    );

    assert_eq!(decision.selection, BackendSelection::Ripgrep);
    assert_eq!(decision.reason, "force-cpu");
    assert!(!decision.allow_rg_fallback);
}

#[test]
fn test_route_search_routes_ast_commands_to_ast_backend() {
    let mut config = base_config();
    config.ast_command = true;
    config.corpus_bytes = 512 * 1024 * 1024;

    let decision = route_search(
        &config,
        Some(&SearchRoutingCalibration {
            threshold_bytes: 32 * 1024 * 1024,
            gpu_positive: true,
        }),
        warm_index_state(),
        true,
    );

    assert_eq!(decision.selection, BackendSelection::AstBackend);
    assert_eq!(decision.reason, "ast-native");
}

#[test]
fn test_route_search_uses_warm_non_stale_compatible_index_before_auto_gpu() {
    let mut config = base_config();
    config.corpus_bytes = 512 * 1024 * 1024;

    let decision = route_search(
        &config,
        Some(&SearchRoutingCalibration {
            threshold_bytes: 32 * 1024 * 1024,
            gpu_positive: true,
        }),
        warm_index_state(),
        true,
    );

    assert_eq!(decision.selection, BackendSelection::TrigramIndex);
    assert_eq!(decision.reason, "index-accelerated");
}

#[test]
fn test_route_search_ignores_stale_or_incompatible_index() {
    let mut config = base_config();
    config.corpus_bytes = 256 * 1024 * 1024;

    let stale = route_search(
        &config,
        Some(&SearchRoutingCalibration {
            threshold_bytes: 32 * 1024 * 1024,
            gpu_positive: true,
        }),
        IndexRoutingState {
            exists: true,
            is_stale: true,
            pattern_compatible: true,
        },
        true,
    );

    let incompatible = route_search(
        &config,
        Some(&SearchRoutingCalibration {
            threshold_bytes: 32 * 1024 * 1024,
            gpu_positive: true,
        }),
        IndexRoutingState {
            exists: true,
            is_stale: false,
            pattern_compatible: false,
        },
        true,
    );

    assert_eq!(stale.selection, BackendSelection::NativeGpu);
    assert_eq!(incompatible.selection, BackendSelection::NativeGpu);
}

#[test]
fn test_route_search_auto_routes_to_gpu_only_with_positive_calibration_above_threshold() {
    let mut config = base_config();
    config.corpus_bytes = 128 * 1024 * 1024;

    let positive = route_search(
        &config,
        Some(&SearchRoutingCalibration {
            threshold_bytes: 32 * 1024 * 1024,
            gpu_positive: true,
        }),
        IndexRoutingState::default(),
        true,
    );
    assert_eq!(positive.selection, BackendSelection::NativeGpu);
    assert_eq!(positive.reason, "gpu-auto-size-threshold");

    let missing = route_search(&config, None, IndexRoutingState::default(), true);
    assert_eq!(missing.selection, BackendSelection::Ripgrep);

    let negative = route_search(
        &config,
        Some(&SearchRoutingCalibration {
            threshold_bytes: 32 * 1024 * 1024,
            gpu_positive: false,
        }),
        IndexRoutingState::default(),
        true,
    );
    assert_eq!(negative.selection, BackendSelection::Ripgrep);

    let unknown_corpus_size = route_search(
        &SearchRoutingConfig {
            corpus_bytes: 64 * 1024 * 1024,
            corpus_bytes_known: false,
            ..config
        },
        Some(&SearchRoutingCalibration {
            threshold_bytes: 32 * 1024 * 1024,
            gpu_positive: true,
        }),
        IndexRoutingState::default(),
        true,
    );
    assert_eq!(unknown_corpus_size.selection, BackendSelection::Ripgrep);

    let below_threshold = route_search(
        &SearchRoutingConfig {
            corpus_bytes: 8 * 1024 * 1024,
            ..config
        },
        Some(&SearchRoutingCalibration {
            threshold_bytes: 32 * 1024 * 1024,
            gpu_positive: true,
        }),
        IndexRoutingState::default(),
        true,
    );
    assert_eq!(below_threshold.selection, BackendSelection::Ripgrep);

    let unavailable = route_search(
        &config,
        Some(&SearchRoutingCalibration {
            threshold_bytes: 32 * 1024 * 1024,
            gpu_positive: true,
        }),
        IndexRoutingState::default(),
        false,
    );
    assert_eq!(unavailable.selection, BackendSelection::NativeCpu);
}

#[test]
fn test_route_search_disables_rg_fallback_for_structured_outputs() {
    let mut config = base_config();
    config.json = true;

    let json_decision = route_search(&config, None, IndexRoutingState::default(), false);
    assert_eq!(json_decision.selection, BackendSelection::NativeCpu);
    assert!(!json_decision.allow_rg_fallback);

    config.json = false;
    config.ndjson = true;

    let ndjson_decision = route_search(&config, None, IndexRoutingState::default(), false);
    assert_eq!(ndjson_decision.selection, BackendSelection::NativeCpu);
    assert!(!ndjson_decision.allow_rg_fallback);
}

#[test]
fn test_route_search_can_prefer_ripgrep_passthrough_as_final_fallback() {
    let mut config = base_config();
    config.prefer_rg_passthrough = true;

    let decision = route_search(&config, None, IndexRoutingState::default(), false);

    assert_eq!(decision.selection, BackendSelection::Ripgrep);
    assert_eq!(decision.reason, "rg_passthrough");
    assert!(!decision.allow_rg_fallback);
}

#[test]
fn test_route_search_defaults_to_ripgrep_for_cold_text_search() {
    let decision = route_search(&base_config(), None, IndexRoutingState::default(), false);

    assert_eq!(decision.selection, BackendSelection::Ripgrep);
    assert_eq!(decision.reason, "rg_passthrough");
    assert!(!decision.allow_rg_fallback);
}

// ---------------------------------------------------------------------------
// Plain-text native routing (perf: skip the `rg` subprocess for the provably
// rg-identical subset). See `native_can_serve_plain_text` in routing.rs.
// ---------------------------------------------------------------------------

/// THE reachability pin. Before the `native_plain_text` guard, `route_search`'s final
/// `native_cpu_auto(true, false)` arm was logically unreachable: reaching it needed
/// `rg_available == true`, but the rg-passthrough branch above it then required
/// `structured_output == true` to fall through, which the `structured_output` branch caught
/// first. This test fails the moment that arm goes dead again.
#[test]
fn test_route_search_routes_admitted_plain_text_to_native_cpu() {
    let mut config = base_config();
    config.native_plain_text = true;

    let decision = route_search(&config, None, IndexRoutingState::default(), false);

    assert_eq!(decision.selection, BackendSelection::NativeCpu);
    assert_eq!(decision.reason, "plain-text-native");
    // rg is still the safety net if the native engine errors mid-request.
    assert!(decision.allow_rg_fallback);
}

/// The same config with the predicate refused must be byte-for-byte today's behavior.
#[test]
fn test_route_search_keeps_ripgrep_when_plain_text_predicate_refuses() {
    let mut config = base_config();
    config.native_plain_text = false;

    let decision = route_search(&config, None, IndexRoutingState::default(), false);

    assert_eq!(decision.selection, BackendSelection::Ripgrep);
    assert_eq!(decision.reason, "rg_passthrough");
}

/// A context search (`-A`/`-B`/`-C`) is what `prefer_rg_passthrough` encodes, and it must keep
/// reaching real `rg` even if some future adapter bug set `native_plain_text`.
#[test]
fn test_route_search_context_search_still_prefers_ripgrep() {
    let mut config = base_config();
    config.prefer_rg_passthrough = true;
    config.native_plain_text = false;

    let decision = route_search(&config, None, IndexRoutingState::default(), false);

    assert_eq!(decision.selection, BackendSelection::Ripgrep);
    assert_eq!(decision.reason, "rg_passthrough");
}

/// `--json` / `--ndjson` routing is UNCHANGED by this feature: they were already native, they
/// must stay native, with the same reason string and no rg fallback.
#[test]
fn test_route_search_structured_output_routing_is_unchanged_by_plain_text_flag() {
    for native_plain_text in [false, true] {
        let mut json_config = base_config();
        json_config.json = true;
        json_config.native_plain_text = native_plain_text;
        let json_decision = route_search(&json_config, None, IndexRoutingState::default(), false);
        assert_eq!(json_decision.selection, BackendSelection::NativeCpu);
        assert_eq!(json_decision.reason, "json_output");
        assert!(!json_decision.allow_rg_fallback);

        let mut ndjson_config = base_config();
        ndjson_config.ndjson = true;
        ndjson_config.native_plain_text = native_plain_text;
        let ndjson_decision =
            route_search(&ndjson_config, None, IndexRoutingState::default(), false);
        assert_eq!(ndjson_decision.selection, BackendSelection::NativeCpu);
        assert_eq!(ndjson_decision.reason, "json_output");
        assert!(!ndjson_decision.allow_rg_fallback);
    }
}

/// Higher-precedence routes are unaffected: an explicit `--index`, an explicit
/// `--gpu-device-ids`, `--pcre2`, and a warm compatible index all still win over the new arm.
#[test]
fn test_route_search_plain_text_flag_never_overrides_higher_precedence_routes() {
    let mut index_config = base_config();
    index_config.native_plain_text = true;
    index_config.explicit_index = true;
    assert_eq!(
        route_search(&index_config, None, IndexRoutingState::default(), false).selection,
        BackendSelection::TrigramIndex
    );

    let mut gpu_config = base_config();
    gpu_config.native_plain_text = true;
    gpu_config.explicit_gpu_device_ids = true;
    assert_eq!(
        route_search(&gpu_config, None, IndexRoutingState::default(), false).selection,
        BackendSelection::NativeGpu
    );

    let mut pcre2_config = base_config();
    pcre2_config.native_plain_text = true;
    pcre2_config.pcre2 = true;
    let pcre2_decision = route_search(&pcre2_config, None, IndexRoutingState::default(), false);
    assert_eq!(pcre2_decision.selection, BackendSelection::Ripgrep);
    assert_eq!(pcre2_decision.reason, "pcre2-required");

    let mut warm_config = base_config();
    warm_config.native_plain_text = true;
    assert_eq!(
        route_search(&warm_config, None, warm_index_state(), false).selection,
        BackendSelection::TrigramIndex
    );
}

#[test]
fn test_native_can_serve_plain_text_admits_the_canonical_request() {
    assert!(native_can_serve_plain_text(&admitted_plain_text_request()));
}

/// The cheap/expensive split is a LATENCY contract, not a refactor: an adapter is required to run
/// `plain_text_native_cheap_checks_pass` first and only then pay for a regex compile and a full
/// file read. This pins the two properties that make the contract safe to rely on.
#[test]
fn test_cheap_checks_gate_the_expensive_tier() {
    // 1. The cheap tier alone never admits -- the expensive fields still have to be true.
    let unprobed = PlainTextNativeRequest {
        pattern_is_native_renderable: false,
        single_path_renders_identically: false,
        ..admitted_plain_text_request()
    };
    assert!(plain_text_native_cheap_checks_pass(&unprobed));
    assert!(!native_can_serve_plain_text(&unprobed));

    // 2. Every cheap disqualifier is visible to the cheap tier, so an adapter that consults it
    //    first can skip the expensive work on ALL of them -- including the requests this route
    //    never optimises (terminal, --json/--ndjson, context searches).
    for disqualified in [
        PlainTextNativeRequest {
            stdout_is_terminal: true,
            ..admitted_plain_text_request()
        },
        PlainTextNativeRequest {
            structured_output: true,
            ..admitted_plain_text_request()
        },
        PlainTextNativeRequest {
            only_allowed_flags: false,
            ..admitted_plain_text_request()
        },
        PlainTextNativeRequest {
            single_path_is_regular_file: false,
            ..admitted_plain_text_request()
        },
        PlainTextNativeRequest {
            pattern_is_empty: true,
            ..admitted_plain_text_request()
        },
        // The environment clause lives in the CHEAP tier deliberately: it is one env lookup with
        // no I/O, and putting it here means an rg-config user never pays for a file read either.
        PlainTextNativeRequest {
            rg_config_env_present: true,
            ..admitted_plain_text_request()
        },
        PlainTextNativeRequest {
            single_path_is_stdin_sentinel: true,
            ..admitted_plain_text_request()
        },
    ] {
        assert!(!plain_text_native_cheap_checks_pass(&disqualified));
        assert!(!native_can_serve_plain_text(&disqualified));
    }
}

/// Every structural refusal, one at a time, from the otherwise-admitted request.
#[test]
fn test_native_can_serve_plain_text_refuses_each_disqualifier() {
    let terminal = PlainTextNativeRequest {
        stdout_is_terminal: true,
        ..admitted_plain_text_request()
    };
    assert!(!native_can_serve_plain_text(&terminal));

    let implicit_path = PlainTextNativeRequest {
        path_was_implicit: true,
        ..admitted_plain_text_request()
    };
    assert!(!native_can_serve_plain_text(&implicit_path));

    let directory_path = PlainTextNativeRequest {
        single_path_is_regular_file: false,
        ..admitted_plain_text_request()
    };
    assert!(!native_can_serve_plain_text(&directory_path));

    // One field carries three data-level divergences: CRLF, non-UTF-8, and NUL/binary.
    let unrenderable_file = PlainTextNativeRequest {
        single_path_renders_identically: false,
        ..admitted_plain_text_request()
    };
    assert!(!native_can_serve_plain_text(&unrenderable_file));

    let empty_pattern = PlainTextNativeRequest {
        pattern_is_empty: true,
        ..admitted_plain_text_request()
    };
    assert!(!native_can_serve_plain_text(&empty_pattern));

    // Refusal note 9: `-` means STDIN to rg, but `Path::new("-").is_file()` is TRUE whenever a
    // file literally named `-` exists in cwd -- so the native route would search that file while
    // rg searched stdin. Plausible output, rc=0, no stderr, WRONG data source.
    let stdin_sentinel = PlainTextNativeRequest {
        single_path_is_stdin_sentinel: true,
        ..admitted_plain_text_request()
    };
    assert!(!native_can_serve_plain_text(&stdin_sentinel));

    // Refusal note 7: a pattern rg rejects (rc=2) that the native matcher accepts with 0 matches
    // (rc=1), or one that fails to compile and trips the extra-stderr fallback warning.
    let unrenderable_pattern = PlainTextNativeRequest {
        pattern_is_native_renderable: false,
        ..admitted_plain_text_request()
    };
    assert!(!native_can_serve_plain_text(&unrenderable_pattern));

    // Refusal note 8: `$RIPGREP_CONFIG_PATH` applies to the rg subprocess and NOT to the native
    // engine, so the canonical admitted shape would return silently wrong results (a config
    // containing `-i` changes which lines match; `--vimgrep` changes the whole output format).
    let rg_config_env = PlainTextNativeRequest {
        rg_config_env_present: true,
        ..admitted_plain_text_request()
    };
    assert!(!native_can_serve_plain_text(&rg_config_env));

    let multiple_paths = PlainTextNativeRequest {
        path_count: 2,
        ..admitted_plain_text_request()
    };
    assert!(!native_can_serve_plain_text(&multiple_paths));

    let no_path = PlainTextNativeRequest {
        path_count: 0,
        ..admitted_plain_text_request()
    };
    assert!(!native_can_serve_plain_text(&no_path));

    let multiple_patterns = PlainTextNativeRequest {
        pattern_count: 2,
        ..admitted_plain_text_request()
    };
    assert!(!native_can_serve_plain_text(&multiple_patterns));

    let structured = PlainTextNativeRequest {
        structured_output: true,
        ..admitted_plain_text_request()
    };
    assert!(!native_can_serve_plain_text(&structured));

    let explicit_format = PlainTextNativeRequest {
        explicit_format: true,
        ..admitted_plain_text_request()
    };
    assert!(!native_can_serve_plain_text(&explicit_format));

    let disallowed_flag = PlainTextNativeRequest {
        only_allowed_flags: false,
        ..admitted_plain_text_request()
    };
    assert!(!native_can_serve_plain_text(&disallowed_flag));
}

/// The allow-list is a CONTRACT, and this test makes it BITE rather than restate it: every
/// entry must be accepted by `plain_text_native_flag_token_is_allowed` (the token matcher the
/// raw-argv adapter actually calls), every combined short cluster built from admitted letters
/// must be accepted, and a cluster containing one non-admitted letter must be refused. The
/// constant-vs-`SearchArgs`-destructure direction is covered in `main.rs` by
/// `every_allow_listed_flag_is_actually_admitted_by_the_search_args_predicate`, which drives the
/// real predicate -- neither test can pass by merely echoing the constant.
#[test]
fn test_plain_text_native_allow_list_drives_the_token_matcher() {
    let allowed = tensor_grep_rs::routing::PLAIN_TEXT_NATIVE_ALLOWED_FLAGS;
    assert!(!allowed.is_empty());

    for flag in allowed {
        assert!(
            plain_text_native_flag_token_is_allowed(flag),
            "{flag} is allow-listed but the token matcher refuses it"
        );
    }

    for cluster in ["-in", "-iw", "-Fn", "-inFw", "-i"] {
        assert!(
            plain_text_native_flag_token_is_allowed(cluster),
            "{cluster} is built only from admitted short flags and must be accepted"
        );
    }

    for excluded in [
        "-c",
        "--count",
        "-v",
        "--invert-match",
        "-C",
        "--context",
        "-A",
        "-B",
        "-o",
        "--only-matching",
        "-r",
        "--replace",
        "-P",
        "--pcre2",
        "-N",
        "--no-line-number",
        "-S",
        "--smart-case",
        "-g",
        "--glob",
        "-t",
        "--type",
        "--hidden",
        "--max-depth",
        "--text",
        "--count-matches",
        "--sort",
        "--format",
        "--color",
        "--json",
        "--ndjson",
        "--cpu",
        "--index",
        // Clusters containing one non-admitted letter must refuse as a whole.
        "-ic",
        "-vn",
        "-inS",
        // `--flag=value` spellings are never admitted (no admitted flag takes a value).
        "--ignore-case=true",
    ] {
        assert!(
            !plain_text_native_flag_token_is_allowed(excluded),
            "{excluded} must keep spawning rg"
        );
    }
}
