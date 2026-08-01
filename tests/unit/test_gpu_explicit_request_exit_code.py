"""An unhonoured explicit `--gpu-device-ids` request does NOT flip the exit code. THE RULING.

Backlog #22, reported by four consecutive live dogfoods as "GPU non-accelerative -- calibrate exit
2 on this CPU build". This file records the answer, which is NO -- as a test rather than a comment,
so the next person who reaches for exit 2 here finds the reasoning already executed.

THE ARGUMENT FOR EXIT 2, stated fairly because it is not stupid, and an earlier cut of this branch
implemented it: the caller asked for a specific execution mode, tg could not provide it, and
silently substituting another is the request going unhonoured. That reads like a refusal, and 2 is
the refusal code.

WHY IT LOSES. `docs/CONTRACTS.md` section 4 defines `2` as INCOMPLETE, meaning the SCAN was
truncated. A CPU-served search runs to completion over every file it was asked about and returns
correct, complete results. Which processor did the arithmetic is a ROUTING fact, not an
incompleteness. The contract already carries the analogous precedent twice, and both go this way:

  * "An OUTPUT-only cap ... is a COMPLETE analysis capped only for display and stays exit `0`;
    only a SCAN truncation exits `2`."
  * `tg imports --deadline` is "a documented NO-OP ... output is byte-identical with or without
    it" -- an accepted flag that changes nothing does not move the exit code.

Two further reasons, both practical:

  * It would break every consumer branching on 1-vs-2 for the most ordinary GPU-requesting
    invocation there is -- a `--gpu-device-ids` search on a machine with no GPU.
  * THE EVIDENCE FOR THE STATUS QUO WAS ALREADY IN THE TEST SUITE. Implementing exit 2 required
    editing THREE independent pre-existing tests that each asserted `exit_code == 0` for exactly
    this case. When a change has to flip three unrelated expectations written at different times,
    the behaviour it is "fixing" was deliberate. That is a signal worth reading BEFORE overriding
    it, not after.

WHAT THE CALLER GETS INSTEAD, and it is strictly more: `gpu_request_unhonoured` still classifies
the request, and the `--json` envelope carries `gpu_evidence_status`, `gpu_proof`,
`native_gpu_unavailable` and `not_gpu_proof_reason`. A harness asking "did GPU actually run" should
read those. A coarse exit code cannot distinguish "no GPU present" from "GPU present but the
sidecar served it" -- the fields can, and the last test here proves it.
"""

from __future__ import annotations

import pytest

from tensor_grep.cli.formatters.json_fmt import _gpu_proof_payload, gpu_request_unhonoured
from tensor_grep.core.result import SearchResult


def _result(*, requested: list[int] | None, backend: str, sidecar: bool) -> SearchResult:
    r = SearchResult(matches=[], total_matches=0, total_files=0)
    r.requested_gpu_device_ids = requested or []
    r.routing_backend = backend
    r.sidecar_used = sidecar
    return r


def test_an_unhonoured_request_is_flagged_but_is_not_an_incompleteness() -> None:
    """THE RULING, in one assertion pair.

    The predicate still fires -- the request WAS unhonoured and the caller should be told. What it
    must not do is imply the scan was truncated, because it was not.
    """
    result = _result(requested=[0], backend="CPUBackend", sidecar=False)

    assert gpu_request_unhonoured(result) is True, (
        "the classifier must still recognise an unhonoured request -- retiring the EXIT rule does "
        "not retire the signal"
    )
    assert result.result_incomplete is False, (
        "an unhonoured GPU request must not set result_incomplete. docs/CONTRACTS.md section 4: "
        "`2`/incomplete means the SCAN was truncated, and this search ran to completion over "
        "every file it was asked about."
    )


def test_the_envelope_carries_the_signal_the_exit_code_deliberately_does_not() -> None:
    """WHERE THE INFORMATION LIVES. This is what makes the ruling safe rather than a loss."""
    payload = _gpu_proof_payload(_result(requested=[0], backend="CPUBackend", sidecar=False))

    assert payload["gpu_evidence_status"] == "unsupported"
    assert payload["gpu_proof"] is False
    assert payload["native_gpu_unavailable"] is True
    assert payload["not_gpu_proof_reason"], "the refusal carries no machine-readable reason"


def test_an_honoured_request_is_native_proof() -> None:
    """CONTROL ARM: a real GPU run must NOT be flagged.

    Without this, "always report unsupported" satisfies the tests above while making a working GPU
    look broken -- the failure mode that matters most on the one platform we cannot test here.
    """
    payload = _gpu_proof_payload(_result(requested=[0], backend="NativeGpuBackend", sidecar=False))

    assert payload["gpu_evidence_status"] == "native"
    assert payload["gpu_proof"] is True
    assert payload["not_gpu_proof_reason"] is None


def test_no_request_means_no_gpu_payload_at_all() -> None:
    """CONTROL ARM: keeps the classifier narrow.

    A search that never asked for GPU emits NO gpu keys. If this ever returns a payload, every
    ordinary CPU search starts carrying GPU evidence fields -- and any future exit rule keyed on
    them would promote all of them.
    """
    payload = _gpu_proof_payload(_result(requested=None, backend="CPUBackend", sidecar=False))

    assert payload == {}, (
        "a search with no --gpu-device-ids emitted GPU evidence keys; the classifier has stopped "
        "being narrow"
    )


@pytest.mark.parametrize(
    ("backend", "sidecar"),
    [("CPUBackend", False), ("NativeGpuBackend", True), ("RustCoreBackend", False)],
)
def test_every_non_proof_route_is_unsupported(backend: str, sidecar: bool) -> None:
    """Sidecar-routed GPU counts as UNHONOURED, not honoured -- and this case settles why the
    envelope beats an exit code.

    `NativeGpuBackend` with `sidecar_used=True` is the subtle one: the backend name says GPU while
    the work went through the sidecar, so it is compatibility output rather than acceleration. A
    rule keyed on the backend NAME alone would call it proof. An exit code could never have
    expressed that distinction at all; the field does it in one string.
    """
    payload = _gpu_proof_payload(_result(requested=[0, 1], backend=backend, sidecar=sidecar))

    assert payload["gpu_evidence_status"] == "unsupported", (
        f"{backend}/sidecar={sidecar} was treated as GPU proof"
    )
    assert payload["native_gpu_unavailable"] is True
