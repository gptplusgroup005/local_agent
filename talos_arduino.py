from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from talos_core import ROOT

ARDUINO_EXTENSIONS = {".ino", ".h", ".hpp", ".c", ".cpp", ".S", ".txt", ".md"}
IGNORED_DIRS = {
    ".git",
    ".vs",
    ".vscode",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
MAX_CONTEXT_BYTES = 64_000
MAX_FILE_BYTES = 128_000
SANDBOX_ROOT = ROOT / ".talos_sandbox" / "arduino"


def arduino_config(config: dict[str, Any]) -> dict[str, str]:
    return {
        "workspace_path": str(config.get("arduino_workspace_path", "")).strip(),
        "fqbn": str(config.get("arduino_fqbn", "")).strip(),
    }


def is_source_file(path: Path) -> bool:
    return path.suffix in ARDUINO_EXTENSIONS


def resolve_workspace(path_text: str) -> Path | None:
    path_text = path_text.strip().strip('"')
    if not path_text:
        return None
    try:
        return Path(path_text).expanduser().resolve()
    except OSError:
        return None


def configured_workspace(config: dict[str, Any]) -> Path | None:
    workspace = resolve_workspace(arduino_config(config)["workspace_path"])
    if workspace is None or not workspace.exists() or not workspace.is_dir():
        return None
    return workspace


def resolve_workspace_file(config: dict[str, Any], relative_path: str, *, must_exist: bool = False) -> tuple[Path | None, str | None]:
    workspace = configured_workspace(config)
    if workspace is None:
        return None, "No valid Arduino sketch folder configured."
    clean_path = relative_path.strip().replace("\\", "/").lstrip("/")
    if not clean_path:
        return None, "File path is required."
    try:
        path = (workspace / clean_path).resolve()
        path.relative_to(workspace)
    except (OSError, ValueError):
        return None, "File path must stay inside the Arduino sketch folder."
    if not is_source_file(path):
        return None, f"Unsupported Arduino workspace file type: {path.suffix or '[none]'}"
    if must_exist and not path.exists():
        return None, "File was not found in the Arduino sketch folder."
    return path, None


def read_workspace_file(config: dict[str, Any], relative_path: str) -> dict[str, Any]:
    path, error = resolve_workspace_file(config, relative_path, must_exist=True)
    if error or path is None:
        return {"ok": False, "error": error}
    if path.stat().st_size > MAX_FILE_BYTES:
        return {"ok": False, "error": f"File is too large to read safely: {path.stat().st_size} bytes."}
    workspace = configured_workspace(config)
    assert workspace is not None
    return {
        "ok": True,
        "path": path.relative_to(workspace).as_posix(),
        "content": path.read_text(encoding="utf-8", errors="replace"),
        "bytes": path.stat().st_size,
    }


def write_workspace_file(config: dict[str, Any], relative_path: str, content: str) -> dict[str, Any]:
    path, error = resolve_workspace_file(config, relative_path)
    if error or path is None:
        return {"ok": False, "error": error}
    workspace = configured_workspace(config)
    assert workspace is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return {
        "ok": True,
        "path": path.relative_to(workspace).as_posix(),
        "bytes": path.stat().st_size,
    }


def delete_workspace_file(config: dict[str, Any], relative_path: str) -> dict[str, Any]:
    path, error = resolve_workspace_file(config, relative_path, must_exist=True)
    if error or path is None:
        return {"ok": False, "error": error}
    workspace = configured_workspace(config)
    assert workspace is not None
    path.unlink()
    return {"ok": True, "path": path.relative_to(workspace).as_posix()}


def iter_source_files(workspace: Path) -> list[Path]:
    files: list[Path] = []
    if not workspace.exists() or not workspace.is_dir():
        return files
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(workspace).parts
        if any(part in IGNORED_DIRS or part.startswith(".talos_") for part in rel_parts[:-1]):
            continue
        if is_source_file(path):
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(workspace).as_posix().lower())


def find_main_sketch(workspace: Path, files: list[Path] | None = None) -> Path | None:
    source_files = files if files is not None else iter_source_files(workspace)
    ino_files = [path for path in source_files if path.suffix.lower() == ".ino"]
    if not ino_files:
        return None
    preferred = workspace / f"{workspace.name}.ino"
    for path in ino_files:
        if path.resolve() == preferred.resolve():
            return path
    return ino_files[0]


def file_row(workspace: Path, path: Path) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        content = ""
    stat = path.stat()
    return {
        "path": path.relative_to(workspace).as_posix(),
        "bytes": stat.st_size,
        "lines": content.count("\n") + (1 if content else 0),
    }


def workspace_summary(config: dict[str, Any]) -> dict[str, Any]:
    arduino = arduino_config(config)
    workspace = resolve_workspace(arduino["workspace_path"])
    if workspace is None:
        return {
            "configured": False,
            "valid": False,
            "path": "",
            "fqbn": arduino["fqbn"],
            "main_sketch": "",
            "files": [],
            "message": "No Arduino sketch folder configured.",
        }
    if not workspace.exists() or not workspace.is_dir():
        return {
            "configured": True,
            "valid": False,
            "path": str(workspace),
            "fqbn": arduino["fqbn"],
            "main_sketch": "",
            "files": [],
            "message": "Configured Arduino sketch folder was not found.",
        }
    files = iter_source_files(workspace)
    main_sketch = find_main_sketch(workspace, files)
    return {
        "configured": True,
        "valid": main_sketch is not None,
        "path": str(workspace),
        "fqbn": arduino["fqbn"],
        "main_sketch": main_sketch.relative_to(workspace).as_posix() if main_sketch else "",
        "files": [file_row(workspace, path) for path in files],
        "message": "Arduino sketch folder ready." if main_sketch else "No .ino file found in this folder.",
    }


def workspace_context(config: dict[str, Any], max_bytes: int = MAX_CONTEXT_BYTES) -> str:
    summary = workspace_summary(config)
    if not summary["valid"]:
        return ""
    workspace = Path(summary["path"])
    lines = [
        "Arduino workspace context",
        f"Folder: {summary['path']}",
        f"Main sketch: {summary['main_sketch']}",
        f"FQBN: {summary['fqbn'] or 'not configured'}",
        "",
    ]
    used = len("\n".join(lines).encode("utf-8"))
    for row in summary["files"]:
        path = workspace / row["path"]
        if row["bytes"] > MAX_FILE_BYTES:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        block = f"--- {row['path']} ---\n{content.rstrip()}\n\n"
        block_size = len(block.encode("utf-8"))
        if used + block_size > max_bytes:
            lines.append("--- context truncated ---")
            break
        lines.append(block)
        used += block_size
    return "\n".join(lines).strip()


def copy_workspace_to_sandbox(workspace: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = SANDBOX_ROOT / f"{workspace.name}_{stamp}"
    target.parent.mkdir(parents=True, exist_ok=True)

    def ignore(_dir: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            if name in IGNORED_DIRS or name.startswith(".talos_"):
                ignored.add(name)
        return ignored

    shutil.copytree(workspace, target, ignore=ignore)
    return target


def run_arduino_compile(config: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    summary = workspace_summary(config)
    if not summary["valid"]:
        return {"ok": False, "status": "not_ready", "summary": summary, "output": summary["message"]}
    if not summary["fqbn"]:
        return {
            "ok": False,
            "status": "missing_fqbn",
            "summary": summary,
            "output": "Set an Arduino FQBN first, for example arduino:avr:uno.",
        }
    cli = shutil.which("arduino-cli")
    if cli is None:
        return {
            "ok": False,
            "status": "missing_cli",
            "summary": summary,
            "output": "arduino-cli was not found in PATH.",
        }
    workspace = Path(summary["path"])
    sandbox = copy_workspace_to_sandbox(workspace)
    command = [cli, "compile", "--fqbn", summary["fqbn"], str(sandbox)]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=sandbox,
        )
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(part for part in (exc.stdout, exc.stderr) if part)
        return {
            "ok": False,
            "status": "timeout",
            "summary": summary,
            "sandbox": str(sandbox),
            "command": " ".join(command),
            "output": output.strip() or f"arduino-cli compile timed out after {timeout} seconds.",
        }
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())
    return {
        "ok": completed.returncode == 0,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "summary": summary,
        "sandbox": str(sandbox),
        "command": " ".join(command),
        "output": output or f"arduino-cli exited with code {completed.returncode}.",
    }
