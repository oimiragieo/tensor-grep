# Filename selection correction

DOGFOOD-FILENAME belongs to the ranking maintainer. It remains in implementation review until
exact-head CI and published-wheel replay complete. Scope is the approved filename preference,
not a new scoring model or the broader ranking campaign.

Implementation `1936f7c1db278da8a63b72caf098290d3f429366` promotes a complete multi-token filename
stem matching an ordered contiguous query phrase. The eligible production winner uses existing
score/path order. It moves before budgets, while exact/bridge symbol evidence wins, all numeric
scores stay unchanged, and other candidates retain order. Cached/session maps remain unmodified;
the preference is recalculated for each query. Language, test, vendor, generated, thin-wrapper,
and marker-to-implementation behavior are covered by focused controls.

The three original ranking pins passed before production edits and independently on the isolated
`99c1ea1` baseline. Final canonical-venv verification passed 68 tests covering the new suite,
lexical ranking, symbol priority, and repository-map targets. Ruff, preview formatting, full
Python mypy (174 files), file-size and bare-call ratchets passed. The first delivery candidate
failed the bare-call ratchet; the corrected module-qualified call passes without a new exception.
The builder's broader 242-case run hit its external 120-second timeout and gives no clearance.

A bounded smoke against the actual repository with one-file/one-symbol budgets selected
`src/tensor_grep/cli/repair_env.py`, `repair_env_command`, line 86 in both render and edit modes.
The isolated baseline instead selected `mcp_server.py` as its file and returned no render primary
symbol, while its edit symbol pointed to `repair_env_command`: the reported disagreement is
reproduced. These runs establish selection behavior, not a speedup or GPU claim.

[Raw commands, exits, and outputs](evidence/2026-10-05-dogfood/ranking.json) identify the source
artifact separately from the reused canonical compiled extension. Independent Sol implementation
review cleared `1936f7c`; later metadata edits need their own review. No release is claimed here.

## Integration onto merged diagnostics

Prior final head `fe12dc0a3e0f3e94881011b327af3398586eedb5` passed CI `37411489377`
with 38 terminal jobs and the full PR check rollup. Source
`f8eca776c5512983c83f30cf10e9b77b1355b1f8` replays its five-file delta onto actual
diagnostics merge `d0d9f7e960622f868a4a41c14c8d21a6e81ac1c8`. Every changed Git blob
equals the previously reviewed blob. These files do not overlap the subprocess change.

Canonical Windows verification on clean `f8eca776` passes 68 ranking tests, full Ruff,
preview formatting (1220 files), mypy (174 source files), unchanged size/bare ratchets,
and nine diagnostic/ranking source replay rows. Actual-repository render and edit both
choose `repair_env.py`, `repair_env_command`, line 86 under one-file/one-symbol budgets.
The first real-source replay exhausted a shared 60-second internal deadline and returned
no edit symbol; it is not clearance. The complete retry used a 90-second internal deadline
inside the unchanged external 120-second process bound. No production budget was changed.

Merge order remains diagnostics, subprocess, ranking, labels. Independently green heads
may share an ordered burst after the current diagnostics release completes. Labels will
also verify the combined source before that burst. This source evidence does not replace
new exact-head review/CI or the final published Windows wheel/native replay.
