use tensor_grep_rs::routing::{
    route_search, BackendSelection, IndexRoutingState, SearchRoutingCalibration,
    SearchRoutingConfig,
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
        gpu_auto_supported: true,
        prefer_rg_passthrough: false,
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
