use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::env;
use std::fmt;
use std::fs::{self, File};
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

pub const DEFAULT_CALIBRATION_SIZES: [u64; 5] = [
    1024 * 1024,
    10 * 1024 * 1024,
    100 * 1024 * 1024,
    500 * 1024 * 1024,
    1024 * 1024 * 1024,
];
pub const CALIBRATION_SAMPLE_COUNT: usize = 3;
pub const STALE_AFTER_SECS: u64 = 7 * 24 * 60 * 60;

const CONFIG_ENV_VAR: &str = "TG_CROSSOVER_CONFIG_PATH";
const CACHE_ENV_VAR: &str = "TG_CROSSOVER_CACHE_DIR";
const MOCK_RESULTS_ENV_VAR: &str = "TG_TEST_CALIBRATION_RESULTS";
const PROJECT_CONFIG_FILE: &str = ".tg_crossover";
const DEFAULT_PATTERN: &str = "ERROR gpu calibration target";
const DEFAULT_DEVICE_ID: i32 = 0;
const GENERATED_FILE_COUNT: usize = 4;
pub const CROSSOVER_CONFIG_VERSION: u32 = 1;
pub const CROSSOVER_ROUTING_BACKEND: &str = "Calibration";
pub const CROSSOVER_ROUTING_REASON: &str = "manual-calibrate";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CalibrationMeasurement {
    pub size_bytes: u64,
    pub cpu_median_ms: f64,
    pub gpu_median_ms: f64,
    pub cpu_samples_ms: Vec<f64>,
    pub gpu_samples_ms: Vec<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CrossoverConfig {
    #[serde(default = "crossover_config_version")]
    pub version: u32,
    #[serde(default = "crossover_routing_backend")]
    pub routing_backend: String,
    #[serde(default = "crossover_routing_reason")]
    pub routing_reason: String,
    #[serde(default)]
    pub sidecar_used: bool,
    pub corpus_size_breakpoint_bytes: u64,
    pub cpu_median_ms: f64,
    pub gpu_median_ms: f64,
    pub recommendation: String,
    pub calibration_timestamp: u64,
    pub device_name: String,
    #[serde(default)]
    pub measurements: Vec<CalibrationMeasurement>,
}

#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum CrossoverRoutingMode {
    Missing,
    Stale,
    CpuAlways,
    GpuAboveBreakpoint,
    BelowBreakpoint,
}

#[derive(Debug, Deserialize)]
struct MockCalibrationRun {
    device_name: String,
    measurements: Vec<MockCalibrationMeasurement>,
}

#[derive(Debug, Deserialize)]
struct MockCalibrationMeasurement {
    size_bytes: u64,
    cpu_samples_ms: Vec<f64>,
    gpu_samples_ms: Vec<f64>,
}

impl CrossoverConfig {
    pub fn is_stale(&self, now_timestamp: u64) -> bool {
        now_timestamp.saturating_sub(self.calibration_timestamp) > STALE_AFTER_SECS
    }

    pub fn routing_mode(&self, corpus_size_bytes: u64) -> CrossoverRoutingMode {
        if self.recommendation == "cpu_always" {
            return CrossoverRoutingMode::CpuAlways;
        }
        if corpus_size_bytes >= self.corpus_size_breakpoint_bytes {
            CrossoverRoutingMode::GpuAboveBreakpoint
        } else {
            CrossoverRoutingMode::BelowBreakpoint
        }
    }
}

fn crossover_config_version() -> u32 {
    CROSSOVER_CONFIG_VERSION
}

fn crossover_routing_backend() -> String {
    CROSSOVER_ROUTING_BACKEND.to_string()
}

fn crossover_routing_reason() -> String {
    CROSSOVER_ROUTING_REASON.to_string()
}

pub fn current_timestamp() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

pub fn resolve_crossover_config_path(search_root: Option<&Path>) -> PathBuf {
    if let Some(path) = env::var_os(CONFIG_ENV_VAR) {
        return PathBuf::from(path);
    }

    let project_path = project_crossover_path(search_root);
    if project_path.is_file() {
        return project_path;
    }

    if let Some(config_home) = user_crossover_config_path() {
        return config_home;
    }

    project_path
}

pub fn load_crossover_config(
    search_root: Option<&Path>,
) -> Result<Option<(PathBuf, CrossoverConfig)>> {
    let path = resolve_crossover_config_path(search_root);
    if !path.is_file() {
        return Ok(None);
    }

    let bytes = fs::read(&path).with_context(|| {
        format!(
            "failed to read crossover calibration file {}",
            path.display()
        )
    })?;
    let config: CrossoverConfig = serde_json::from_slice(&bytes).with_context(|| {
        format!(
            "failed to parse crossover calibration file {}",
            path.display()
        )
    })?;
    Ok(Some((path, config)))
}

pub fn load_fresh_crossover_config(
    search_root: Option<&Path>,
    now_timestamp: u64,
) -> Result<Option<(PathBuf, CrossoverConfig)>> {
    match load_crossover_config(search_root)? {
        Some((path, config)) if !config.is_stale(now_timestamp) => Ok(Some((path, config))),
        Some(_) | None => Ok(None),
    }
}

pub fn run_crossover_calibration(executable: &Path) -> Result<CrossoverConfig> {
    let now_timestamp = current_timestamp();

    if let Some(mocked) = load_mock_calibration_results()? {
        let measurements = mocked
            .measurements
            .into_iter()
            .map(|measurement| {
                CalibrationMeasurement::from_samples(
                    measurement.size_bytes,
                    measurement.cpu_samples_ms,
                    measurement.gpu_samples_ms,
                )
            })
            .collect::<Result<Vec<_>>>()?;
        return summarize_measurements(mocked.device_name, measurements, now_timestamp);
    }

    let cache_dir = resolve_crossover_cache_dir();
    fs::create_dir_all(&cache_dir).with_context(|| {
        format!(
            "failed to create crossover cache directory {}",
            cache_dir.display()
        )
    })?;

    let device_name = detect_device_name(DEFAULT_DEVICE_ID)?;
    let mut measurements = Vec::new();
    for size_bytes in DEFAULT_CALIBRATION_SIZES {
        let corpus_dir = ensure_cached_corpus(&cache_dir, size_bytes)?;
        let cpu_samples_ms = benchmark_mode(executable, &corpus_dir, SearchMode::Cpu)?;
        let gpu_samples_ms =
            benchmark_mode(executable, &corpus_dir, SearchMode::Gpu(DEFAULT_DEVICE_ID))?;
        measurements.push(CalibrationMeasurement::from_samples(
            size_bytes,
            cpu_samples_ms,
            gpu_samples_ms,
        )?);
    }

    summarize_measurements(device_name, measurements, now_timestamp)
}

pub fn write_crossover_config(
    config: &CrossoverConfig,
    search_root: Option<&Path>,
) -> Result<PathBuf> {
    let path = resolve_crossover_config_path(search_root);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).with_context(|| {
            format!(
                "failed to create parent directory for crossover config {}",
                path.display()
            )
        })?;
    }

    let bytes =
        serde_json::to_vec_pretty(config).context("failed to serialize crossover config")?;
    fs::write(&path, bytes)
        .with_context(|| format!("failed to write crossover config {}", path.display()))?;
    Ok(path)
}

fn load_mock_calibration_results() -> Result<Option<MockCalibrationRun>> {
    let Some(raw) = env::var_os(MOCK_RESULTS_ENV_VAR) else {
        return Ok(None);
    };

    let parsed: MockCalibrationRun = serde_json::from_str(&raw.to_string_lossy())
        .context("failed to parse TG_TEST_CALIBRATION_RESULTS JSON")?;
    Ok(Some(parsed))
}

fn summarize_measurements(
    device_name: String,
    measurements: Vec<CalibrationMeasurement>,
    calibration_timestamp: u64,
) -> Result<CrossoverConfig> {
    if measurements.is_empty() {
        bail!("crossover calibration did not produce any measurements");
    }

    let winner = measurements
        .iter()
        .find(|measurement| measurement.gpu_median_ms < measurement.cpu_median_ms)
        .cloned();
    let representative = winner.clone().unwrap_or_else(|| {
        measurements
            .iter()
            .min_by(|left, right| {
                let left_ratio = left.gpu_median_ms / left.cpu_median_ms.max(0.000_1);
                let right_ratio = right.gpu_median_ms / right.cpu_median_ms.max(0.000_1);
                left_ratio
                    .partial_cmp(&right_ratio)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .cloned()
            .unwrap()
    });

    let recommendation = if winner.is_some() {
        format!("gpu_above_{}mb", representative.corpus_megabytes().max(1))
    } else {
        "cpu_always".to_string()
    };

    Ok(CrossoverConfig {
        version: CROSSOVER_CONFIG_VERSION,
        routing_backend: CROSSOVER_ROUTING_BACKEND.to_string(),
        routing_reason: CROSSOVER_ROUTING_REASON.to_string(),
        sidecar_used: false,
        corpus_size_breakpoint_bytes: representative.size_bytes,
        cpu_median_ms: representative.cpu_median_ms,
        gpu_median_ms: representative.gpu_median_ms,
        recommendation,
        calibration_timestamp,
        device_name,
        measurements,
    })
}

impl CalibrationMeasurement {
    fn from_samples(
        size_bytes: u64,
        cpu_samples_ms: Vec<f64>,
        gpu_samples_ms: Vec<f64>,
    ) -> Result<Self> {
        Ok(Self {
            size_bytes,
            cpu_median_ms: median(&cpu_samples_ms)?,
            gpu_median_ms: median(&gpu_samples_ms)?,
            cpu_samples_ms,
            gpu_samples_ms,
        })
    }

    fn corpus_megabytes(&self) -> u64 {
        self.size_bytes / (1024 * 1024)
    }
}

fn median(samples: &[f64]) -> Result<f64> {
    if samples.is_empty() {
        bail!("median requires at least one sample")
    }

    let mut sorted = samples.to_vec();
    sorted.sort_by(|left, right| left.partial_cmp(right).unwrap_or(std::cmp::Ordering::Equal));
    let middle = sorted.len() / 2;
    if sorted.len() % 2 == 1 {
        Ok(sorted[middle])
    } else {
        Ok((sorted[middle - 1] + sorted[middle]) / 2.0)
    }
}

fn benchmark_mode(executable: &Path, corpus_dir: &Path, mode: SearchMode) -> Result<Vec<f64>> {
    let mut samples = Vec::with_capacity(CALIBRATION_SAMPLE_COUNT);
    for _ in 0..CALIBRATION_SAMPLE_COUNT {
        let started = Instant::now();
        let mut command = Command::new(executable);
        command.arg("search");
        match mode {
            SearchMode::Cpu => {
                command.arg("--cpu");
            }
            SearchMode::Gpu(device_id) => {
                command.arg("--gpu-device-ids").arg(device_id.to_string());
            }
        }
        command
            .arg("--fixed-strings")
            .arg("--count")
            .arg(DEFAULT_PATTERN)
            .arg(corpus_dir)
            .stdout(Stdio::null())
            .stderr(Stdio::null());

        let status = command.status().with_context(|| {
            format!(
                "failed to execute crossover calibration command for {}",
                corpus_dir.display()
            )
        })?;
        if !status.success() {
            bail!(
                "crossover calibration command failed for {} with exit code {:?}",
                corpus_dir.display(),
                status.code()
            );
        }

        samples.push(started.elapsed().as_secs_f64() * 1000.0);
    }
    Ok(samples)
}

fn ensure_cached_corpus(cache_dir: &Path, size_bytes: u64) -> Result<PathBuf> {
    let corpus_dir = cache_dir.join(size_label(size_bytes));
    if corpus_size_bytes(&corpus_dir).unwrap_or(0) >= size_bytes {
        return Ok(corpus_dir);
    }

    if corpus_dir.exists() {
        fs::remove_dir_all(&corpus_dir)
            .with_context(|| format!("failed to reset cached corpus {}", corpus_dir.display()))?;
    }
    fs::create_dir_all(&corpus_dir)
        .with_context(|| format!("failed to create cached corpus {}", corpus_dir.display()))?;

    let pattern_line = format!("{DEFAULT_PATTERN}\n");
    let filler_line = "INFO steady state\nWARN retry later\n";
    let chunk = format!("{pattern_line}{filler_line}");
    let target_per_file = size_bytes.div_ceil(GENERATED_FILE_COUNT as u64);

    for index in 0..GENERATED_FILE_COUNT {
        let file_path = corpus_dir.join(format!("chunk-{index}.log"));
        let file = File::create(&file_path).with_context(|| {
            format!(
                "failed to create cached corpus file {}",
                file_path.display()
            )
        })?;
        let mut writer = BufWriter::new(file);
        let mut written = 0u64;
        while written < target_per_file {
            writer.write_all(chunk.as_bytes()).with_context(|| {
                format!("failed to write cached corpus file {}", file_path.display())
            })?;
            written = written.saturating_add(chunk.len() as u64);
        }
        writer.flush().with_context(|| {
            format!("failed to flush cached corpus file {}", file_path.display())
        })?;
    }

    Ok(corpus_dir)
}

fn corpus_size_bytes(corpus_dir: &Path) -> Result<u64> {
    if !corpus_dir.is_dir() {
        return Ok(0);
    }

    let mut total = 0u64;
    for entry in fs::read_dir(corpus_dir)
        .with_context(|| format!("failed to read cached corpus {}", corpus_dir.display()))?
    {
        let entry = entry?;
        if entry.file_type()?.is_file() {
            total = total.saturating_add(entry.metadata()?.len());
        }
    }
    Ok(total)
}

fn size_label(size_bytes: u64) -> String {
    if size_bytes >= 1024 * 1024 * 1024 {
        format!("{}GB", size_bytes / (1024 * 1024 * 1024))
    } else {
        format!("{}MB", size_bytes / (1024 * 1024))
    }
}

fn resolve_crossover_cache_dir() -> PathBuf {
    if let Some(path) = env::var_os(CACHE_ENV_VAR) {
        return PathBuf::from(path);
    }
    env::temp_dir().join("tensor-grep-crossover")
}

fn project_crossover_path(search_root: Option<&Path>) -> PathBuf {
    let base = search_root
        .map(normalize_search_root)
        .or_else(|| env::current_dir().ok())
        .unwrap_or_else(|| PathBuf::from("."));
    base.join(PROJECT_CONFIG_FILE)
}

fn normalize_search_root(path: &Path) -> PathBuf {
    if path.is_file() {
        path.parent().unwrap_or(Path::new(".")).to_path_buf()
    } else {
        path.to_path_buf()
    }
}

fn user_crossover_config_path() -> Option<PathBuf> {
    if let Some(path) = env::var_os("XDG_CONFIG_HOME") {
        return Some(
            PathBuf::from(path)
                .join("tensor-grep")
                .join("crossover.json"),
        );
    }
    if let Some(path) = env::var_os("APPDATA") {
        return Some(
            PathBuf::from(path)
                .join("tensor-grep")
                .join("crossover.json"),
        );
    }
    env::var_os("HOME").map(|home| {
        PathBuf::from(home)
            .join(".config")
            .join("tensor-grep")
            .join("crossover.json")
    })
}

// P0-4 (GPU Phase-0 honesty, #596) pointed calibrate-failure guidance at
// TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR + `tg upgrade`, phrased conditionally ("if published ...
// falls back to CPU when it is not") so it would never assert a false "no NVIDIA asset
// published" claim. CEO dogfood (v1.76.6) found that phrasing still misleading in practice:
// no NVIDIA-flavored asset has ever shipped (the release profile that builds one is held
// off), so the caveated "upgrade" invitation was a permanent dead end dressed up as honest
// advice. Fix: stop advertising the upgrade from a build that structurally has no CUDA
// support compiled in -- state plainly that GPU acceleration is experimental and not shipped
// in *this build*. This is flip-agnostic with NO new signal needed: the moment an
// NVIDIA-flavored asset actually ships, it is compiled WITH the `cuda` feature, which takes
// the entirely different arm below (real device enumeration) and never reaches this string
// at all -- so the claim can never go stale from a future flag-flip. It only ever describes
// a build with no CUDA compiled in, which is permanently true for that concrete artifact
// regardless of what the release pipeline later publishes.
//
// cfg-gated to match its only call site (the `not(feature = "cuda")` arm of
// `detect_device_name` below) -- otherwise it is unreachable dead code under a `--features
// cuda` build and trips `clippy -D warnings` there.
#[cfg(not(feature = "cuda"))]
fn crossover_gpu_remediation_hint_no_cuda_build() -> String {
    "GPU (CUDA) acceleration is experimental and is not shipped in this build (compiled \
     without CUDA support), so it cannot run GPU calibration here. Run `tg doctor` to confirm \
     this build's native flavor, and see docs/gpu_crossover.md for the current GPU roadmap."
        .to_string()
}

// Distinct from the no-cuda-build hint above: this build DOES have CUDA support compiled in,
// so telling the caller to go "upgrade to get NVIDIA" would be nonsensical -- the problem is
// that the requested device id isn't among the enumerated devices (a hardware/driver/config
// problem), not a missing build.
//
// cfg-gated to match its only call site (the `feature = "cuda"` arm of `detect_device_name`
// below) -- otherwise it is unreachable dead code under a default (non-cuda) build and trips
// `clippy -D warnings` there.
#[cfg(feature = "cuda")]
fn crossover_gpu_remediation_hint_device_not_found() -> String {
    "Run `tg devices` to list routable GPU device IDs, or `tg doctor` to check GPU runtime \
     health, then retry calibration with a valid device id."
        .to_string()
}

/// Distinguishes "this build structurally cannot run GPU calibration because CUDA support
/// isn't compiled in" from every other calibration failure (a bad device id on a
/// cuda-enabled build, I/O errors, a failed benchmark subprocess, ...). `main.rs`'s
/// `handle_calibrate_command` downcasts an `anyhow::Error` on this type to decide whether its
/// `--json` flag should emit the machine-readable `calibration_status: skipped_no_cuda_build`
/// signal -- string-matching the rendered message would work too, but a distinct error kind
/// can't accidentally match an unrelated failure that happens to share wording. `reason` and
/// `remediation` are kept as separate fields (rather than one pre-joined string) so the
/// `--json` payload can expose them as two JSON fields without re-splitting formatted text.
#[derive(Debug)]
pub struct NoCudaBuildError {
    pub reason: String,
    pub remediation: String,
}

impl fmt::Display for NoCudaBuildError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        writeln!(f, "{}", self.reason)?;
        write!(f, "{}", self.remediation)
    }
}

impl std::error::Error for NoCudaBuildError {}

/// Builds the `--json` skip-signal payload for `handle_calibrate_command`'s no-cuda-build
/// path. Pulled out as a pure, allocation-only function (no `process::exit`, no I/O) so the
/// JSON shape is unit-testable without spawning a subprocess to observe the real exit code.
pub fn skip_signal_payload(err: &NoCudaBuildError) -> serde_json::Value {
    serde_json::json!({
        "calibration_status": "skipped_no_cuda_build",
        "reason": err.reason.clone(),
        "remediation": err.remediation.clone(),
    })
}

fn detect_device_name(device_id: i32) -> Result<String> {
    #[cfg(feature = "cuda")]
    {
        let devices = crate::gpu_native::enumerate_cuda_devices()
            .context("failed to enumerate CUDA devices")?;
        if let Some(device) = devices
            .into_iter()
            .find(|device| device.device_id == device_id)
        {
            return Ok(device.name);
        }
        bail!(
            "CUDA device {device_id} is unavailable for crossover calibration.\n{}",
            crossover_gpu_remediation_hint_device_not_found()
        );
    }

    #[cfg(not(feature = "cuda"))]
    {
        let _ = device_id;
        let no_cuda_error = NoCudaBuildError {
            reason: "crossover calibration requires a CUDA-enabled build.".to_string(),
            remediation: crossover_gpu_remediation_hint_no_cuda_build(),
        };
        Err(no_cuda_error.into())
    }
}

enum SearchMode {
    Cpu,
    Gpu(i32),
}

#[cfg(test)]
mod tests {
    use super::*;

    // Follow-up to #596 (CEO dogfood, v1.76.6): #596's remediation was honest in isolation
    // ("if published ... falls back to CPU when it is not") but still dangled
    // TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR=nvidia + `tg upgrade` as an obtainable path, when in
    // reality no NVIDIA-flavored asset has ever shipped (the release profile that builds one
    // is held off) -- a permanent dead end. This RED->GREEN test asserts the no-cuda-build
    // message states the honest, evergreen fact (GPU experimental / not shipped in this
    // build) instead of inviting an upgrade dance that always falls back to CPU today.
    //
    // detect_device_name is always-compiled (no `cuda` feature required), so this runs in the
    // default `test-rust-core` CI matrix (ubuntu/windows/macos x stable/nightly, `cargo test
    // --no-default-features`). The `#[cfg(feature = "cuda")]` arm is only compile-checked by
    // the separate `cuda-feature-check` job (`cargo check --features cuda`) -- never
    // test-executed here, which is an accepted pre-existing gap (council nit).
    #[cfg(not(feature = "cuda"))]
    #[test]
    fn detect_device_name_without_cuda_feature_states_experimental_not_upgrade_dead_end() {
        let err = detect_device_name(0).expect_err("a non-cuda build must fail closed");
        let message = err.to_string();
        assert!(
            !message.contains("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR"),
            "message must not invite the FLAVOR=nvidia override as an obtainable path: {message}"
        );
        assert!(
            !message.contains("tg upgrade"),
            "message must not invite `tg upgrade` as a way to get GPU support today: {message}"
        );
        assert!(
            message.contains("experimental"),
            "message should state GPU acceleration is experimental: {message}"
        );
        assert!(
            message.contains("not shipped in this build"),
            "message should honestly scope the claim to this build, not the whole release: \
             {message}"
        );
        assert!(
            message.contains("tg doctor"),
            "message should still point at `tg doctor` for diagnostics: {message}"
        );
    }

    // v20 dogfood follow-up (GPU honesty / harness-misread): `tg calibrate` on a CPU-only
    // build honestly fails closed (exit 2, see main.rs's `handle_calibrate_command`), but a
    // dogfood harness misread that honest message as a bare FAILURE instead of a SKIP. Fix:
    // `detect_device_name`'s no-cuda-build error must downcast to `NoCudaBuildError` (not just
    // render matching substrings, per the prior test above) so `--json` can emit a structured
    // `calibration_status: skipped_no_cuda_build` signal a harness can classify as SKIP. This
    // must stay distinguishable from the cuda-enabled device-not-found `bail!` above, which is
    // a real failure (bad device id), never a build-capability skip.
    #[cfg(not(feature = "cuda"))]
    #[test]
    fn detect_device_name_without_cuda_feature_downcasts_to_no_cuda_build_error() {
        let err = detect_device_name(0).expect_err("a non-cuda build must fail closed");
        let no_cuda = err
            .downcast_ref::<NoCudaBuildError>()
            .expect("non-cuda detect_device_name failure must be a NoCudaBuildError");
        assert_eq!(
            no_cuda.reason,
            "crossover calibration requires a CUDA-enabled build."
        );
        assert!(no_cuda.remediation.contains("not shipped in this build"));
        assert!(no_cuda.remediation.contains("experimental"));

        // Round-trip through Display must stay byte-identical to the pre-existing pinned
        // message (the test above) -- this is what `eprintln!("{err}")` still prints when
        // --json is not set.
        assert_eq!(
            err.to_string(),
            format!("{}\n{}", no_cuda.reason, no_cuda.remediation)
        );
    }

    // The JSON-formatting half of the same fix, isolated from `std::process::exit(2)` (which
    // `handle_calibrate_command` calls directly and so cannot be exercised in-process without
    // killing the test runner) -- asserts the pure payload-building helper produces the exact
    // structured shape a dogfood harness classifies as SKIP-not-FAIL.
    #[test]
    fn skip_signal_payload_reports_skipped_no_cuda_build() {
        let no_cuda = NoCudaBuildError {
            reason: "crossover calibration requires a CUDA-enabled build.".to_string(),
            remediation: "install a CUDA-enabled build and retry.".to_string(),
        };

        let payload = skip_signal_payload(&no_cuda);

        assert_eq!(
            payload["calibration_status"],
            serde_json::json!("skipped_no_cuda_build")
        );
        assert_eq!(payload["reason"], serde_json::json!(no_cuda.reason));
        assert_eq!(
            payload["remediation"],
            serde_json::json!(no_cuda.remediation)
        );
    }

    // Mirror check for the cuda-enabled arm: it must keep its OWN, scenario-correct
    // remediation (a device/driver/config problem, not a "no GPU build" problem) and must
    // never regress to claim GPU is "not shipped in this build" when the build plainly HAS
    // CUDA compiled in. #182 NIT-2 (honest coverage): unlike the production fn -- which IS
    // compile-checked via its real call site at :533 under `cargo check --features cuda` --
    // this `#[cfg(feature = "cuda")]` TEST fn is compiled by NO CI job (`cuda-feature-check`
    // runs `cargo check --features cuda` WITHOUT `--tests`, and `test-rust-core` is cuda-off),
    // so it is neither run NOR compile-checked in CI today; it is checked only by a local
    // `cargo test --features cuda`. Accepted gap: adding `--all-targets` to cuda-feature-check
    // would compile-check it but risks surfacing pre-existing cuda test debt in
    // main.rs/test_routing.rs -- deferred deliberately, not silently skipped.
    #[cfg(feature = "cuda")]
    #[test]
    fn crossover_gpu_remediation_hint_device_not_found_is_not_a_build_availability_claim() {
        let hint = crossover_gpu_remediation_hint_device_not_found();
        assert!(
            !hint.contains("not shipped in this build"),
            "a CUDA-enabled build must never be told GPU isn't shipped in it: {hint}"
        );
        assert!(hint.contains("tg devices") || hint.contains("tg doctor"));
    }
}
