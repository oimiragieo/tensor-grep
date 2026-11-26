from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app

pytestmark = pytest.mark.skipif(
    shutil.which("ast-grep") is None and shutil.which("sg") is None,
    reason="ast-grep binary required for real AST rule-pack recall tests",
)


def _scan_dir(path: Path, ruleset: str, language: str | None = None) -> dict[str, object]:
    args = [
        "scan",
        "--ruleset",
        ruleset,
        "--path",
        str(path),
        "--json",
        "--allow-broad-generated-scan",
    ]
    if language is not None:
        args.extend(["--language", language])
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


def test_rule_pack_recall_on_multi_kwarg_vulnerable_corpus(tmp_path: Path) -> None:
    py_vuln = tmp_path / "vuln" / "app.py"
    py_vuln.parent.mkdir(parents=True)
    py_vuln.write_text(
        "\n".join([
            "import subprocess",
            "import requests",
            "import yaml",
            "import pandas",
            "subprocess.run(cmd, shell=True)",
            "subprocess.run(cmd, shell=True, check=True)",
            "subprocess.run(shell=True, args=cmd)",
            "subprocess.run(cmd, text=True, shell=True, capture_output=True)",
            "subprocess.call(cmd, shell=True, timeout=10)",
            "subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)",
            "subprocess.check_call(cmd, shell=True, cwd='/tmp')",
            "subprocess.check_output(cmd, shell=True, text=True)",
            "requests.get(url, verify=False)",
            "requests.get(url, verify=False, timeout=5)",
            "requests.get(verify=False, url=url, headers=h)",
            "requests.post(url, json=body, verify=False, timeout=5)",
            "yaml.load(data)",
            "yaml.load(data, Loader=yaml.Loader)",
            "yaml.load(data, Loader=yaml.UnsafeLoader)",
            "yaml.load(data, Loader=yaml.FullLoader)",
            "yaml.load(data, Loader=yaml.Loader, extra=1)",
            "pandas.read_pickle(path, compression='gzip')",
        ])
        + "\n",
        encoding="utf-8",
    )

    js_vuln = tmp_path / "vuln" / "index.js"
    js_vuln.write_text(
        "\n".join([
            "const child_process = require('child_process');",
            "child_process.exec(cmd, { cwd: '/tmp' }, cb);",
            "child_process.execSync(cmd, { encoding: 'utf8' });",
            'require("child_process").exec(cmd, { timeout: 1000 }, cb);',
            "child_process.execSync(cmd, { stdio: 'inherit' });",
        ])
        + "\n",
        encoding="utf-8",
    )

    ts_vuln = tmp_path / "vuln" / "index.ts"
    ts_vuln.write_text(
        "\n".join([
            "child_process.exec(cmd, { cwd: '/tmp' }, cb);",
            "child_process.execSync(cmd, { encoding: 'utf8' });",
        ])
        + "\n",
        encoding="utf-8",
    )

    expected_cases = 24  # 18 python + 4 js + 2 ts vulnerable calls
    detected_cases = 0

    sub_payload = _scan_dir(tmp_path / "vuln", "subprocess-safe")
    detected_cases += int(sub_payload["total_matches"])  # type: ignore[arg-type]

    tls_payload = _scan_dir(tmp_path / "vuln", "tls-safe", language="python")
    detected_cases += int(tls_payload["total_matches"])  # type: ignore[arg-type]

    deser_payload = _scan_dir(tmp_path / "vuln", "deserialization-safe", language="python")
    detected_cases += int(deser_payload["total_matches"])  # type: ignore[arg-type]

    recall = detected_cases / expected_cases
    assert recall >= 0.95, f"Recall {recall:.2%} ({detected_cases}/{expected_cases}) below 95%"


def test_rule_pack_negative_controls_have_zero_false_positives(tmp_path: Path) -> None:
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir(parents=True)
    (clean_dir / "safe.py").write_text(
        "\n".join([
            "import subprocess",
            "import requests",
            "import yaml",
            "subprocess.run(['ls', '-la'], check=True)",
            "subprocess.run(cmd, shell=False, check=True)",
            "subprocess.check_output(['echo', 'ok'], text=True)",
            "requests.get(url, verify=True, timeout=5)",
            "requests.post(url, json=body, timeout=5)",
            "yaml.load(data, Loader=yaml.SafeLoader)",
            "yaml.safe_load(data)",
        ])
        + "\n",
        encoding="utf-8",
    )
    (clean_dir / "safe.js").write_text(
        "child_process.execFile('ls', ['-la'], cb);\nexec(query, params);\n",
        encoding="utf-8",
    )

    for ruleset in ("subprocess-safe", "tls-safe", "deserialization-safe"):
        payload = _scan_dir(clean_dir, ruleset)
        assert payload["total_matches"] == 0, (
            f"Unexpected false positives in {ruleset}: {payload['findings']}"
        )
