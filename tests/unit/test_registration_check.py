"""Tests for the registration-completeness detector (catches 'added X but missed site N')."""

import json
from pathlib import Path

from tensor_grep.core.registration_check import (
    RegistrationGroup,
    RegistrationSite,
    check_entity,
    check_from_config,
    check_group,
    check_groups,
    extract_members,
    load_config,
    main,
)


def _write_config(tmp_path: Path, group: dict) -> str:
    cfg = tmp_path / "reg.json"
    cfg.write_text(json.dumps({"registration_groups": [group]}), encoding="utf-8")
    return str(cfg)


def test_entity_scoped_config_ignores_legit_asymmetry(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    a.write_text('FLAGS = {"--x", "--rank"}\n', encoding="utf-8")
    b = tmp_path / "b.rs"
    b.write_text('const FLAGS: &[&str] = &["--rank", "--z"];\n', encoding="utf-8")
    cfg = _write_config(
        tmp_path,
        {
            "name": "g",
            "entities": ["--rank"],
            "sites": [{"file": str(a), "symbol": "FLAGS"}, {"file": str(b), "symbol": "FLAGS"}],
        },
    )
    report = check_from_config(cfg)
    assert report["complete"] is True  # --rank in both; --x/--z asymmetry not flagged
    assert report["groups"][0]["mode"] == "entity-scoped"


def test_empty_or_renamed_symbol_surfaces_as_incomplete(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    a.write_text('FLAGS = {"--rank"}\n', encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text(
        'OTHER = {"--rank"}\n', encoding="utf-8"
    )  # FLAGS symbol absent → silent-empty vector
    cfg = _write_config(
        tmp_path,
        {
            "name": "g",
            "entities": ["--rank"],
            "sites": [{"file": str(a), "symbol": "FLAGS"}, {"file": str(b), "symbol": "FLAGS"}],
        },
    )
    report = check_from_config(cfg)
    assert report["complete"] is False
    assert report["groups"][0]["empty_sites"]


def test_main_exit_code_zero_then_one(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    a.write_text('FLAGS = {"--rank"}\n', encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text('FLAGS = {"--rank"}\n', encoding="utf-8")
    cfg = _write_config(
        tmp_path,
        {
            "name": "g",
            "entities": ["--rank"],
            "sites": [{"file": str(a), "symbol": "FLAGS"}, {"file": str(b), "symbol": "FLAGS"}],
        },
    )
    assert main([cfg]) == 0
    b.write_text('FLAGS = {"--other"}\n', encoding="utf-8")  # --rank now missing from b
    assert main([cfg]) == 1


def test_check_entity_complete_when_in_all_sites_despite_legit_asymmetry(tmp_path: Path) -> None:
    # Sites legitimately differ (a has --x, b has --z) — only the queried entity matters.
    a = tmp_path / "a.py"
    a.write_text('FLAGS = {"--x", "--rank"}\n', encoding="utf-8")
    b = tmp_path / "b.rs"
    b.write_text('const FLAGS: &[&str] = &["--z", "--rank"];\n', encoding="utf-8")
    group = RegistrationGroup(
        "flags", (RegistrationSite(str(a), "FLAGS"), RegistrationSite(str(b), "FLAGS"))
    )
    report = check_entity(group, "--rank")
    assert report["complete"] is True
    assert len(report["present_in"]) == 2


def test_check_entity_flags_only_the_missing_entity_not_asymmetry(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    a.write_text('FLAGS = {"--x", "--rank"}\n', encoding="utf-8")
    b = tmp_path / "b.rs"
    b.write_text('const FLAGS: &[&str] = &["--z"];\n', encoding="utf-8")  # --rank missing here
    group = RegistrationGroup(
        "flags", (RegistrationSite(str(a), "FLAGS"), RegistrationSite(str(b), "FLAGS"))
    )
    report = check_entity(group, "--rank")
    assert report["complete"] is False
    assert len(report["missing_from"]) == 1  # only --rank flagged; --x/--z asymmetry ignored


def test_extract_members_python_set(tmp_path: Path) -> None:
    f = tmp_path / "x.py"
    f.write_text('OTHER = 1\nFLAGS = {\n    "--a",\n    "--b",\n}\nMORE = 2\n', encoding="utf-8")
    assert extract_members(str(f), "FLAGS") == {"--a", "--b"}


def test_extract_members_rust_array_skips_type_annotation(tmp_path: Path) -> None:
    f = tmp_path / "x.rs"
    # The `&[&str]` type annotation must NOT be mistaken for the value array.
    f.write_text('const FLAGS: &[&str] = &[\n    "--a",\n    "--b",\n];\n', encoding="utf-8")
    assert extract_members(str(f), "FLAGS") == {"--a", "--b"}


def test_extract_members_missing_symbol_returns_empty(tmp_path: Path) -> None:
    f = tmp_path / "x.py"
    f.write_text("NOTHING = {}\n", encoding="utf-8")
    assert extract_members(str(f), "ABSENT") == set()


def test_extract_members_skips_commented_out_entries(tmp_path: Path) -> None:
    # A `#`-commented quoted string inside the block must NOT be collected — otherwise it reads as
    # a registered member and masks a genuine registration gap (false negative defeats the tool).
    f = tmp_path / "x.py"
    f.write_text(
        'FLAGS = {\n    "--a",\n    # "--ghost",  not registered yet\n    "--b",\n}\n',
        encoding="utf-8",
    )
    assert extract_members(str(f), "FLAGS") == {"--a", "--b"}


def test_extract_members_ignores_symbol_mention_in_preceding_comment(tmp_path: Path) -> None:
    # A comment/docstring mentioning the symbol before its declaration must not be mistaken for it.
    f = tmp_path / "x.py"
    f.write_text(
        "# FLAGS must list every front-door flag; see OTHER == FLAGS guards\n"
        'FLAGS = {"--a", "--b"}\n',
        encoding="utf-8",
    )
    assert extract_members(str(f), "FLAGS") == {"--a", "--b"}


def test_extract_members_bracket_inside_string_does_not_overshoot(tmp_path: Path) -> None:
    # A bracket inside a string literal must not corrupt the depth count and swallow later content.
    f = tmp_path / "x.py"
    f.write_text(
        'FLAGS = ["route[v1", "/health"]\nGHOST = ["--should-not-appear"]\n',
        encoding="utf-8",
    )
    assert extract_members(str(f), "FLAGS") == {"route[v1", "/health"}


def test_extract_members_handles_escaped_quote_in_member(tmp_path: Path) -> None:
    f = tmp_path / "x.py"
    f.write_text('FLAGS = ["a\\"b", "c"]\n', encoding="utf-8")
    assert extract_members(str(f), "FLAGS") == {'a"b', "c"}


def test_check_group_flags_entity_missing_from_one_site(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    a.write_text('FLAGS = {"--x", "--y", "--rank"}\n', encoding="utf-8")
    b = tmp_path / "b.rs"
    b.write_text('const FLAGS: &[&str] = &["--x", "--y"];\n', encoding="utf-8")  # missing --rank
    group = RegistrationGroup(
        "flags", (RegistrationSite(str(a), "FLAGS"), RegistrationSite(str(b), "FLAGS"))
    )
    report = check_group(group)
    assert report["complete"] is False
    assert len(report["missing"]) == 1
    assert report["missing"][0]["entity"] == "--rank"
    assert str(b) in report["missing"][0]["missing_from"][0]


def test_check_group_complete_when_all_sites_match(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    a.write_text('FLAGS = {"--x", "--y"}\n', encoding="utf-8")
    b = tmp_path / "b.rs"
    b.write_text('const FLAGS: &[&str] = &["--x", "--y"];\n', encoding="utf-8")
    group = RegistrationGroup(
        "flags", (RegistrationSite(str(a), "FLAGS"), RegistrationSite(str(b), "FLAGS"))
    )
    assert check_group(group)["complete"] is True


def test_check_groups_aggregates_and_sets_overall(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    a.write_text('G = {"--x"}\n', encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text('G = {"--x", "--y"}\n', encoding="utf-8")
    groups = [
        RegistrationGroup("g", (RegistrationSite(str(a), "G"), RegistrationSite(str(b), "G")))
    ]
    report = check_groups(groups)
    assert report["complete"] is False
    assert report["incomplete_groups"] == 1


def test_load_config_parses_groups(tmp_path: Path) -> None:
    cfg = tmp_path / "reg.json"
    cfg.write_text(
        json.dumps({
            "registration_groups": [
                {
                    "name": "search-flag",
                    "sites": [
                        {"file": "a.py", "symbol": "FLAGS"},
                        {"file": "b.rs", "symbol": "FLAGS"},
                    ],
                }
            ]
        }),
        encoding="utf-8",
    )
    groups = load_config(str(cfg))
    assert len(groups) == 1
    assert groups[0].name == "search-flag"
    assert len(groups[0].sites) == 2
    assert groups[0].sites[0].symbol == "FLAGS"


def test_load_config_accepts_toml(tmp_path: Path) -> None:
    cfg = tmp_path / "reg.toml"
    cfg.write_text(
        "[[registration_groups]]\n"
        'name = "search-flag"\n'
        'entities = ["--rank"]\n'
        "sites = [\n"
        '  { file = "a.py", symbol = "FLAGS" },\n'
        '  { file = "b.rs", symbol = "FLAGS" },\n'
        "]\n",
        encoding="utf-8",
    )
    groups = load_config(str(cfg))
    assert len(groups) == 1
    assert groups[0].name == "search-flag"
    assert groups[0].entities == ("--rank",)
    assert len(groups[0].sites) == 2
    assert groups[0].sites[0].symbol == "FLAGS"
