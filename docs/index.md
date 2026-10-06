# tensor-grep documentation

`tensor-grep` (`tg`) helps you search text and code, inspect relationships between symbols, and prepare bounded context for a code change. Start with the [first-search guide](getting-started.md) if you are new to the command line.

## Choose a task

- Find words or a pattern in logs and files: [Getting started](getting-started.md#search-a-sample-log).
- Find code by its structure, such as a function call: [AST search](getting-started.md#search-code-structure).
- See likely important files or gather context for a change: [Orientation and context](getting-started.md#choose-a-next-task).
- Reuse a project snapshot across requests: [Sessions](getting-started.md#reuse-a-session).
- Save and restore one file during an edit: [Checkpoints](getting-started.md#save-a-checkpoint-before-an-edit).
- Install `tg` or check platform support: [Installation](installation.md) and [support matrix](SUPPORT_MATRIX.md).

## Use and integration

- [Experimental features](EXPERIMENTAL.md) describes optional features, their setup, and limits.
- [Cache management](runbooks/cache-management.md) distinguishes search indexes, AST data, sessions, and checkpoints.
- [Harness API](harness_api.md) documents machine-readable command contracts.
- [Harness cookbook](harness_cookbook.md) gives task examples for AI and editor integrations.
- [Harness API](harness_api.md) documents the CLI and MCP machine-readable interfaces; [session daemon](session_daemon_protocol.md) covers repeated session requests.

## How it works

[Architecture](architecture.md) maps the Rust and Python entry points, search engines, caches, and source directories. For the maintained search-routing rules, see [routing policy](routing_policy.md). For compatibility promises, see [contracts](CONTRACTS.md).

## Maintainers and operators

- [Benchmarks](benchmarks.md) records accepted comparisons and their limits.
- [CI pipeline](CI_PIPELINE.md), [release checklist](RELEASE_CHECKLIST.md), and [package publishing](package_manager_publish.md) cover delivery.
- [Hotfix procedure](HOTFIX_PROCEDURE.md) and [enterprise review bundle](enterprise_review_bundle_ci.md) cover operational workflows.
- [GPU troubleshooting](runbooks/gpu-troubleshooting.md) and [resident worker runbook](runbooks/resident-worker.md) cover optional paths.
