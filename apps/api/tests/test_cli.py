import json
from pathlib import Path
import sys

import pytest

from app import cli


def test_github_import_defaults_enabled_and_can_be_disabled():
    assert cli.Settings(data_dir="test").github_import_enabled is True
    assert cli.Settings(data_dir="test", github_import_enabled=False).github_import_enabled is False


def test_direct_service_github_default_and_override(tmp_path, monkeypatch):
    from app.service import RepositoryRegistry
    monkeypatch.delenv("CODETRAIL_GITHUB_IMPORT_ENABLED", raising=False)
    assert RepositoryRegistry({}, data_root=tmp_path / "enabled").github_import_enabled
    monkeypatch.setenv("CODETRAIL_GITHUB_IMPORT_ENABLED", "0")
    assert not RepositoryRegistry({}, data_root=tmp_path / "disabled").github_import_enabled


def test_shared_config_and_environment_precedence(tmp_path, monkeypatch):
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({"data_dir": str(tmp_path / "data"), "allowed_import_roots": [str(tmp_path)]}))
    settings = cli.load_settings(config)
    for key in ("CODETRAIL_DATA_DIR", "CODETRAIL_REPOSITORIES", "CODETRAIL_ALLOWED_IMPORT_ROOTS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CODETRAIL_WORKBENCH_URL", "http://localhost:3333")
    cli.apply_settings(settings)
    assert cli.os.environ["CODETRAIL_DATA_DIR"] == str(tmp_path / "data")
    assert json.loads(cli.os.environ["CODETRAIL_ALLOWED_IMPORT_ROOTS"]) == [str(tmp_path)]
    assert cli.os.environ["CODETRAIL_WORKBENCH_URL"] == "http://localhost:3333"


@pytest.mark.parametrize("roots", [["relative"], ["/definitely-not-existing-codetrail-root"]])
def test_reject_invalid_config_roots(tmp_path, roots):
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({"data_dir": str(tmp_path), "allowed_import_roots": roots}))
    with pytest.raises(ValueError):
        cli.load_settings(config)


def test_init_preserves_existing_settings_and_print_is_read_only(tmp_path, monkeypatch, capsys):
    config = tmp_path / "settings.json"
    monkeypatch.setattr(sys, "argv", ["codetrail", "--config", str(config), "init", "--data-dir", str(tmp_path)])
    cli.main()
    before = config.read_bytes()
    cli.main()
    assert config.read_bytes() == before
    monkeypatch.setattr(sys, "argv", ["codetrail", "install", "--print-config"])
    capsys.readouterr()
    cli.main()
    assert json.loads(capsys.readouterr().out) == {"command": "codetrail", "args": ["mcp"]}


def test_install_preserves_or_replaces_only_named_server(tmp_path, monkeypatch):
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({"data_dir": str(tmp_path)}))
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return cli.subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", ["codetrail", "--config", str(config), "install"])
    cli.main()
    assert len(calls) == 1
    monkeypatch.setattr(sys, "argv", ["codetrail", "--config", str(config), "install", "--replace"])
    cli.main()
    assert calls[-1] == ["codex", "mcp", "add", "codetrail", "--", "codetrail", "--config", str(config), "mcp"]
