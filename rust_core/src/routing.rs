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

    pub const fn native_cpu_auto(rg_available: bool, structured_output: bool) -> Self {
        Self::new(
            BackendSelection::NativeCpu,
            "cpu-auto-size-threshold",
            rg_available && !structured_output,
        )
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

    if config.rg_available && (config.prefer_rg_passthrough || !structured_output) {
        return RoutingDecision::ripgrep();
    }

    if structured_output {
        return RoutingDecision::native_cpu_json(config.rg_available);
    }

    if !config.rg_available {
        RoutingDecision::native_cpu_rg_unavailable()
    } else {
        RoutingDecision::native_cpu_auto(true, false)
    }
}
