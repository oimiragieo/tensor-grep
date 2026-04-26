from pathlib import Path

path = Path("rust_core/src/main.rs")
content = path.read_text(encoding="utf-8")

# 1. Update positional_ripgrep_args
content = content.replace(
    r"""        patterns: vec![pattern.to_string()],
        path: path.to_string(),
        pcre2: cli.pcre2,
        max_filesize: cli.max_filesize.clone(),
    }""",
    r"""        patterns: vec![pattern.to_string()],
        path: path.to_string(),
        pcre2: cli.pcre2,
        max_filesize: cli.max_filesize.clone(),
        no_ignore_vcs: cli.no_ignore_vcs,
    }""",
)

# 2. Update command_ripgrep_args
content = content.replace(
    r"""        patterns: request.patterns.clone(),
        path: request.path.clone(),
        pcre2: args.pcre2,
        max_filesize: args.max_filesize.clone(),
    }""",
    r"""        patterns: request.patterns.clone(),
        path: request.path.clone(),
        pcre2: args.pcre2,
        max_filesize: args.max_filesize.clone(),
        no_ignore_vcs: args.no_ignore_vcs,
    }""",
)

# 3. Fix parse_early_ripgrep_args (initialized with defaults)
content = content.replace(
    r"""        patterns: Vec::new(),
        path: String::new(),
    };""",
    r"""        patterns: Vec::new(),
        path: String::new(),
        no_ignore_vcs: false,
        pcre2: false,
        max_filesize: None,
    };""",
)

path.write_text(content, encoding="utf-8")
print("Successfully patched main.rs initializers.")
