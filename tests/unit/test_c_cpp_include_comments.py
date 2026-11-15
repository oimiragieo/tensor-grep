from tensor_grep.cli.lang_c_cpp_include import iter_include_directives


def test_block_commented_include_is_not_a_dependency_edge() -> None:
    assert iter_include_directives('/*\n#include "dep.h"\n*/\n') == []
    assert iter_include_directives("/*\n#include <dep.h>\n") == []


def test_comment_markers_in_strings_do_not_hide_live_include() -> None:
    source = 'const char *s = "/*";\n#include "dep.h"\n'
    assert iter_include_directives(source) == [("dep.h", False)]


def test_comment_before_include_is_treated_as_whitespace() -> None:
    assert iter_include_directives("/* note */ #include <dep.h>\n") == [("dep.h", True)]
