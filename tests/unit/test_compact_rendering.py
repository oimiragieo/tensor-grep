import textwrap

import pytest

from tensor_grep.cli.repo_map import _render_source_block


def _render_block(
    tmp_path,
    *,
    file_name: str,
    block: str,
    render_profile: str,
) -> dict[str, object]:
    path = tmp_path / file_name
    path.write_text(block, encoding="utf-8")
    source = {
        "file": str(path),
        "name": path.stem,
        "source": block,
        "start_line": 1,
        "end_line": len(block.splitlines()),
    }
    return _render_source_block(
        source,
        render_profile=render_profile,
        optimize_context=False,
    )


@pytest.mark.parametrize("render_profile", ["compact", "llm"])
def test_js_jsdoc_removed_in_compact_profiles(tmp_path, render_profile):
    rendered = _render_block(
        tmp_path,
        file_name="payments.js",
        render_profile=render_profile,
        block=textwrap.dedent(
            """\
            /**
             * Create an invoice.
             */
            function createInvoice(total) {
              return total;
            }
            """
        ),
    )

    assert "/**" not in rendered["rendered_source"]
    assert "Create an invoice." not in rendered["rendered_source"]
    assert rendered["render_diagnostics"]["js_jsdoc_removed"] == 3


def test_js_jsdoc_preserved_in_full_profile(tmp_path):
    rendered = _render_block(
        tmp_path,
        file_name="payments.js",
        render_profile="full",
        block=textwrap.dedent(
            """\
            /**
             * Create an invoice.
             */
            function createInvoice(total) {
              return total;
            }
            """
        ),
    )

    assert "/**" in rendered["rendered_source"]
    assert rendered["render_diagnostics"]["js_jsdoc_removed"] == 0


@pytest.mark.parametrize("render_profile", ["compact", "llm"])
def test_typescript_type_only_imports_removed_in_compact_profiles(tmp_path, render_profile):
    rendered = _render_block(
        tmp_path,
        file_name="payments.ts",
        render_profile=render_profile,
        block=textwrap.dedent(
            """\
            import type { Invoice } from "./types";
            import { saveInvoice } from "./storage";

            export function persist(invoice: Invoice) {
              return saveInvoice(invoice);
            }
            """
        ),
    )

    assert 'import type { Invoice } from "./types";' not in rendered["rendered_source"]
    assert 'import { saveInvoice } from "./storage";' in rendered["rendered_source"]
    assert rendered["render_diagnostics"]["ts_type_imports_removed"] == 1


def test_typescript_type_only_imports_preserved_in_full_profile(tmp_path):
    rendered = _render_block(
        tmp_path,
        file_name="payments.ts",
        render_profile="full",
        block=textwrap.dedent(
            """\
            import type { Invoice } from "./types";
            export function persist(invoice: Invoice) {
              return invoice;
            }
            """
        ),
    )

    assert 'import type { Invoice } from "./types";' in rendered["rendered_source"]
    assert rendered["render_diagnostics"]["ts_type_imports_removed"] == 0


@pytest.mark.parametrize("render_profile", ["compact", "llm"])
def test_typescript_jsdoc_removed_in_compact_profiles(tmp_path, render_profile):
    rendered = _render_block(
        tmp_path,
        file_name="payments.ts",
        render_profile=render_profile,
        block=textwrap.dedent(
            """\
            /**
             * Persist an invoice.
             */
            export function persist(total: number) {
              return total;
            }
            """
        ),
    )

    assert "/**" not in rendered["rendered_source"]
    assert "Persist an invoice." not in rendered["rendered_source"]
    assert rendered["render_diagnostics"]["js_jsdoc_removed"] == 3


def test_typescript_jsdoc_preserved_in_full_profile(tmp_path):
    rendered = _render_block(
        tmp_path,
        file_name="payments.ts",
        render_profile="full",
        block=textwrap.dedent(
            """\
            /**
             * Persist an invoice.
             */
            export function persist(total: number) {
              return total;
            }
            """
        ),
    )

    assert "/**" in rendered["rendered_source"]
    assert rendered["render_diagnostics"]["js_jsdoc_removed"] == 0


@pytest.mark.parametrize(
    ("render_profile", "doc_comment"),
    [
        ("compact", "/// Persist an invoice."),
        ("compact", "//! Persist an invoice."),
        ("llm", "/// Persist an invoice."),
        ("llm", "//! Persist an invoice."),
    ],
)
def test_rust_doc_comments_removed_in_compact_profiles(tmp_path, render_profile, doc_comment):
    rendered = _render_block(
        tmp_path,
        file_name="payments.rs",
        render_profile=render_profile,
        block=f"{doc_comment}\npub fn persist(total: i32) -> i32 {{\n    total\n}}\n",
    )

    assert doc_comment not in rendered["rendered_source"]
    assert rendered["render_diagnostics"]["rust_doc_comments_removed"] == 1


@pytest.mark.parametrize("doc_comment", ["/// Persist an invoice.", "//! Persist an invoice."])
def test_rust_doc_comments_preserved_in_full_profile(tmp_path, doc_comment):
    rendered = _render_block(
        tmp_path,
        file_name="payments.rs",
        render_profile="full",
        block=f"{doc_comment}\npub fn persist(total: i32) -> i32 {{\n    total\n}}\n",
    )

    assert doc_comment in rendered["rendered_source"]
    assert rendered["render_diagnostics"]["rust_doc_comments_removed"] == 0


@pytest.mark.parametrize(
    ("render_profile", "attribute_line"),
    [
        ("compact", "#[derive(Debug, Clone)]"),
        ("compact", "#[cfg(test)]"),
        ("compact", "#[allow(dead_code)]"),
        ("llm", "#[derive(Debug, Clone)]"),
        ("llm", "#[cfg(test)]"),
        ("llm", "#[allow(dead_code)]"),
    ],
)
def test_rust_attributes_removed_in_compact_profiles(tmp_path, render_profile, attribute_line):
    rendered = _render_block(
        tmp_path,
        file_name="payments.rs",
        render_profile=render_profile,
        block=f"{attribute_line}\npub fn persist(total: i32) -> i32 {{\n    total\n}}\n",
    )

    assert attribute_line not in rendered["rendered_source"]
    assert rendered["render_diagnostics"]["rust_attributes_removed"] == 1


@pytest.mark.parametrize(
    "attribute_line",
    [
        "#[derive(Debug, Clone)]",
        "#[cfg(test)]",
        "#[allow(dead_code)]",
    ],
)
def test_rust_attributes_preserved_in_full_profile(tmp_path, attribute_line):
    rendered = _render_block(
        tmp_path,
        file_name="payments.rs",
        render_profile="full",
        block=f"{attribute_line}\npub fn persist(total: i32) -> i32 {{\n    total\n}}\n",
    )

    assert attribute_line in rendered["rendered_source"]
    assert rendered["render_diagnostics"]["rust_attributes_removed"] == 0


def test_python_compact_profile_stripping_is_not_regressed(tmp_path):
    rendered = _render_block(
        tmp_path,
        file_name="payments.py",
        render_profile="compact",
        block=textwrap.dedent(
            '''\
            class PaymentService:
                """Persist invoices."""
                pass
            '''
        ),
    )

    assert '"""Persist invoices."""' not in rendered["rendered_source"]
    assert "pass" not in rendered["rendered_source"]
    assert rendered["render_diagnostics"]["removed_docstring_lines"] == 1
    assert rendered["render_diagnostics"]["removed_boilerplate_lines"] == 1
