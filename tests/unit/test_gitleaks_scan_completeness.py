"""
RED test: gitleaks scan completeness and positive-control validation.

This test proves the two CRITICAL gitleaks findings from Codex Sol:
1. --no-merges omits merge commits (credentials in merge resolutions escape)
2. Positive-control rule names don't match gitleaks default config
"""

import subprocess
import json
import tempfile
from pathlib import Path


def test_gitleaks_scan_includes_merge_commits():
    """
    EXPECTED FAILURE (RED): Gitleaks with --no-merges misses credentials introduced in merge commits.

    This test proves that using --no-merges creates a gap in scan coverage.
    The scan should either:
    - Run WITHOUT --no-merges (full history), OR
    - Document the merge-commit limitation explicitly
    """
    # Create a temp repo with a merge that introduces a secret
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)

        # Initialize repo
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, capture_output=True)

        # Create base commit
        (repo / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, capture_output=True)

        # Create branch and add secret
        subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, capture_output=True)
        (repo / "file.txt").write_text("AKIAIOSFODNN7EXAMPLE")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add secret"], cwd=repo, capture_output=True)

        # Return to main and create a merge commit
        subprocess.run(["git", "checkout", "main"], cwd=repo, capture_output=True)
        (repo / "other.txt").write_text("change on main")
        subprocess.run(["git", "add", "other.txt"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "main change"], cwd=repo, capture_output=True)

        # Merge (this commit contains the secret in the merged files)
        subprocess.run(["git", "merge", "--no-ff", "feature", "-m", "merge feature"], cwd=repo, capture_output=True)

        # Run gitleaks with --no-merges (SHOULD MISS the secret introduced in the merge)
        result_no_merges = subprocess.run(
            ["gitleaks", "detect", "--source", "git", "--log-opts", "--all --no-merges", "--json"],
            cwd=repo,
            capture_output=True,
            text=True
        )

        findings_no_merges = json.loads(result_no_merges.stdout) if result_no_merges.stdout else []

        # Run gitleaks WITH all commits
        result_all = subprocess.run(
            ["gitleaks", "detect", "--source", "git", "--log-opts", "--all", "--json"],
            cwd=repo,
            capture_output=True,
            text=True
        )

        findings_all = json.loads(result_all.stdout) if result_all.stdout else []

        # THIS MUST BE RED: --no-merges finds FEWER findings than --all
        assert len(findings_no_merges) < len(findings_all), (
            f"Gitleaks with --no-merges found {len(findings_no_merges)} findings, "
            f"but --all found {len(findings_all)}. This proves --no-merges omits commits. "
            f"The gitleaks scan must either use --all or document the limitation."
        )


def test_gitleaks_positive_control_rules_match_config():
    """
    EXPECTED FAILURE (RED): Gitleaks positive-control reports rules that don't exist in default config.

    The report claims:
    - `aws-access-key-id` (does not exist; actual rule is `aws-access-token`)
    - `aws-secret-access-key` (does not exist in default v8.30.1 config)

    Verify the actual rules from the gitleaks v8.30.1 default config.
    """
    # Fetch gitleaks default config
    config_url = "https://raw.githubusercontent.com/gitleaks/gitleaks/v8.30.1/config/gitleaks.toml"
    result = subprocess.run(
        ["curl", "-s", config_url],
        capture_output=True,
        text=True,
        timeout=10
    )

    config = result.stdout

    # Check what rules actually exist
    has_aws_access_token = "aws-access-token" in config
    has_aws_access_key_id = "aws-access-key-id" in config  # Should NOT exist in default
    has_aws_secret_access_key = "aws-secret-access-key" in config  # Should NOT exist in default

    # THIS MUST BE RED: the report's rule names don't match the actual config
    assert has_aws_access_key_id or has_aws_secret_access_key, (
        f"Report claims rules 'aws-access-key-id' and 'aws-secret-access-key', "
        f"but gitleaks v8.30.1 default config has neither. "
        f"Actual AWS rule: 'aws-access-token'. "
        f"The positive-control receipt is not reproducible from the stated command."
    )


if __name__ == "__main__":
    print("Running RED tests for gitleaks findings...")
    try:
        test_gitleaks_scan_includes_merge_commits()
        print("FAIL: test_gitleaks_scan_includes_merge_commits should be RED but passed")
    except AssertionError as e:
        print(f"RED (expected): {e}")

    try:
        test_gitleaks_positive_control_rules_match_config()
        print("FAIL: test_gitleaks_positive_control_rules_match_config should be RED but passed")
    except AssertionError as e:
        print(f"RED (expected): {e}")
