# Portable subprocess guard fingerprints

The exact `80cc2100784416308e2514cf56ceca0a5b97902b` guard uses `ast.dump`, whose
serialization differs between Python versions. The newly collectable tests expose this:
the same 72 production calls yield 22 violations on Python 3.11 and 3.13, versus zero on
3.12. The production subprocess behavior is unchanged. No test skip is an acceptable fix.

Replace only the fingerprint serializer with a deterministic recursive representation:

1. Include each AST node's class name and sorted `ast.iter_fields` entries.
2. Preserve every present field recursively, including ordinary `None` and empty lists.
3. Omit only `type_params` whose value is exactly an empty list, matching its absence
   before Python 3.12. Nonempty `type_params` and `type_params=None` remain visible.
4. Preserve list order and scalar `repr`; source location attributes remain excluded.

Recompute pins without changing authority. Match each old/new source call one-to-one by
path, function, target and occurrence. Generated paths embed a changing parent launch
hash: first map the two outer launches, then translate each generated path and
`generated_from` through that exact parent map. Require 72 calls, 12 generated calls,
16 decoding calls and seven exceptions. Operations, options, policies, rationale,
locations and ordinals must match. Every migrated exception retains all five exact key
components and its rationale, and must still be consumed once. No new exception is added.

Add controls proving absent/empty type parameters agree; nonempty and `None` type
parameters differ; ordinary `None`/empty-list mutations differ; field ordering and
location changes do not affect identity. Existing alias, wrapper, generated-helper,
positional-option and manifest mutation controls remain required.

Before changing pins, preserve the failing 3.11 and 3.13 controls. Afterward, run the
stdlib production scan on installed 3.11, canonical 3.12 and installed 3.13, comparing
full inventories and exact pins as well as zero violations and the population. Run the
canonical guard tests through the console pytest entrypoint, focused Ruff/preview and
the unchanged size gate. New exact-head independent and Opus reviews plus full CI are
required before merge. These source checks are not published-wheel clearance.

The independent Sol plan review approved this approach, including the generated-parent
mapping and full cross-interpreter inventory comparison. Hash method: SHA-256 of these
canonical worktree file bytes; the review receipt records the concrete digest.
