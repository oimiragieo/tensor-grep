import time

from tensor_grep.cli.lang_c_cpp_include import iter_include_directives


def test_block_commented_include_is_not_a_dependency_edge() -> None:
    assert iter_include_directives('/*\n#include "dep.h"\n*/\n') == []
    assert iter_include_directives("/*\n#include <dep.h>\n") == []


def test_comment_markers_in_strings_do_not_hide_live_include() -> None:
    source = 'const char *s = "/*";\n#include "dep.h"\n'
    assert iter_include_directives(source) == [("dep.h", False)]


def test_comment_markers_in_raw_strings_do_not_hide_live_include() -> None:
    source = (
        'const char *s = R"tag(\n#include "decoy.h"\nfoo" /* literal\n)tag";\n#include "dep.h"\n'
    )
    assert iter_include_directives(source) == [("dep.h", False)]


def test_comment_before_include_is_treated_as_whitespace() -> None:
    assert iter_include_directives("/* note */ #include <dep.h>\n") == [("dep.h", True)]


def test_line_spliced_line_comment_hides_following_physical_include() -> None:
    source = "// note \\" + "\n" + '#include "decoy.h"\n#include "dep.h"\n'
    assert iter_include_directives(source) == [("dep.h", False)]


def test_line_spliced_string_does_not_create_a_dependency_edge() -> None:
    source = 'const char *text = "prefix\\' + "\n" + '#include <decoy.h>";\n#include <dep.h>\n'
    assert iter_include_directives(source) == [("dep.h", True)]


def test_unterminated_raw_literal_masking_is_bounded() -> None:
    source = 'R"(' * 16_000
    started = time.perf_counter()

    assert iter_include_directives(source) == []

    assert time.perf_counter() - started < 0.75
