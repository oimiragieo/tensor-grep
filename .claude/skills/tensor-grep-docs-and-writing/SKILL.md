---
name: tensor-grep-docs-and-writing
description: Write public tensor-grep documentation with verified commands, clear explanations, and accurate feature claims.
---

# Write product documentation

Write for a reader who can use a terminal but may be new to code analysis.
Explain unfamiliar terms at first use and provide small, copyable examples.

## Match the document to its purpose

- `README.md`: product benefits, a short working example, installation, and next steps.
- `docs/getting-started.md`: a disposable practice project and a guided first task.
- `docs/architecture.md`: how commands reach their implementations and stored state.
- `docs/harness_api.md` and `docs/CONTRACTS.md`: public inputs, outputs, and guarantees.
- `docs/EXPERIMENTAL.md`: optional or experimental behavior and limitations.

Verify examples against command registration and current help. Distinguish native
and Python entry points, required extras, and platform-specific quoting. Show an
explicit path and explain placeholder values. Restore examples must use disposable
files and returned checkpoint IDs.

## Keep claims supportable

Describe what a feature helps a user do. Do not imply universal speedups,
complete call graphs, guaranteed edit correctness, or GPU execution from a
fallback result. Link detailed contracts rather than copying large specifications
into every guide.

Keep private operating procedures, personal paths, unpublished results, model
assignments, and internal strategy out of public docs and skills. New files under
`.claude` require an explicit publication decision.

## Verify the result

Check relative links, command syntax, navigation in `mkdocs.yml`, and applicable
documentation-governance tests. Build the site with `mkdocs build --strict` in an
environment with MkDocs Material installed. Review the final diff for stale
counts, unsupported promises, and internal material.
