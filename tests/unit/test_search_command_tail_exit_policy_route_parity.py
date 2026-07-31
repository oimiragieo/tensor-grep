"""Every `search_command` TAIL exit-code policy must be classified: reachable on both the
delegated and non-delegated routes, or explicitly annotated as route-specific.

Background (task 22 investigation, 2026-07-31): PR #868 added a new exit-code rule
(`gpu_request_unhonoured`) to the very end of `search_command`, ~530 lines after the
native-delegation exit (`sys.exit(_delegate_to_native_tg_search(...))`). That rule is keyed on
`SearchConfig.gpu_device_ids`, which is ELIGIBLE for delegation (one of the OR triggers
`_can_delegate_to_native_tg_search` accepts, not a member of the refuse-tuple
`_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`) -- so a request that fires the new rule can ALSO
take the native-delegation route entirely, where this module's tail code (and therefore the new
rule) never runs at all. Whether the native binary applies an equivalent rule on ITS exit paths
is a separate, contract-contested question (see PR #868 / the CI diagnostic added alongside this
file) this Python-only test cannot answer by inspecting Rust source at import time.

This is the THIRD instance of one class (per AGENTS.md / `tensor-grep-architecture-contract`):
a rule implemented on the Python route while native delegation bypasses it. The existing sibling
test `tests/unit/test_native_delegation_field_coverage.py` already guards the analogous
"output-shaping SearchConfig field silently dropped by delegation" class (#25/#336) with a
source-derived enumeration; this file guards the sibling "exit-code POLICY silently
route-specific" class the same way -- by enumerating
`tensor_grep.cli.main._SEARCH_COMMAND_TAIL_EXIT_CODE_POLICIES` and requiring every entry to
either cite a verified native mirror or an explicit, non-empty route-specific reason. It cannot
verify the Rust side's actual behaviour (that requires building and running the native binary,
which is out of scope for a Python unit test and forbidden on this box) -- it can only make the
CLASSIFICATION itself mandatory and auditable, so a new tail-only rule cannot be added silently.
"""

from __future__ import annotations

import dataclasses

from tensor_grep.cli import main as tg_main
from tensor_grep.cli.main import _TailExitCodePolicy


def _classify(policy: _TailExitCodePolicy, refuse_tuple: frozenset[str]) -> list[str]:
    """Return the list of problems with `policy`'s classification; empty means it is sound.

    A policy is soundly classified when EXACTLY ONE of `mirrored_in_native_at` /
    `route_specific_reason` is a non-empty string. Trigger-field membership in the
    delegation refuse-tuple is informational only (recorded via `test_*_reason_still_applies`
    below) -- it does not exempt an entry from needing a citation, so every policy stays
    self-documenting regardless of how the refuse-tuple evolves.
    """
    del refuse_tuple  # reserved for the staleness checks below; not consulted for soundness
    problems: list[str] = []
    has_mirror = bool(policy.mirrored_in_native_at and policy.mirrored_in_native_at.strip())
    has_reason = bool(policy.route_specific_reason and policy.route_specific_reason.strip())
    if has_mirror and has_reason:
        problems.append(
            f"{policy.name!r}: both mirrored_in_native_at and route_specific_reason are set -- "
            "a policy cannot simultaneously claim verified native parity AND be an "
            "acknowledged route-specific gap. Pick one."
        )
    if not has_mirror and not has_reason:
        problems.append(
            f"{policy.name!r}: neither mirrored_in_native_at nor route_specific_reason is set. "
            "Every search_command tail exit-code policy must be classified: either cite where "
            "a human verified the native binary applies the identical rule "
            "(mirrored_in_native_at), or explain why it is knowingly Python-route-only "
            "(route_specific_reason) with a tracking issue/PR."
        )
    return problems


class TestRealRegistryIsFullyClassified:
    """The GREEN arm: today's actual registry must classify cleanly."""

    def test_every_registered_policy_is_classified(self) -> None:
        refuse_tuple = frozenset(tg_main._NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS)
        problems = [
            problem
            for policy in tg_main._SEARCH_COMMAND_TAIL_EXIT_CODE_POLICIES
            for problem in _classify(policy, refuse_tuple)
        ]
        assert not problems, (
            "Unclassified (or mis-classified) search_command tail exit-code polic"
            f"{'y' if len(problems) == 1 else 'ies'}:\n" + "\n".join(problems)
        )

    def test_registry_is_non_empty(self) -> None:
        # A future refactor that accidentally clears the tuple must not silently make the two
        # tests above vacuously pass on an empty registry.
        assert len(tg_main._SEARCH_COMMAND_TAIL_EXIT_CODE_POLICIES) >= 2

    def test_no_duplicate_policy_names(self) -> None:
        names = [policy.name for policy in tg_main._SEARCH_COMMAND_TAIL_EXIT_CODE_POLICIES]
        assert len(names) == len(set(names)), f"duplicate policy name(s) in registry: {names}"


class TestSyntheticViolationsAreCaught:
    """The RED arm: a made-up, wrongly-classified entry must be flagged. Constructed here (never
    added to the real registry), so this proves the checker can fail before trusting that the
    green arm above means anything."""

    def test_synthetic_policy_with_no_classification_is_flagged(self) -> None:
        bad = _TailExitCodePolicy(
            name="synthetic_unclassified",
            trigger_fields=frozenset({"json_mode"}),
        )
        problems = _classify(bad, frozenset())
        assert problems, "an entirely unclassified synthetic policy must be flagged"
        assert "synthetic_unclassified" in problems[0]

    def test_synthetic_policy_with_both_fields_set_is_flagged(self) -> None:
        bad = _TailExitCodePolicy(
            name="synthetic_contradictory",
            trigger_fields=frozenset({"ndjson"}),
            mirrored_in_native_at="rust_core/src/main.rs:1 fake citation",
            route_specific_reason="also claims to be a gap, contradicting the citation above",
        )
        problems = _classify(bad, frozenset())
        assert problems, (
            "a policy claiming BOTH native parity and a route-specific gap must be flagged"
        )
        assert "synthetic_contradictory" in problems[0]

    def test_synthetic_policy_with_blank_strings_is_flagged(self) -> None:
        # Whitespace-only strings must not satisfy the citation requirement -- this is the same
        # "cosmetic non-empty string that carries no information" trap a bare `True` would be.
        bad = _TailExitCodePolicy(
            name="synthetic_blank",
            trigger_fields=frozenset(),
            route_specific_reason="   ",
        )
        problems = _classify(bad, frozenset())
        assert problems, "a whitespace-only route_specific_reason must not count as a real citation"

    def test_synthetic_properly_mirrored_policy_is_not_flagged(self) -> None:
        # Positive control alongside the negative ones above: a well-formed mirrored entry must
        # NOT be flagged, proving `_classify` discriminates rather than always failing.
        good = _TailExitCodePolicy(
            name="synthetic_mirrored",
            trigger_fields=frozenset(),
            mirrored_in_native_at="rust_core/src/main.rs:1 fake but well-formed citation",
        )
        assert _classify(good, frozenset()) == []

    def test_synthetic_properly_route_specific_policy_is_not_flagged(self) -> None:
        good = _TailExitCodePolicy(
            name="synthetic_route_specific",
            trigger_fields=frozenset({"force_cpu"}),
            route_specific_reason="tracked as backlog #999, deliberately Python-only for now",
        )
        assert _classify(good, frozenset()) == []


class TestGpuRequestUnhonouredReasonStaysCurrent:
    """Staleness guard (mirrors the sibling file's `test_known_gap_has_no_stale_entries`): if a
    future change moves `gpu_device_ids` into the delegation refuse-tuple, or a native mirror
    gets implemented and someone flips this entry to `mirrored_in_native_at` without actually
    removing the trigger, the STATED REASON for the gap ("gpu_device_ids is delegation-eligible,
    not refused") would go stale. This test fails loudly rather than let the registry entry keep
    citing a reason that is no longer true."""

    def test_trigger_field_is_still_delegation_eligible_not_refused(self) -> None:
        policy = next(
            p
            for p in tg_main._SEARCH_COMMAND_TAIL_EXIT_CODE_POLICIES
            if p.name == "gpu_request_unhonoured"
        )
        refuse_tuple = frozenset(tg_main._NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS)
        assert policy.trigger_fields, "gpu_request_unhonoured must declare its trigger field(s)"
        overlap = policy.trigger_fields & refuse_tuple
        assert not overlap, (
            f"gpu_request_unhonoured's stated route_specific_reason claims {sorted(policy.trigger_fields)} "
            f"is delegation-eligible (not refused), but {sorted(overlap)} is now in "
            "_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS -- the reason is STALE. Either the "
            "route-ambiguity no longer exists (delegation now refuses whenever this policy could "
            "fire, so the gap may be closeable) or the refuse-tuple change was a mistake; update "
            "this registry entry's reason either way, do not leave a stale justification."
        )
        assert policy.route_specific_reason, (
            "gpu_request_unhonoured is still delegation-eligible and must stay classified as "
            "route-specific until the native mirror is verified (backlog #22 stays blocked "
            "pending that contract decision)."
        )


def test_dataclass_shape_has_not_drifted() -> None:
    # Cheap guard: if `_TailExitCodePolicy`'s fields are renamed, `_classify` above would start
    # silently reading nothing (AttributeError would actually be loud here, but a rename to a
    # similarly-named field could read a default instead) -- pin the exact field set.
    field_names = {f.name for f in dataclasses.fields(_TailExitCodePolicy)}
    assert field_names == {
        "name",
        "trigger_fields",
        "mirrored_in_native_at",
        "route_specific_reason",
    }
