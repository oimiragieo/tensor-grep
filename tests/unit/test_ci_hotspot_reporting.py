from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_CiHotspotSummary_DefaultCapture_PrintsCompletedCallTimings(tmp_path: Path) -> None:
    generated_test = tmp_path / "test_ci_hotspot_rows.py"
    generated_test.write_text(
        "def test_CiHotspotRows_FixedSamples_AreCollected(ci_hotspot_rows):\n"
        "    ci_hotspot_rows.extend([\n"
        "        ('help', 0.125, 0.250, 'discovered-binary'),\n"
        "        ('tier-pin', 0.500, 0.125, 'none'),\n"
        "        ('help', float('inf'), 0.250, 'discovered-binary'),\n"
        "        ('help', 0.125, 0.250, 'route-marker-must-not-render'),\n"
        "        ('schema-marker-must-not-render', 0.500, 0.125, 'none'),\n"
        "    ])\n"
        "\n"
        "    assert len(ci_hotspot_rows) == 5\n"
        "    assert ci_hotspot_rows[:2] == [\n"
        "        ('help', 0.125, 0.250, 'discovered-binary'),\n"
        "        ('tier-pin', 0.500, 0.125, 'none'),\n"
        "    ]\n",
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[2]
    child_env = os.environ.copy()
    child_env["PYTHONPATH"] = os.pathsep.join([
        str(repo_root / "tests"),
        str(repo_root / "src"),
    ])
    child_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--basetemp",
            str(tmp_path / "child-basetemp"),
            "--rootdir",
            str(tmp_path),
            "-p",
            "no:cacheprovider",
            "-p",
            "conftest",
            str(generated_test),
        ],
        cwd=tmp_path,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=260,
        check=False,
    )

    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert "CI hotspot attribution" in result.stdout
    assert "help python=0.125s native=0.250s route=discovered-binary" in result.stdout
    assert "tier-pin child=0.500s parent=0.125s route=none" in result.stdout

    assert "python=inf" not in result.stdout
    assert "route-marker-must-not-render" not in result.stdout
    assert "schema-marker-must-not-render" not in result.stdout
