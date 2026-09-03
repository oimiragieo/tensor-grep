from typer.testing import CliRunner

from tensor_grep.cli.main import app

runner = CliRunner()


def test_cli_install_dry_run():
    res = runner.invoke(app, ["install", "--target", "claude", "--dry-run", "--json"])
    assert res.exit_code == 0
    assert '"status": "dry_run"' in res.stdout
    assert '"target": "claude"' in res.stdout


def test_cli_uninstall_dry_run():
    res = runner.invoke(app, ["uninstall", "--target", "cursor", "--dry-run", "--json"])
    assert res.exit_code == 0
    assert '"status": "dry_run"' in res.stdout
    assert '"target": "cursor"' in res.stdout


def test_cli_install_invalid_target():
    res = runner.invoke(app, ["install", "--target", "invalid_agent"])
    assert res.exit_code != 0
    assert "Unsupported agent target" in res.output or "Error" in res.output
