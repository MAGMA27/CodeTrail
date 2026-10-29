"""Installed API/MCP entry point sharing one editable user configuration."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from pydantic import BaseModel, ConfigDict, Field

from .indexing import TYPESCRIPT_COMPILER_VERSION


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_dir: str = Field(min_length=1)
    allowed_import_roots: list[str] = Field(default_factory=list)
    workbench_url: str = "http://localhost:3000"
    web_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"])
    github_import_enabled: bool = True
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


def discover_typescript_compiler(cwd: Path | None = None) -> Path | None:
    """Find the locked compiler only inside a recognizable CodeTrail workspace."""
    directory = (cwd or Path.cwd()).resolve()
    root = None
    for candidate in (directory, *directory.parents):
        try:
            package = json.loads((candidate / "package.json").read_text(encoding="utf-8"))
            web_package = json.loads((candidate / "apps/web/package.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
            isinstance(package, dict)
            and isinstance(web_package, dict)
            and package.get("name") == "codetrail"
            and web_package.get("name") == "@codetrail/web"
            and (candidate / "apps/api/app/cli.py").is_file()
        ):
            root = candidate.resolve()
            break
    if root is None:
        return None

    for package_dir in (root / "apps/web/node_modules/typescript", root / "node_modules/typescript"):
        try:
            package_dir = package_dir.resolve(strict=True)
            if not package_dir.is_relative_to(root):
                continue
            package = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        compiler = package_dir / "lib/typescript.js"
        if not isinstance(package, dict) or package.get("name") != "typescript" or package.get("version") != TYPESCRIPT_COMPILER_VERSION:
            continue
        try:
            compiler = compiler.resolve(strict=True)
        except OSError:
            continue
        if compiler.is_file() and compiler.is_relative_to(root):
            return compiler
    return None


def warn_typescript_unavailable(settings_preserved: bool = False) -> None:
    message = (
        f"codetrail: locked TypeScript {TYPESCRIPT_COMPILER_VERSION} compiler not found; Python indexing remains available, "
        "but TypeScript/TSX indexing will fail. Run pnpm install --frozen-lockfile in the CodeTrail "
        "workspace or configure typescript_compiler / CODETRAIL_TYPESCRIPT_COMPILER."
    )
    if settings_preserved:
        message += " Existing settings were preserved; edit that file or use the environment variable."
    print(message, file=sys.stderr)


def warn_typescript_not_persisted() -> None:
    print(
        f"codetrail: TypeScript {TYPESCRIPT_COMPILER_VERSION} was found in this workspace, but existing settings were preserved; "
        "API/MCP started from another directory need typescript_compiler or CODETRAIL_TYPESCRIPT_COMPILER configured.",
        file=sys.stderr,
    )


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
    compiler = settings.typescript_compiler or discover_typescript_compiler()
    if compiler:
        values["CODETRAIL_TYPESCRIPT_COMPILER"] = str(compiler)
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
    commands.add_parser("worker", help="Start the Redis-backed asynchronous job worker")
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
            created = not path.exists()
            compiler = None
            if created:
                compiler = Path(args.typescript_compiler).resolve() if args.typescript_compiler else discover_typescript_compiler()
                settings = Settings(data_dir=str(Path(args.data_dir).resolve()),
                    allowed_import_roots=[str(Path(root).resolve()) for root in (args.allow_root or [str(Path.cwd())])],
                    typescript_compiler=str(compiler) if compiler else None)
                if any(not Path(root).is_dir() for root in settings.allowed_import_roots):
                    raise ValueError("allowed import roots must be existing directories")
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8") as file:
                    file.write(settings.model_dump_json(indent=2) + "\n")
            settings = load_settings(path)
            configured_compiler = settings.typescript_compiler or os.getenv("CODETRAIL_TYPESCRIPT_COMPILER")
            if not compiler and not created:
                compiler = discover_typescript_compiler()
            if not (configured_compiler or compiler):
                warn_typescript_unavailable(settings_preserved=not created)
            elif not created and not configured_compiler:
                warn_typescript_not_persisted()
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
        if args.command == "worker" and not path.exists():
            # Container workers can be configured entirely through their explicit environment.
            # The worker does not need an interactive user's CLI settings file.
            pass
        else:
            apply_settings(load_settings(path))
        if args.command == "worker":
            if not os.getenv("CODETRAIL_REDIS_URL"):
                raise ValueError("CODETRAIL_REDIS_URL is required to run the worker")
            import logging

            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
            from arq.worker import run_worker

            from .worker import worker_settings

            run_worker(worker_settings())
        elif args.command == "api":
            import uvicorn
            uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)
        else:
            from .mcp_server import main as mcp_main
            mcp_main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"codetrail: {error}\n")


if __name__ == "__main__":
    main()
