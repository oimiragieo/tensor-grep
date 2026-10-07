from tensor_grep.cli.doctor_report import (
    _doctor_rust_binary_version_status,
    _doctor_rust_binary_warning,
)


def test_existing_binary_whose_version_probe_failed_is_unknown_not_missing():
    assert (
        _doctor_rust_binary_version_status(
            native_tg_binary_kind="standalone-executable",
            rust_binary_version=None,
            rust_binary_version_matches=None,
        )
        == "unknown"
    )


def test_absent_binary_is_still_missing():
    assert (
        _doctor_rust_binary_version_status(
            native_tg_binary_kind="missing",
            rust_binary_version=None,
            rust_binary_version_matches=None,
        )
        == "missing"
    )


def test_unknown_status_carries_a_warning():
    warning = _doctor_rust_binary_warning(
        expected_version="1.0.0", rust_binary_version=None, rust_binary_version_status="unknown"
    )
    assert warning is not None and "could not be verified" in warning


def test_stale_in_tree_remediation_uses_portable_cargo_command():
    from tensor_grep.cli.doctor_report import _doctor_rust_binary_remediation

    for status, kind in (("stale", "in-tree-release"), ("stale-skipped", "missing")):
        message = _doctor_rust_binary_remediation(
            rust_binary_version_status=status, native_tg_binary_kind=kind
        )
        assert message is not None
        command = message.split("`", 2)[1]
        assert command == "cargo build --manifest-path rust_core/Cargo.toml --release"
        assert "TG_NATIVE_TG_BINARY" in message
