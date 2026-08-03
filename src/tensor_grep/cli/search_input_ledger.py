"""Behaviorless Round-60 seam for SearchInputLedger (Task 2A / #89).

RED-phase: caps/types export + fail-closed admit/charge APIs. No route surrogates.
Public/delegation doors live in bootstrap/main/native producers; tests invoke those
doors and observe child-start seams. Green-phase production must replace stubs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Combined file/byte caps span explicit pattern + ignore inputs together.
MAX_PATTERN_OR_IGNORE_FILE_BYTES = 1 << 20  # 1 MiB per file
MAX_COMBINED_PATTERN_IGNORE_FILES = 32
MAX_COMBINED_DECODED_BYTES = 4 << 20  # 4 MiB aggregate
MAX_PATTERN_OR_IGNORE_RULE_BYTES = 16 << 10  # 16 KiB per rule
# Pattern totals (positional + -e + -f) and ignore totals (explicit + generated)
# are SEPARATE 65,536 caps — never one combined pattern+ignore counter.
MAX_COMBINED_PATTERNS = 65_536
MAX_COMBINED_IGNORE_RULES = 65_536
MAX_COMPILED_MATCHER_LIVE_MEMORY_BYTES = 64 << 20  # 64 MiB
MAX_MATCHER_TRANSITIONS = 10_000_000
REQUEST_DEADLINE_SECONDS = 300

SEARCH_INPUT_LIMIT_REASON = "search_input_limit"

ROUTE_DOORS = (
    "bootstrap",
    "full_cli",
    "direct_native",
    "native_to_rg",
    "native_to_sidecar",
)


@dataclass
class SearchInputLimitExceeded(Exception):
    """Raised when a ledger dimension exceeds its inclusive cap or deadline."""

    dimension: str
    observed: int | float
    limit: int | float
    incomplete_reason_class: str = SEARCH_INPUT_LIMIT_REASON


@dataclass
class RouteProcessCounters:
    """Injectable observable starts for compiler/native/rg/sidecar/matcher."""

    compiler_starts: int = 0
    native_starts: int = 0
    rg_starts: int = 0
    sidecar_starts: int = 0
    matcher_starts: int = 0
    cpu_starts: int = 0

    def any_started(self) -> bool:
        return (
            self.compiler_starts
            + self.native_starts
            + self.rg_starts
            + self.sidecar_starts
            + self.matcher_starts
            + self.cpu_starts
        ) > 0

    def record(self, kind: str) -> None:
        field_name = {
            "compiler": "compiler_starts",
            "native": "native_starts",
            "rg": "rg_starts",
            "sidecar": "sidecar_starts",
            "matcher": "matcher_starts",
            "cpu": "cpu_starts",
        }.get(kind)
        if field_name is None:
            raise ValueError(f"unknown child-start kind: {kind!r}")
        setattr(self, field_name, getattr(self, field_name) + 1)


@dataclass
class SearchInputLedger:
    """No-refund ledger spanning explicit and generated sources.

    Behaviorless: ``admit_*`` / ``charge_*`` / ``check_deadline`` raise
    ``NotImplementedError`` (fail closed; no silent accept).
    """

    file_count: int = 0
    decoded_bytes: int = 0
    pattern_count: int = 0
    ignore_rule_count: int = 0
    compiled_live_memory_bytes: int = 0
    matcher_transitions: int = 0
    deadline_seconds: float = float(REQUEST_DEADLINE_SECONDS)
    installed_before_route_selection: bool = False
    _charges: list[str] = field(default_factory=list)

    def mark_installed_before_route_selection(self) -> None:
        # Behaviorless: intentionally a no-op so route doors stay unguarded.
        return None

    def admit_file(self, *, size_bytes: int, source: str = "explicit") -> None:
        _ = size_bytes, source
        raise NotImplementedError("SearchInputLedger.admit_file is not implemented")

    def admit_rule_bytes(self, *, size_bytes: int, kind: str = "pattern") -> None:
        _ = size_bytes, kind
        raise NotImplementedError("SearchInputLedger.admit_rule_bytes is not implemented")

    def admit_patterns(self, count: int, *, source: str = "positional") -> None:
        _ = count, source
        raise NotImplementedError("SearchInputLedger.admit_patterns is not implemented")

    def admit_ignore_rules(self, count: int, *, source: str = "explicit") -> None:
        _ = count, source
        raise NotImplementedError("SearchInputLedger.admit_ignore_rules is not implemented")

    def charge_matcher_construction(self, *, live_memory_bytes: int) -> None:
        _ = live_memory_bytes
        raise NotImplementedError(
            "SearchInputLedger.charge_matcher_construction is not implemented"
        )

    def charge_matcher_transitions(self, count: int) -> None:
        _ = count
        raise NotImplementedError("SearchInputLedger.charge_matcher_transitions is not implemented")

    def check_deadline(self, *, elapsed_seconds: float) -> None:
        _ = elapsed_seconds
        raise NotImplementedError("SearchInputLedger.check_deadline is not implemented")

    def incomplete_envelope(
        self, *, dimension: str, observed: int | float, limit: int | float
    ) -> dict[str, Any]:
        return {
            "result_incomplete": True,
            "incomplete_reason_class": SEARCH_INPUT_LIMIT_REASON,
            "dimension": dimension,
            "observed": observed,
            "limit": limit,
            "exit": 2,
        }


def on_public_route_entry(route: str) -> None:
    """Must be called by every public/delegation door before child selection.

    Behaviorless no-op. Producers do not call this yet — route-install REDs observe that.
    """
    _ = route
    return None
