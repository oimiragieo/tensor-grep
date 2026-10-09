//! Hard-coded CPU runtime library pins, checked again before native library loading.

#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
pub const LIBRARIES: &[(&str, usize, &str)] = &[(
    "libonnxruntime.1.24.4.dylib",
    27708456,
    "f7a4418c1fbbb12705f26991c52cd9e4a90c54d4802c487d0facb20897790fe9",
)];

#[cfg(all(target_os = "linux", target_arch = "aarch64"))]
pub const LIBRARIES: &[(&str, usize, &str)] = &[(
    "libonnxruntime.so.1.24.4",
    18625376,
    "f373ae5fd4d878ed17ebea26e5480bd3d0a15e3bd6bc94b878e6c1b73998d22a",
)];

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
pub const LIBRARIES: &[(&str, usize, &str)] = &[(
    "libonnxruntime.so.1.24.4",
    22159232,
    "2d518e11c7767f9fdc4413bdbeb45acf30db504ff76a82fc19d9238a3f9f6c24",
)];

#[cfg(all(target_os = "windows", target_arch = "x86_64"))]
pub const LIBRARIES: &[(&str, usize, &str)] = &[
    (
        "onnxruntime.dll",
        16119840,
        "ef720fc44a4ea48626bfe1ebd29642de20222d7f104a509ea305d9f3cb3b7850",
    ),
    (
        "onnxruntime_providers_shared.dll",
        22040,
        "f123ce89224771bfeaf60a21cec31672985b731c312541da82bafaf38933f02c",
    ),
];

#[cfg(all(target_os = "windows", target_arch = "aarch64"))]
pub const LIBRARIES: &[(&str, usize, &str)] = &[
    (
        "onnxruntime.dll",
        15476760,
        "a5842307bc58e562997053a59a8764ff0936b6738b5502a6103d084b1ee834fa",
    ),
    (
        "onnxruntime_providers_shared.dll",
        21536,
        "fe06c0c7122f560e02022d1f122e271ed882d65de87b8a420cd9b38250bd3be2",
    ),
];

#[cfg(not(any(
    all(target_os = "macos", target_arch = "aarch64"),
    all(target_os = "linux", target_arch = "aarch64"),
    all(target_os = "linux", target_arch = "x86_64"),
    all(target_os = "windows", target_arch = "x86_64"),
    all(target_os = "windows", target_arch = "aarch64")
)))]
pub const LIBRARIES: &[(&str, usize, &str)] = &[];
