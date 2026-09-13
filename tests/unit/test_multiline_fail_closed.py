"""`--multiline` must fail closed when no backend can honor it.

Exit 1 means "complete search, no matches" under the three-state contract in docs/CONTRACTS.md.
Returning that for a request whose flag was silently dropped is a FALSE COMPLETE -- the caller
cannot tell "this pattern is absent" from "I did not run the search you asked for".

Backend availability is FORCED with @patch rather than detected, mirroring
tests/unit/test_pipeline.py:303-313. An env-detected test passes or fails by what happens to be
installed on the runner, which is how a POSIX-only and a WSL-only bug both escaped this repo's
host-green checks on 2026-09-12.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import ConfigurationError, Pipeline


@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
def test_multiline_raises_when_rg_is_missing(mock_rust, mock_rg) -> None:
    mock_rg.return_value.is_available.return_value = False
    mock_rust.return_value.is_available.return_value = True

    with pytest.raises(ConfigurationError, match="multiline"):
        Pipeline(force_cpu=True, config=SearchConfig(multiline=True, query_pattern="a\\nb"))


@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
def test_multiline_dotall_raises_when_rg_is_missing(mock_rust, mock_rg) -> None:
    """Same false complete: a dropped dotall silently changes what the pattern MEANS."""
    mock_rg.return_value.is_available.return_value = False
    mock_rust.return_value.is_available.return_value = True

    with pytest.raises(ConfigurationError, match="multiline"):
        Pipeline(
            force_cpu=True,
            config=SearchConfig(multiline_dotall=True, query_pattern="a.b"),
        )


@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
def test_multiline_is_HONORED_when_rg_is_available(mock_rust, mock_rg) -> None:
    """The other half of the contract. A gate that only ever raises would break every
    multiline search on a normal install -- this pins that rg is SELECTED, not refused.
    """
    mock_rg.return_value.is_available.return_value = True
    mock_rust.return_value.is_available.return_value = True

    pipeline = Pipeline(force_cpu=True, config=SearchConfig(multiline=True, query_pattern="a\\nb"))

    assert pipeline.backend is mock_rg.return_value


@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
def test_multiline_with_explicit_gpu_raises_instead_of_rerouting(mock_rust, mock_rg) -> None:
    """REGRESSION GUARD for the defect council round 2 found in v2.

    A greedy `elif multiline` captured `-U --gpu-device-ids` and quietly sent it to rg with no
    `fallback_reason` -- silently downgrading an EXPLICIT routing request, which is the same
    defect class this plan removes. An explicit GPU request that cannot be honored must raise,
    exactly as the existing `_raise_explicit_gpu_configuration_error` guard in the `config.ast`
    arm does for `--ast --gpu-device-ids`.
    """
    mock_rg.return_value.is_available.return_value = True
    mock_rust.return_value.is_available.return_value = True

    with pytest.raises(ConfigurationError, match="GPU"):
        Pipeline(
            force_cpu=True,
            config=SearchConfig(multiline=True, query_pattern="a\\nb", gpu_device_ids=[0]),
        )


@patch("tensor_grep.core.pipeline.CPUBackend")
@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
def test_multiline_with_ltl_raises_instead_of_discarding_multiline(
    mock_rust, mock_rg, mock_cpu
) -> None:
    """`-U --ltl` must REFUSE, not silently drop `-U`.

    rg IS available here, so a failure cannot be blamed on the environment. Pre-fix this
    reaches the semantics arm (the branch that selects `CPUBackend()` when `config.ltl` is
    set) and gets CPUBackend -- which never reads `config.multiline`
    (its ONLY consumer in the package is backends/ripgrep_backend.py:585-592). The search then
    runs line-oriented and returns exit 1, which docs/CONTRACTS.md defines as "complete search,
    no matches": the same false complete this gate exists to remove.

    Routing to rg instead is NOT the fix -- that would drop LTL semantics. Refusing is the only
    answer that is neither a silent downgrade nor a lie about completeness.

    v6 asserted the OPPOSITE of this (`pipeline.backend is mock_cpu.return_value`), which
    pinned the defect the plan's own Goal names. Council round 5 caught it.
    """
    mock_rg.return_value.is_available.return_value = True
    mock_rust.return_value.is_available.return_value = True

    with pytest.raises(ConfigurationError, match="ltl"):
        Pipeline(
            force_cpu=False,
            config=SearchConfig(multiline=True, ltl=True, query_pattern="a\\nb"),
        )

    # CPUBackend must not have been INSTANTIATED on the way to the raise. Asserting on
    # `.search()` would be vacuous -- `Pipeline.__init__` never calls it, so that assertion
    # passes whether or not the gate works (council round 6). The constructor call is the
    # observable that actually discriminates: pre-fix the ltl branch constructs one.
    mock_cpu.assert_not_called()


@patch("tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend")
@patch("tensor_grep.backends.ast_backend.AstBackend")
@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
def test_multiline_with_ast_still_reaches_the_ast_arm(
    mock_rust, mock_rg, mock_ast_native, mock_ast_wrapper
) -> None:
    """Third exclusion twin, completing the trio with the GPU and LTL guards.

    v3's condition carries `and not config.ast`, but nothing pinned it -- deleting that clause
    would have passed the entire suite, and a future refactor would have silently re-widened the
    gate so `-U --ast` sent an AST request to rg REGEX. `ast_wrapper_backend.py:138-146` shows
    multiline AST patterns are a real supported input, so this is not a theoretical combination.

    Asserts the `elif config and config.ast:` arm still wins: the selected backend must NOT be
    rg, even
    though rg is available and multiline is set.
    """
    mock_rg.return_value.is_available.return_value = True
    mock_rust.return_value.is_available.return_value = True
    # FORCED, not detected. `_supports_native_ast_pattern` (pipeline.py:52-60) rejects this
    # metavar pattern, so the arm depends entirely on the WRAPPER being available; without
    # these two patches the test raises ConfigurationError from
    # `_raise_explicit_ast_configuration_error` (cited by SYMBOL, not line: this gate's own
    # insertion shifts every line below it) on any runner
    # that lacks ast-grep, and the pre-fix count silently becomes 6 failed / 1 passed instead
    # of the expected 5 failed / 2 passed -- the AST test RAISES rather than selecting.
    # (This sentence read "becomes 5 failed / 2 passed" until v12, which was TRUE when the
    # baseline was 4/3 and became a tautology the moment v8 moved the baseline to 5/2:
    # it warned that the count becomes the number it already is. Council round 10.)
    # Mirrors tests/unit/test_pipeline.py:13-14.
    mock_ast_wrapper.return_value.is_available.return_value = True
    mock_ast_native.return_value.is_available.return_value = False

    pipeline = Pipeline(
        force_cpu=False,
        config=SearchConfig(multiline=True, ast=True, query_pattern="def $NAME($$$A): $$$B"),
    )

    # POSITIVE assertion. `is not mock_rg.return_value` would also pass if the pipeline raised
    # or picked some third backend for an unrelated reason -- it cannot tell the AST arm winning
    # from the AST arm being skipped.
    assert pipeline.backend is mock_ast_wrapper.return_value


@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
def test_a_search_without_multiline_is_unaffected(mock_rust, mock_rg) -> None:
    """CONTROL. Without this, the gate could raise unconditionally and both raise-tests would
    still pass -- which would look rigorous while breaking every ordinary --cpu search.
    """
    mock_rg.return_value.is_available.return_value = False
    mock_rust.return_value.is_available.return_value = True

    pipeline = Pipeline(force_cpu=True, config=SearchConfig(query_pattern="alpha"))

    assert pipeline.backend is mock_rust.return_value
