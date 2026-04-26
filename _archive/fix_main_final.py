from pathlib import Path

path = Path("rust_core/src/main.rs")
content = path.read_text(encoding="utf-8")

# 1. Remove duplicate no_ignore_vcs
content = content.replace(
    r"""        path: path.to_string(),
        pcre2: cli.pcre2,
        max_filesize: cli.max_filesize.clone(),
        no_ignore_vcs: cli.no_ignore_vcs,
    }""",
    r"""        path: path.to_string(),
        pcre2: cli.pcre2,
        max_filesize: cli.max_filesize.clone(),
    }""",
)

content = content.replace(
    r"""        path: request.path.clone(),
        pcre2: args.pcre2,
        max_filesize: args.max_filesize.clone(),
        no_ignore_vcs: args.no_ignore_vcs,
    }""",
    r"""        path: request.path.clone(),
        pcre2: args.pcre2,
        max_filesize: args.max_filesize.clone(),
    }""",
)

# 2. Add missing pcre2 to SearchRoutingConfig
content = content.replace(
    r"""            gpu_auto_supported,
            prefer_rg_passthrough: search_has_context(&args) && !args.json && !args.ndjson,
        },""",
    r"""            gpu_auto_supported,
            prefer_rg_passthrough: search_has_context(&args) && !args.json && !args.ndjson,
            pcre2: args.pcre2,
        },""",
)

path.write_text(content, encoding="utf-8")
print("Successfully fixed main.rs duplicate fields and missing pcre2.")
