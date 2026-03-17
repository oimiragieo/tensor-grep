use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::env;
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

pub fn load_crossover_config(search_root: Option<&Path>) -> Result<Option<(PathBuf, CrossoverConfig)>> {
    let path = resolve_crossover_config_path(search_root);
    if !path.is_file() {
        return Ok(None);
    }

    let bytes = fs::read(&path)
        .with_context(|| format!("failed to read crossover calibration file {}", path.display()))?;
    let config: CrossoverConfig = serde_json::from_slice(&bytes)
        .with_context(|| format!("failed to parse crossover calibration file {}", path.display()))?;
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
    fs::create_dir_all(&cache_dir)
        .with_context(|| format!("failed to create crossover cache directory {}", cache_dir.display()))?;

    let device_name = detect_device_name(DEFAULT_DEVICE_ID)?;
    let mut measurements = Vec::new();
    for size_bytes in DEFAULT_CALIBRATION_SIZES {
        let corpus_dir = ensure_cached_corpus(&cache_dir, size_bytes)?;
        let cpu_samples_ms = benchmark_mode(executable, &corpus_dir, SearchMode::Cpu)?;
        let gpu_samples_ms = benchmark_mode(executable, &corpus_dir, SearchMode::Gpu(DEFAULT_DEVICE_ID))?;
        measurements.push(CalibrationMeasurement::from_samples(
            size_bytes,
            cpu_samples_ms,
            gpu_samples_ms,
        )?);
    }

    summarize_measurements(device_name, measurements, now_timestamp)
}

pub fn write_crossover_config(config: &CrossoverConfig, search_root: Option<&Path>) -> Result<PathBuf> {
    let path = resolve_crossover_config_path(search_root);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).with_context(|| {
            format!(
                "failed to create parent directory for crossover config {}",
                path.display()
            )
        })?;
    }

    let bytes = serde_json::to_vec_pretty(config).context("failed to serialize crossover config")?;
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
                left_ratio.partial_cmp(&right_ratio).unwrap_or(std::cmp::Ordering::Equal)
            })
            .cloned()
            .unwrap()
    });

    let recommendation = if winner.is_some() {
        format!(
            "gpu_above_{}mb",
            representative.corpus_megabytes().max(1)
        )
    } else {
        "cpu_always".to_string()
    };

    Ok(CrossoverConfig {
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
    fn from_samples(size_bytes: u64, cpu_samples_ms: Vec<f64>, gpu_samples_ms: Vec<f64>) -> Result<Self> {
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
        let file = File::create(&file_path)
            .with_context(|| format!("failed to create cached corpus file {}", file_path.display()))?;
        let mut writer = BufWriter::new(file);
        let mut written = 0u64;
        while written < target_per_file {
            writer
                .write_all(chunk.as_bytes())
                .with_context(|| format!("failed to write cached corpus file {}", file_path.display()))?;
            written = written.saturating_add(chunk.len() as u64);
        }
        writer
            .flush()
            .with_context(|| format!("failed to flush cached corpus file {}", file_path.display()))?;
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
        return Some(PathBuf::from(path).join("tensor-grep").join("crossover.json"));
    }
    if let Some(path) = env::var_os("APPDATA") {
        return Some(PathBuf::from(path).join("tensor-grep").join("crossover.json"));
    }
    env::var_os("HOME").map(|home| {
        PathBuf::from(home)
            .join(".config")
            .join("tensor-grep")
            .join("crossover.json")
    })
}

fn detect_device_name(device_id: i32) -> Result<String> {
    #[cfg(feature = "cuda")]
    {
        let devices = crate::gpu_native::enumerate_cuda_devices().context("failed to enumerate CUDA devices")?;
        if let Some(device) = devices.into_iter().find(|device| device.device_id == device_id) {
            return Ok(device.name);
        }
        bail!("CUDA device {device_id} is unavailable for crossover calibration");
    }

    #[cfg(not(feature = "cuda"))]
    {
        let _ = device_id;
        bail!("crossover calibration requires a CUDA-enabled build")
    }
}

enum SearchMode {
    Cpu,
    Gpu(i32),
}
