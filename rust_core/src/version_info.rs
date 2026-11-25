use std::ffi::OsString;

pub(crate) fn version_invocation_verbose(raw_args: &[OsString]) -> Option<bool> {
    let args: Vec<&str> = raw_args
        .iter()
        .skip(1)
        .filter_map(|arg| arg.to_str())
        .collect();
    if args.len() != raw_args.len().saturating_sub(1) {
        return None;
    }

    let tail = match args.as_slice() {
        ["search", rest @ ..] => rest,
        other => other,
    };

    match tail {
        ["--version"] | ["-V"] => Some(false),
        [a, b]
            if matches!(*a, "--version" | "-V") && matches!(*b, "--verbose" | "-v")
                || matches!(*a, "--verbose" | "-v") && matches!(*b, "--version" | "-V") =>
        {
            Some(true)
        }
        _ => None,
    }
}

pub(crate) fn target_triple() -> &'static str {
    if cfg!(all(
        target_arch = "x86_64",
        target_os = "windows",
        target_env = "msvc"
    )) {
        "x86_64-pc-windows-msvc"
    } else if cfg!(all(
        target_arch = "x86_64",
        target_os = "windows",
        target_env = "gnu"
    )) {
        "x86_64-pc-windows-gnu"
    } else if cfg!(all(
        target_arch = "aarch64",
        target_os = "windows",
        target_env = "msvc"
    )) {
        "aarch64-pc-windows-msvc"
    } else if cfg!(all(
        target_arch = "x86_64",
        target_os = "linux",
        target_env = "gnu"
    )) {
        "x86_64-unknown-linux-gnu"
    } else if cfg!(all(
        target_arch = "x86_64",
        target_os = "linux",
        target_env = "musl"
    )) {
        "x86_64-unknown-linux-musl"
    } else if cfg!(all(
        target_arch = "aarch64",
        target_os = "linux",
        target_env = "gnu"
    )) {
        "aarch64-unknown-linux-gnu"
    } else if cfg!(all(target_arch = "x86_64", target_os = "macos")) {
        "x86_64-apple-darwin"
    } else if cfg!(all(target_arch = "aarch64", target_os = "macos")) {
        "aarch64-apple-darwin"
    } else {
        std::env::consts::ARCH
    }
}

pub(crate) fn runtime_simd_line() -> String {
    #[cfg(any(target_arch = "x86", target_arch = "x86_64"))]
    {
        let sse42 = if is_x86_feature_detected!("sse4.2") {
            "+sse4.2"
        } else {
            "-sse4.2"
        };
        let avx2 = if is_x86_feature_detected!("avx2") {
            "+avx2"
        } else {
            "-avx2"
        };
        format!("simd(runtime):{sse42},{avx2}")
    }
    #[cfg(not(any(target_arch = "x86", target_arch = "x86_64")))]
    {
        format!("simd(runtime):arch={}", std::env::consts::ARCH)
    }
}

pub(crate) fn format_version_output(verbose: bool) -> String {
    let header = format!("tg {}", env!("CARGO_PKG_VERSION"));
    if !verbose {
        return header;
    }

    let cuda_flag = if cfg!(feature = "cuda") {
        "+cuda"
    } else {
        "-cuda"
    };
    let frontdoor = if cfg!(feature = "cuda") {
        "cuda"
    } else {
        "cpu"
    };

    format!(
        "{header}\n\nfeatures:{cuda_flag}\nfrontdoor:{frontdoor}\ntarget:{}\n{}",
        target_triple(),
        runtime_simd_line()
    )
}

pub(crate) fn print_version(verbose: bool) {
    println!("{}", format_version_output(verbose));
}

#[cfg(test)]
mod tests {
    use super::*;

    fn os_args(items: &[&str]) -> Vec<OsString> {
        items.iter().map(OsString::from).collect()
    }

    #[test]
    fn version_verbose_invocation_parses_top_level_and_search_variants() {
        assert_eq!(
            version_invocation_verbose(&os_args(&["tg", "--version"])),
            Some(false)
        );
        assert_eq!(
            version_invocation_verbose(&os_args(&["tg", "-V"])),
            Some(false)
        );
        assert_eq!(
            version_invocation_verbose(&os_args(&["tg", "--version", "--verbose"])),
            Some(true)
        );
        assert_eq!(
            version_invocation_verbose(&os_args(&["tg", "-V", "-v"])),
            Some(true)
        );
        assert_eq!(
            version_invocation_verbose(&os_args(&["tg", "--verbose", "--version"])),
            Some(true)
        );
        assert_eq!(
            version_invocation_verbose(&os_args(&["tg", "search", "--verbose", "-V"])),
            Some(true)
        );
        assert_eq!(
            version_invocation_verbose(&os_args(&["tg", "search", "needle"])),
            None
        );
    }

    #[test]
    fn version_verbose_output_reports_honest_features_and_target() {
        let short = format_version_output(false);
        assert_eq!(short, format!("tg {}", env!("CARGO_PKG_VERSION")));

        let verbose = format_version_output(true);
        assert!(verbose.starts_with(&format!("tg {}\n\n", env!("CARGO_PKG_VERSION"))));
        if cfg!(feature = "cuda") {
            assert!(verbose.contains("features:+cuda\n"));
            assert!(verbose.contains("frontdoor:cuda\n"));
        } else {
            assert!(verbose.contains("features:-cuda\n"));
            assert!(verbose.contains("frontdoor:cpu\n"));
        }
        assert!(verbose.contains(&format!("target:{}\n", target_triple())));
        assert!(verbose.contains("simd(runtime):"));
    }
}
