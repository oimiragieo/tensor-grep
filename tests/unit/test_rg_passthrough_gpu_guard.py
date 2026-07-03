"""Round-5 Q9: an explicit --gpu-device-ids request must NOT take the rg-passthrough fast path
(which runs plain CPU rg with exit 0 and no diagnostic) — it must reach Pipeline where the
"never silently downgrade an explicit GPU request to CPU" fail-loud contract runs."""

from tensor_grep.cli.main import _can_passthrough_rg
from tensor_grep.core.config import SearchConfig


def _passthrough(config: SearchConfig) -> bool:
    return _can_passthrough_rg(
        config,
        format_type="rg",
        explicit_rg_format=False,
        json_mode=False,
        ndjson_mode=False,
        files_mode=False,
        files_with_matches=False,
        files_without_match=False,
        only_matching=False,
        stats_mode=False,
    )


def test_gpu_device_ids_request_is_not_passthrough() -> None:
    # Was True (silent CPU downgrade); must be False so Pipeline can raise ConfigurationError.
    assert _passthrough(SearchConfig(gpu_device_ids=[0])) is False


def test_plain_search_still_passthrough() -> None:
    # The default fast path must be preserved (no perf regression).
    assert _passthrough(SearchConfig()) is True
