"""Installed API/MCP entry point sharing one editable user configuration."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_dir: str = Field(min_length=1)
    allowed_import_roots: list[str] = Field(default_factory=list)
    workbench_url: str = "http://localhost:3000"
    web_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"])
    github_import_enabled: bool = False
    external_run_enabled: bool = True
    typescript_compiler: str | None = None


def config_path(explicit: str | None = None) -> Path:
    return Path(explicit or os.getenv("CODETRAIL_CONFIG") or (Path.home() / ".config/codetrail/settings.json")).expanduser().resolve()


def load_settings(path: Path) -> Settings:
    settings = Settings.model_validate_json(path.read_text(encoding="utf-8"))
    paths = [settings.data_dir, *settings.allowed_import_roots]
    if settings.typescript_compiler:
        paths.append(settings.typescript_compiler)
    if any(not Path(value).is_absolute() for value in paths):
        raise ValueError("configuration paths must be absolute")
    if any(not Path(value).is_dir() for value in settings.allowed_import_roots):
        raise ValueError("allowed import roots must be existing directories")
    return settings


def apply_settings(settings: Settings) -> None:
    values = {
        "CODETRAIL_DATA_DIR": settings.data_dir,
        "CODETRAIL_REPOSITORIES": "{}",
        "CODETRAIL_ALLOWED_IMPORT_ROOTS": json.dumps(settings.allowed_import_roots),
        "CODETRAIL_WORKBENCH_URL": settings.workbench_url,
        "CODETRAIL_WEB_ORIGINS": ",".join(settings.web_origins),
        "CODETRAIL_GITHUB_IMPORT_ENABLED": str(int(settings.github_import_enabled)),
        "CODETRAIL_MCP_EXTERNAL_RUN_ENABLED": str(int(settings.external_run_enabled)),
    }
    if settings.typescript_compiler:
        values["CODETRAIL_TYPESCRIPT_COMPILER"] = settings.typescript_compiler
    for key, value in values.items():
        os.environ.setdefault(key, value)


def main() -> None:
    parser = argparse.ArgumentParser(prog="codetrail")
    parser.add_argument("--config", help="Settings file; otherwise CODETRAIL_CONFIG or user default")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create user settings without overwriting existing files")
    init.add_argument("--data-dir", default=str(Path.home() / ".local/share/codetrail"))
    init.add_argument("--allow-root", action="append", default=[])
    init.add_argument("--typescript-compiler")
    api = commands.add_parser("api", help="Start loopback HTTP API")
    api.add_argument("--port", type=int, default=8000)
    commands.add_parser("mcp", help="Start MCP stdio server")
    install = commands.add_parser("install", help="Register Codex MCP, preserving existing registration")
    install.add_argument("--replace", action="store_true")
    install.add_argument("--print-config", action="store_true")
    commands.add_parser("status", help="Show config location and Codex registration")
    commands.add_parser("uninstall", help="Remove only the Codex MCP registration; retain all data")
    args = parser.parse_args()
    path = config_path(args.config)
    try:
        if args.command == "init":
            if not path.exists():
                settings = Settings(data_dir=str(Path(args.data_dir).resolve()),
                    allowed_import_roots=[str(Path(root).resolve()) for root in args.allow_root],
                    typescript_compiler=str(Path(args.typescript_compiler).resolve()) if args.typescript_compiler else None)
                if any(not Path(root).is_dir() for root in settings.allowed_import_roots):
                    raise ValueError("allowed import roots must be existing directories")
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8") as file:
                    file.write(settings.model_dump_json(indent=2) + "\n")
            load_settings(path)
            print(path)
            return
        if args.command in {"install", "status", "uninstall"}:
            entry_args = (["--config", str(path)] if args.config or os.getenv("CODETRAIL_CONFIG") else []) + ["mcp"]
            if args.command == "install":
                if args.print_config:
                    print(json.dumps({"command": "codetrail", "args": entry_args}, indent=2))
                    return
                load_settings(path)
                existing = subprocess.run(["codex", "mcp", "get", "codetrail", "--json"], capture_output=True)
                if existing.returncode == 0 and not args.replace:
                    print("Existing codetrail registration retained; use --replace to update.")
                    return
                subprocess.run(["codex", "mcp", "add", "codetrail", "--", "codetrail", *entry_args], check=True)
            elif args.command == "status":
                print(f"Configuration: {path}")
                subprocess.run(["codex", "mcp", "get", "codetrail", "--json"], check=True)
            else:
                subprocess.run(["codex", "mcp", "remove", "codetrail"], check=True)
            return
        apply_settings(load_settings(path))
        if args.command == "api":
            import uvicorn
            uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)
        else:
            from .mcp_server import main as mcp_main
            mcp_main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"codetrail: {error}\n")


if __name__ == "__main__":
    main()
