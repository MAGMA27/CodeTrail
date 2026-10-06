"""Generate a deterministic, non-executed architecture browsing fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path


RESPONSIBILITIES = (
    ("client", "Client", "gateway"),
    ("gateway", "Gateway", "application"),
    ("application", "Application", "worker"),
    ("worker", "Worker", "storage"),
    ("storage", "Storage", "reporting"),
    ("reporting", "Reporting", "client"),
)
MODULES_PER_RESPONSIBILITY = 20
EXTRA_DEFINITIONS = 45


def _contents() -> dict[str, str]:
    files: dict[str, str] = {}
    for name, _label, next_name in RESPONSIBILITIES:
        files[f"{name}/__init__.py"] = '"""Synthetic architecture fixture package."""\n'
        for ordinal in range(MODULES_PER_RESPONSIBILITY):
            path = f"{name}/part_{ordinal:02d}.py"
            lines = [
                '"""One deterministic module in the cross-responsibility ring."""',
                f"from {next_name}.part_{ordinal:02d} import dispatch as next_dispatch",
                "",
            ]
            if name == "gateway" and ordinal == 0:
                lines.extend(
                    [
                        "from fastapi import FastAPI",
                        "",
                        "app = FastAPI()",
                        "",
                        "",
                        "def dispatch():",
                        "    return next_dispatch()",
                        "",
                        "",
                        '@app.get("/api/items/{item_id}")',
                        "def read_item(item_id: str):",
                        "    return next_dispatch()",
                        "",
                    ]
                )
            elif name == "application" and ordinal == 0:
                lines.extend(
                    [
                        "from . import handlers, other_handlers",
                        "",
                        'HANDLER_MODULES = {"primary": handlers, "secondary": other_handlers}',
                        "",
                        "",
                        'def dispatch_dynamic(group: str = "primary"): ',
                        "    module = HANDLER_MODULES[group]",
                        '    handler = getattr(module, "handle")',
                        "    return handler()",
                        "",
                        "",
                        "def dispatch():",
                        "    dispatch_dynamic()",
                        "    return next_dispatch()",
                        "",
                    ]
                )
            else:
                lines.extend(["def dispatch():", "    return next_dispatch()", ""])
            if name == "storage" and ordinal == 0:
                for index in range(EXTRA_DEFINITIONS):
                    lines.extend(
                        [
                            f"def item_{index:03d}():",
                            f'    return "storage-item-{index:03d}"',
                            "",
                        ]
                    )
            files[path] = "\n".join(lines).rstrip() + "\n"

    files["application/handlers.py"] = '"""Primary dynamic handler."""\n\ndef handle():\n    return "primary"\n'
    files["application/other_handlers.py"] = '"""Alternate dynamic handler."""\n\ndef handle():\n    return "alternate"\n'
    files["web/api.ts"] = '''const apiBaseUrl = "http://example.invalid";
function requestJson(path: string, validator: unknown, init: RequestInit = {}) {
  return fetch(`${apiBaseUrl}${path}`, { ...init, headers: {}, cache: 'no-store' });
}
export function getItem(itemId: string) {
  return requestJson(`/api/items/${encodeURIComponent(itemId)}`, undefined);
}
export function getDynamic(path: string) {
  return requestJson(path, undefined);
}
'''

    manifest = {
        "schema_version": 1,
        "subsystems": [
            {
                "id": name,
                "label": label,
                "owners": [f"{name}/", "web/"] if name == "client" else [f"{name}/"],
            }
            for name, label, _next in RESPONSIBILITIES
        ]
    }
    files["codetrail-subsystems.json"] = json.dumps(
        manifest, ensure_ascii=False, indent=2
    ) + "\n"
    return files


def _write_empty_directory(output: Path) -> dict[str, str]:
    if output.is_symlink():
        raise ValueError("output must not be a symbolic link")
    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            raise ValueError("output must be an empty directory")
    else:
        output.mkdir(parents=True)
    files = _contents()
    for relative_path, content in sorted(files.items()):
        destination = output / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    return files


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="codetrail-architecture-generator-") as temp:
        root = Path(temp)
        first = root / "first"
        second = root / "second"
        generated = _write_empty_directory(first)
        _write_empty_directory(second)
        if _file_hashes(first) != _file_hashes(second):
            raise AssertionError("generation is not deterministic")
        source_module_count = sum(
            path.endswith((".py", ".ts", ".tsx")) for path in generated
        )
        definition_count = sum(
            line.startswith("def item_")
            for line in generated["storage/part_00.py"].splitlines()
        )
        if source_module_count < 120 or definition_count <= 40:
            raise AssertionError("fixture is too small for the planned budget checks")
        occupied = root / "occupied"
        occupied.mkdir()
        (occupied / "keep.txt").write_text("keep", encoding="utf-8")
        try:
            _write_empty_directory(occupied)
        except ValueError:
            pass
        else:
            raise AssertionError("nonempty output was not rejected")
    print(
        "generator self-test passed: deterministic, "
        f"{source_module_count} source modules, {definition_count} extra definitions, "
        "nonempty output rejected"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="empty destination; omit for a new temp directory")
    parser.add_argument("--self-test", action="store_true", help="check determinism and output safety")
    args = parser.parse_args()
    if args.self_test:
        if args.output is not None:
            parser.error("--output cannot be combined with --self-test")
        self_test()
        return
    output = args.output
    if output is None:
        output = Path(tempfile.mkdtemp(prefix="codetrail-architecture-fixture-"))
    files = _write_empty_directory(output)
    module_count = sum(path.endswith((".py", ".ts", ".tsx")) for path in files)
    manifest_hash = hashlib.sha256((output / "codetrail-subsystems.json").read_bytes()).hexdigest()
    print(f"fixture: {output.resolve()}")
    print(f"source modules: {module_count}; responsibility groups: {len(RESPONSIBILITIES)}")
    print(f"manifest sha256: {manifest_hash}")
    print("Generated source was not executed.")


if __name__ == "__main__":
    main()
