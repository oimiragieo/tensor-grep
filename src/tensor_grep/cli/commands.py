# `KNOWN_COMMANDS` is the shared top-level command contract read by both the
# Python Typer application and the Rust native front-door. Python-only help
# routing hints live here too, but Rust maintains its own narrower help probe.

PYTHON_FULL_HELP_COMMANDS = {
    "search",
}

KNOWN_COMMANDS = {
    "search",
    "calibrate",
    "upgrade",
    "update",
    "audit-verify",
    "mcp",
    "classify",
    "run",
    "scan",
    "test",
    "ast-info",
    "new",
    "worker",
    "defs",
    "refs",
    "source",
    "impact",
    "callers",
    "blast-radius",
    "blast-radius-render",
    "blast-radius-plan",
    "edit-plan",
    "context-render",
    "rulesets",
    "audit-history",
    "audit-diff",
    "review-bundle",
    "devices",
    "context",
    "lsp",
    "lsp-setup",
    "__gpu-native-stats",
    "__gpu-transfer-bench",
    "__gpu-cuda-graphs",
    "__gpu-oom-probe",
    "map",
    "session",
    "doctor",
    "checkpoint",
}
