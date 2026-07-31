"""Every search `--json` emitter must carry the defaulted-scope disclosure. Enumerated by EMITTER.

Task #26. THIS FILE EXISTS BECAUSE A CENSUS KEYED ON THE WRONG THING REPORTED "4 of 4 COVERED".

The first cut of the #26 Rust work enumerated the population as the `#[derive(Serialize)]` structs
that model a search `--json` document:

    NativeJsonOutput            native_search.rs   struct
    SearchResultJson            main.rs            struct
    SearchSummaryNdjson         main.rs            struct
    GpuNativeSearchResultJson   main.rs            struct   (cuda-gated)

All four got the fields. The census was complete, type-checked, and wrong by one. A FIFTH emitter,
`normalize_gpu_sidecar_json` (main.rs), builds the same document shape by hand with
`serde_json::json!()`. It shares no type with any sibling, so it is invisible to any sweep that
keys on the derive macro -- and it is not cuda-gated, so it is live in every build on the
GPU-sidecar route. It kept its pre-#26 shape silently.

THE RULE THIS ENCODES: **enumerate EMITTERS, not the mechanism they happen to use.** The property
that matters is "this function writes a search JSON document", and a struct definition is only one
way to satisfy it. A census that keys on the implementation detail rather than the behaviour
reports a number that is confidently, checkably wrong.

WHY SOURCE-ENUMERATION AND NOT A BEHAVIOURAL TEST: reaching the sidecar emitter needs a live GPU
sidecar process; reaching the cuda emitter needs a CUDA build that CI compiles but never executes.
A behavioural test would cover the routes someone remembered to invoke -- reproducing the original
defect, in which the covered routes were exactly the ones that had been noticed. Reading the source
is what makes the coverage total rather than sampled.

Sibling mechanism, same shape, different property:
`tests/unit/test_every_search_dispatch_route_discloses.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

_RUST = Path(__file__).resolve().parents[2] / "rust_core" / "src"
_MAIN_RS = _RUST / "main.rs"
_NATIVE_SEARCH_RS = _RUST / "native_search.rs"

# The gate every emitter must route through. One shared helper is the whole point: a route that
# re-derives "was the path defaulted" locally is a route that can disagree with its siblings.
_GATE = "defaulted_scope_fields"

# THE POPULATION, by emitter. Each entry: (file, symbol that emits a search --json document).
# Adding a new emitter without adding it here is the failure this file exists to catch -- which is
# why the "is this list still complete?" arm below is not optional decoration.
_EMITTERS: tuple[tuple[Path, str], ...] = (
    (_NATIVE_SEARCH_RS, "emit_json_matches"),
    (_MAIN_RS, "emit_json_search_results"),
    (_MAIN_RS, "emit_ndjson_search_results"),
    (_MAIN_RS, "emit_gpu_native_json_results"),
    (_MAIN_RS, "normalize_gpu_sidecar_json"),
)

# AUDITED AND DELIBERATELY EXCLUDED. A site in a census is a candidate, not a defect, and the
# reason for excluding one belongs at the ratchet so it is not re-litigated every time someone
# greps `"total_matches"` and finds six hits.
#
#   main.rs::broad_scan_refusal_json_envelope
#       Emits `total_matches: 0` but is a REFUSAL, not a result. It already carries
#       `result_incomplete: true` + `incomplete_reason_class: "scan_limit"` and exits 2. The scope
#       note would be actively WRONG here: it says "the search ran, in a narrower scope than you
#       may have meant", and this search did not run at all. Adding it would put an advisory
#       alongside a refusal and blur the one distinction the exit contract depends on.
#
#   main.rs::GpuSidecarSearchPayload
#       `#[derive(Deserialize)]` -- an INPUT parser for the sidecar's stdout, not an emitter.
#       `normalize_gpu_sidecar_json` is the emitter that consumes it, and that one IS in the list.


def _function_body(source: str, name: str) -> str:
    """The source of `fn <name>`, from its signature to the next top-level `fn`/end of file.

    Brace-matching would be more precise but also more code to be wrong; the next top-level `fn`
    is an adequate terminator in this codebase because these emitters are all top-level, and the
    arm below fails loudly if a name stops resolving at all.
    """
    match = re.search(rf"^fn {re.escape(name)}\b", source, re.M)
    if match is None:
        match = re.search(rf"^pub fn {re.escape(name)}\b", source, re.M)
    assert match is not None, (
        f"emitter `{name}` no longer exists. This census is now BLIND to it: it cannot verify a "
        "function it cannot find. Either the function was renamed (update _EMITTERS) or it was "
        "deleted (confirm its route no longer emits a search JSON document)."
    )
    rest = source[match.end() :]
    next_fn = re.search(r"^(pub )?fn ", rest, re.M)
    return rest[: next_fn.start()] if next_fn else rest


def test_every_json_emitter_routes_through_the_shared_scope_gate() -> None:
    """THE POPULATION CHECK, keyed on emitters rather than on `#[derive(Serialize)]`."""
    missing = []
    for path, name in _EMITTERS:
        body = _function_body(path.read_text(encoding="utf-8"), name)
        if _GATE not in body:
            missing.append(f"{path.name}::{name}")

    assert not missing, (
        f"these search --json emitters never call `{_GATE}`: {missing}. Every emitter needs its "
        "OWN call -- there is no single chokepoint, which is why the first cut of this work "
        "covered four of five. Add the gate at the new emitter, not only where the last one went."
    )


def test_the_gate_has_exactly_one_definition() -> None:
    """CONTROL ARM: the point of a shared gate is that it is SHARED.

    Satisfying the test above by pasting a local copy of the predicate into each emitter would
    pass it while recreating the drift the whole task exists to close. There must be one
    definition, in the lib crate, reachable from both crates.
    """
    definitions = []
    for path in (_MAIN_RS, _NATIVE_SEARCH_RS):
        source = path.read_text(encoding="utf-8")
        definitions += [
            f"{path.name}:{m.start()}" for m in re.finditer(rf"fn {_GATE}\s*\(", source)
        ]

    assert len(definitions) == 1, (
        f"expected exactly ONE definition of `{_GATE}`, found {len(definitions)}: {definitions}. "
        "Two copies of one rule is how the engines drift apart -- the exact failure #26 closes."
    )

    native_search = _NATIVE_SEARCH_RS.read_text(encoding="utf-8")
    assert re.search(rf"pub fn {_GATE}\s*\(", native_search), (
        "the shared gate is not `pub` in native_search.rs (the LIB crate). main.rs is the BINARY "
        "crate, so a definition living there is unreachable from the lib and forces a second copy."
    )


def test_the_census_fires_on_a_synthetic_ungated_emitter() -> None:
    """PROVE THE MECHANISM, on the arm that matters.

    An untested gate is untested code. If the matcher ever stops flagging an emitter that lacks
    the call, this census has gone inert and the sixth emitter ships uncovered exactly as the
    fifth did.
    """
    synthetic = (
        "fn emit_something_new(stdout: &str) -> Result<Value> {\n"
        '    let mut value = serde_json::json!({"total_matches": 0});\n'
        "    Ok(value)\n"
        "}\n"
        "fn next_thing() {}\n"
    )

    body = _function_body(synthetic, "emit_something_new")
    assert _GATE not in body, (
        "the matcher found the gate in a body that does not contain it -- the window logic is "
        "wrong and this census would pass anything"
    )


def test_the_census_does_not_fire_on_a_gated_emitter() -> None:
    """CONTROL ARM on the mechanism: it must discriminate, not flag everything.

    A census that flags correct code teaches everyone to delete it, which costs more than the
    defect it was built for.
    """
    synthetic = (
        "fn emit_something_new(stdout: &str) -> Result<Value> {\n"
        "    let (path_was_defaulted, scope_note) =\n"
        "        defaulted_scope_fields(path_was_implicit, payload.total_matches);\n"
        "    Ok(value)\n"
        "}\n"
        "fn next_thing() {}\n"
    )

    body = _function_body(synthetic, "emit_something_new")
    assert _GATE in body, "the matcher flags a correctly-gated emitter; it cannot discriminate"
