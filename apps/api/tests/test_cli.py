import json
from pathlib import Path
import sys

import pytest

from app import cli


def test_github_import_defaults_enabled_and_can_be_disabled():
    assert cli.Settings(data_dir="test").github_import_enabled is True
    assert cli.Settings(data_dir="test", github_import_enabled=False).github_import_enabled is False
    assert cli.Settings(data_dir="test").real_run_enabled is False


def test_real_run_setting_maps_to_environment_without_overriding_explicit_value(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.os, "environ", dict(cli.os.environ))
    for key in (
        "CODETRAIL_DATA_DIR",
        "CODETRAIL_REPOSITORIES",
        "CODETRAIL_ALLOWED_IMPORT_ROOTS",
        "CODETRAIL_WORKBENCH_URL",
        "CODETRAIL_WEB_ORIGINS",
        "CODETRAIL_GITHUB_IMPORT_ENABLED",
        "CODETRAIL_MCP_EXTERNAL_RUN_ENABLED",
        "CODETRAIL_REAL_RUN_ENABLED",
        "CODETRAIL_TYPESCRIPT_COMPILER",
    ):
        monkeypatch.delenv(key, raising=False)
    cli.apply_settings(cli.Settings(data_dir=str(tmp_path / "data"), real_run_enabled=True))
    assert cli.os.environ["CODETRAIL_REAL_RUN_ENABLED"] == "1"

    monkeypatch.setenv("CODETRAIL_REAL_RUN_ENABLED", "0")
    cli.apply_settings(cli.Settings(data_dir=str(tmp_path / "other-data"), real_run_enabled=True))
    assert cli.os.environ["CODETRAIL_REAL_RUN_ENABLED"] == "0"


def test_direct_service_github_default_and_override(tmp_path, monkeypatch):
    from app.service import RepositoryRegistry
    monkeypatch.delenv("CODETRAIL_GITHUB_IMPORT_ENABLED", raising=False)
    assert RepositoryRegistry({}, data_root=tmp_path / "enabled").github_import_enabled
    monkeypatch.setenv("CODETRAIL_GITHUB_IMPORT_ENABLED", "0")
    assert not RepositoryRegistry({}, data_root=tmp_path / "disabled").github_import_enabled


def test_shared_config_and_environment_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.os, "environ", dict(cli.os.environ))
    config = tmp_path / "settings.json"
    configured_compiler = tmp_path / "configured-typescript.js"
    environment_compiler = tmp_path / "environment-typescript.js"
    config.write_text(json.dumps({"data_dir": str(tmp_path / "data"), "allowed_import_roots": [str(tmp_path)], "typescript_compiler": str(configured_compiler)}))
    settings = cli.load_settings(config)
    for key in ("CODETRAIL_DATA_DIR", "CODETRAIL_REPOSITORIES", "CODETRAIL_ALLOWED_IMPORT_ROOTS", "CODETRAIL_TYPESCRIPT_COMPILER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CODETRAIL_WORKBENCH_URL", "http://localhost:3333")
    monkeypatch.setenv("CODETRAIL_TYPESCRIPT_COMPILER", str(environment_compiler))
    cli.apply_settings(settings)
    assert cli.os.environ["CODETRAIL_DATA_DIR"] == str(tmp_path / "data")
    assert json.loads(cli.os.environ["CODETRAIL_ALLOWED_IMPORT_ROOTS"]) == [str(tmp_path)]
    assert cli.os.environ["CODETRAIL_WORKBENCH_URL"] == "http://localhost:3333"
    assert cli.os.environ["CODETRAIL_TYPESCRIPT_COMPILER"] == str(environment_compiler)


@pytest.mark.parametrize("roots", [["relative"], ["/definitely-not-existing-codetrail-root"]])
def test_reject_invalid_config_roots(tmp_path, roots):
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({"data_dir": str(tmp_path), "allowed_import_roots": roots}))
    with pytest.raises(ValueError):
        cli.load_settings(config)


def test_init_preserves_existing_settings_and_print_is_read_only(tmp_path, monkeypatch, capsys):
    config = tmp_path / "settings.json"
    settings = {
        "data_dir": str(tmp_path / "original-data"),
        "allowed_import_roots": [str(tmp_path)],
        "typescript_compiler": str(tmp_path / "original-typescript.js"),
    }
    config.write_text(json.dumps(settings))
    monkeypatch.setattr(sys, "argv", ["codetrail", "--config", str(config), "init", "--data-dir", str(tmp_path), "--allow-root", str(tmp_path.parent), "--typescript-compiler", str(tmp_path / "new-typescript.js")])
    before = config.read_bytes()
    cli.main()
    assert config.read_bytes() == before
    monkeypatch.setattr(sys, "argv", ["codetrail", "install", "--print-config"])
    capsys.readouterr()
    cli.main()
    assert json.loads(capsys.readouterr().out) == {"command": "codetrail", "args": ["mcp"]}


def _make_workspace(root: Path, compiler_version: str | None = None) -> Path | None:
    (root / "apps/api/app").mkdir(parents=True)
    (root / "apps/api/app/cli.py").touch()
    (root / "package.json").write_text(json.dumps({"name": "codetrail"}))
    (root / "apps/web").mkdir(parents=True, exist_ok=True)
    (root / "apps/web/package.json").write_text(json.dumps({"name": "@codetrail/web"}))
    if compiler_version is None:
        return None
    package = root / "apps/web/node_modules/typescript"
    (package / "lib").mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": "typescript", "version": compiler_version}))
    compiler = package / "lib/typescript.js"
    compiler.write_text("never executed")
    return compiler.resolve()


def test_init_defaults_to_current_root_and_discovers_locked_typescript(tmp_path, monkeypatch, capsys):
    compiler = _make_workspace(tmp_path, "5.9.3")
    config = tmp_path / "settings.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CODETRAIL_TYPESCRIPT_COMPILER", raising=False)
    monkeypatch.setattr(sys, "argv", ["codetrail", "--config", str(config), "init"])

    cli.main()

    settings = cli.load_settings(config)
    assert settings.allowed_import_roots == [str(tmp_path.resolve())]
    assert settings.typescript_compiler == str(compiler)
    assert settings.real_run_enabled is False
    assert capsys.readouterr().err == ""


def test_runtime_discovers_typescript_without_rewriting_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.os, "environ", dict(cli.os.environ))
    compiler = _make_workspace(tmp_path, "5.9.3")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CODETRAIL_TYPESCRIPT_COMPILER", raising=False)

    cli.apply_settings(cli.Settings(data_dir=str(tmp_path / "data")))

    assert cli.os.environ["CODETRAIL_TYPESCRIPT_COMPILER"] == str(compiler)


def test_init_keeps_python_available_and_warns_when_typescript_is_missing(tmp_path, monkeypatch, capsys):
    _make_workspace(tmp_path)
    config = tmp_path / "settings.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CODETRAIL_TYPESCRIPT_COMPILER", raising=False)
    monkeypatch.setattr(sys, "argv", ["codetrail", "--config", str(config), "init"])

    cli.main()

    assert cli.load_settings(config).typescript_compiler is None
    warning = capsys.readouterr().err
    assert "Python indexing remains available" in warning
    assert "TypeScript/TSX indexing will fail" in warning
    cli.main()
    assert "Existing settings were preserved" in capsys.readouterr().err


def test_init_ignores_unreadable_workspace_package_metadata(tmp_path, monkeypatch, capsys):
    _make_workspace(tmp_path)
    (tmp_path / "apps/web/package.json").write_bytes(b"\xff")
    config = tmp_path / "settings.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CODETRAIL_TYPESCRIPT_COMPILER", raising=False)
    monkeypatch.setattr(sys, "argv", ["codetrail", "--config", str(config), "init"])

    cli.main()

    assert cli.load_settings(config).typescript_compiler is None
    assert "Python indexing remains available" in capsys.readouterr().err


def test_init_explicit_typescript_compiler_takes_precedence(tmp_path, monkeypatch):
    discovered = _make_workspace(tmp_path, "5.9.3")
    explicit = tmp_path / "explicit-typescript.js"
    explicit.write_text("never executed")
    config = tmp_path / "settings.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["codetrail", "--config", str(config), "init", "--typescript-compiler", str(explicit)])

    cli.main()

    assert cli.load_settings(config).typescript_compiler == str(explicit.resolve())
    assert cli.load_settings(config).typescript_compiler != str(discovered)


def test_existing_settings_are_not_updated_with_discovered_compiler(tmp_path, monkeypatch, capsys):
    _make_workspace(tmp_path, "5.9.3")
    config = tmp_path / "settings.json"
    original = {"data_dir": str(tmp_path / "data"), "allowed_import_roots": [str(tmp_path)]}
    config.write_text(json.dumps(original))
    before = config.read_bytes()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CODETRAIL_TYPESCRIPT_COMPILER", raising=False)
    monkeypatch.setattr(sys, "argv", ["codetrail", "--config", str(config), "init"])

    cli.main()

    assert config.read_bytes() == before
    assert "existing settings were preserved" in capsys.readouterr().err


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
