from pathlib import Path

path = Path("rust_core/src/main.rs")
content = path.read_text(encoding="utf-8")

content = content.replace(
    r"""                                patterns: params.patterns.to_vec(),
                                path: params.path.to_string(),
                            });""",
    r"""                                patterns: params.patterns.to_vec(),
                                path: params.path.to_string(),
                                no_ignore_vcs: false,
                                pcre2: false,
                                max_filesize: None,
                            });""",
)

path.write_text(content, encoding="utf-8")
print("Successfully patched handle_auto_gpu_search fallback.")
