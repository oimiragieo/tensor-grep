def assert_no_negative_secret_lines(payload, positive_line):
    matched_lines = {
        occurrence["line"]
        for finding in payload["findings"]
        for occurrence in finding.get("occurrences", [])
    }
    unexpected_lines = set(range(1, positive_line)) & matched_lines
    assert not unexpected_lines, (
        f"secrets-basic findings on negative lines: {sorted(unexpected_lines)}"
    )
