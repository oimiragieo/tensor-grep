# `KNOWN_COMMANDS` is the shared top-level command contract read by both the
# Python Typer application and the Rust native front-door. Python-only help
# routing hints live here too, but Rust maintains its own narrower help probe.

PYTHON_FULL_HELP_COMMANDS = {
    "search",
}

KNOWN_COMMANDS = {
    "agent",
    "search",
    "calibrate",
    "upgrade",
    "update",
    "repair-launcher",
    "audit",
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
    "imports",
    "importers",
    "find",
    "blast-radius",
    "blast-radius-render",
    "blast-radius-plan",
    "diff-impact",
    "edit-plan",
    "context-render",
    "route-test",
    "prepare",
    "rulesets",
    "audit-history",
    "audit-diff",
    "review-bundle",
    "evidence",
    "ledger",
    "devices",
    "context",
    "lsp",
    "lsp-setup",
    "__gpu-native-stats",
    "__gpu-transfer-bench",
    "__gpu-cuda-graphs",
    "__gpu-oom-probe",
    "map",
    "orient",
    "codemap",
    "inventory",
    "docs-coverage",
    "session",
    "doctor",
    "checkpoint",
    "dogfood",
    "install-dense",
    "install",
    "uninstall",
}

# `RESERVED_TOP_LEVEL_COMMANDS` = roadmap commands that DO NOT EXIST yet (A90). They must never
# be faked by the search fall-through (`tg edit-ready --json` must exit 2 with
# `error.code=unknown_command`, not search for "edit-ready"), and they must never be treated as
# registered commands either -- a reserved name is NOT in KNOWN_COMMANDS, and the A90 lifecycle
# invariant `RESERVED ∩ KNOWN == ∅` (pinned by test) fails the build if a name lands in both.
# Roadmap source: `docs/plans/2026-08-09-worldclass-roadmap.md` (edit-ready / verify-edit /
# workspace are the phase-2 edit-control-plane commands). When a reserved command is REALIZED,
# this entry is REMOVED in the same PR that registers it (KNOWN_COMMANDS + the 4 command sites +
# the parity test) -- the lifecycle gate fails if a KNOWN_COMMANDS addition leaves its reserved
# twin behind.
RESERVED_TOP_LEVEL_COMMANDS = {
    "edit-ready",
    "verify-edit",
    "workspace",
}
