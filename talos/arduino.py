from __future__ import annotations

import shutil
import subprocess
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from talos.core import ROOT
from talos.native_bridge import (
    extract_ino_names,
    list_arduino_ide_processes,
    list_arduino_open_workspaces,
    list_arduino_tool_processes,
    list_arduino_workspace_boards,
    list_window_rows,
    list_window_titles,
    native_available,
)

ARDUINO_EXTENSIONS = {".ino", ".h", ".hpp", ".c", ".cpp", ".s", ".txt", ".md"}
IGNORED_DIRS = {
    ".git",
    ".vs",
    ".vscode",
    "__pycache__",
    ".cache",
    ".pio",
    "build",
    "cmake-build-debug",
    "cmake-build-release",
    "dist",
    "node_modules",
}
MAX_CONTEXT_BYTES = 64_000
MAX_FILE_BYTES = 128_000
SANDBOX_ROOT = ROOT / ".talos_sandbox" / "arduino"
ARDUINO_CLI_CANDIDATES = [
    Path.home() / "AppData" / "Local" / "Programs" / "Arduino IDE" / "resources" / "app" / "lib" / "backend" / "resources" / "arduino-cli.exe",
    Path("C:/Program Files/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe"),
    Path("C:/Program Files (x86)/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe"),
]
_ARDUINO_CLI_CACHE: str | None = None
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PROGRAM_MEMORY_RE = re.compile(
    r"Sketch uses\s+(?P<used>\d+)\s+bytes\s+\((?P<percent>\d+)%\).*?Maximum is\s+(?P<maximum>\d+)\s+bytes",
    re.IGNORECASE,
)
DYNAMIC_MEMORY_RE = re.compile(
    r"Global variables use\s+(?P<used>\d+)\s+bytes\s+\((?P<percent>\d+)%\).*?Maximum is\s+(?P<maximum>\d+)\s+bytes",
    re.IGNORECASE,
)
COMPILE_ISSUE_RE = re.compile(
    r"^(?P<file>[A-Za-z]:\\.*?|[^:\n]+):(?P<line>\d+):(?:(?P<column>\d+):)?\s*(?P<level>error|warning):\s*(?P<message>.+)$",
    re.IGNORECASE,
)

def resolve_workspace(path_text: str) -> Path | None:
    path_text = path_text.strip().strip('"')
    if not path_text:
        return None
    try:
        return Path(path_text).expanduser().resolve()
    except OSError:
        return None

def workspace_profile_key(path_text: str) -> str:
    workspace = resolve_workspace(path_text)
    return str(workspace).lower() if workspace is not None else ""

def normalize_environment_profile(profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = profile if isinstance(profile, dict) else {}
    try:
        baud_rate = int(profile.get("baud_rate") or 0)
    except (TypeError, ValueError):
        baud_rate = 0
    libraries = profile.get("libraries")
    if isinstance(libraries, str):
        libraries = libraries.replace("\r", "").replace(",", "\n").split("\n")
    library_names = []
    for library in libraries if isinstance(libraries, list) else []:
        name = str(library).strip()
        if name and name not in library_names:
            library_names.append(name)
    build_properties = profile.get("build_properties")
    if isinstance(build_properties, str):
        build_properties = build_properties.replace("\r", "").split("\n")
    properties = []
    for property_text in build_properties if isinstance(build_properties, list) else []:
        value = str(property_text).strip()
        if value and "=" in value and value not in properties:
            properties.append(value)
    build_flags = profile.get("build_flags")
    if isinstance(build_flags, str):
        build_flags = build_flags.replace("\r", "").split("\n")
    flags = []
    for flag in build_flags if isinstance(build_flags, list) else []:
        value = str(flag).strip()
        if value and value not in flags:
            flags.append(value)
    return {
        "fqbn": str(profile.get("fqbn") or "").strip(),
        "serial_port": str(profile.get("serial_port") or "").strip(),
        "baud_rate": baud_rate if 0 < baud_rate <= 4_000_000 else 0,
        "build_flags": flags[:32],
        "build_properties": properties[:16],
        "libraries": library_names[:32],
    }

def environment_profile(config: dict[str, Any], workspace_path: str | None = None) -> dict[str, Any]:
    path_text = workspace_path if workspace_path is not None else str(config.get("arduino_workspace_path") or "")
    key = workspace_profile_key(path_text)
    profiles = config.get("arduino_profiles")
    profile = profiles.get(key) if key and isinstance(profiles, dict) else None
    return normalize_environment_profile(profile)

def save_environment_profile(config: dict[str, Any], workspace_path: str, profile: dict[str, Any]) -> dict[str, Any]:
    workspace = resolve_workspace(workspace_path)
    if workspace is None or not workspace.exists() or not workspace.is_dir():
        return {"ok": False, "error": "Choose a valid Arduino sketch folder before saving its environment profile."}
    normalized = normalize_environment_profile(profile)
    profiles = config.get("arduino_profiles")
    profiles = dict(profiles) if isinstance(profiles, dict) else {}
    profiles[str(workspace).lower()] = {"path": str(workspace), **normalized}
    config["arduino_profiles"] = profiles
    return {"ok": True, "path": str(workspace), "profile": normalized}

def arduino_config(config: dict[str, Any]) -> dict[str, str]:
    workspace_path = str(config.get("arduino_workspace_path", "")).strip()
    profile = environment_profile(config, workspace_path)
    return {
        "workspace_path": workspace_path,
        "fqbn": str(profile.get("fqbn") or "") or str(config.get("arduino_fqbn", "")).strip(),
    }

def is_source_file(path: Path) -> bool:
    return path.suffix.lower() in ARDUINO_EXTENSIONS

def open_window_titles() -> list[str]:
    return list_window_titles()

def arduino_ide_status(
    processes: list[dict[str, object]] | None = None,
    tool_processes: list[dict[str, object]] | None = None,
    titles: list[str] | None = None,
) -> dict[str, Any]:
    processes = processes if processes is not None else list_arduino_ide_processes()
    tool_processes = tool_processes if tool_processes is not None else list_arduino_tool_processes()
    board = detected_board(tool_processes)
    titles = titles if titles is not None else [
        title for title in open_window_titles() if "arduino" in title.lower() or ".ino" in title.lower()
    ]
    windows = [
        {"pid": process["pid"], "title": process["title"]}
        for process in processes
        if str(process.get("title") or "").strip()
    ]
    known_window_titles = {str(window["title"]) for window in windows}
    for title in titles:
        if title not in known_window_titles:
            windows.append({"pid": 0, "title": title})
    return {
        "running": bool(processes or titles),
        "process_count": len(processes),
        "board_fqbn": board["fqbn"],
        "board_name": board["board_name"],
        "windows": windows,
    }

def detected_board(processes: list[dict[str, object]] | None = None) -> dict[str, str]:
    rows = processes if processes is not None else list_arduino_tool_processes()
    for process in rows:
        fqbn = str(process.get("fqbn") or "").strip()
        if fqbn:
            return {
                "fqbn": fqbn,
                "board_name": str(process.get("board_name") or "").strip(),
            }
    return {"fqbn": "", "board_name": ""}

def detected_boards(processes: list[dict[str, object]] | None = None) -> list[dict[str, str]]:
    rows = processes if processes is not None else list_arduino_tool_processes()
    boards: list[dict[str, str]] = []
    seen: set[str] = set()
    for process in rows:
        fqbn = str(process.get("fqbn") or "").strip()
        if not fqbn:
            continue
        key = fqbn.lower()
        if key in seen:
            continue
        seen.add(key)
        boards.append(
            {
                "fqbn": fqbn,
                "board_name": str(process.get("board_name") or "").strip(),
            }
        )
    return boards

def boards_by_window_title(
    window_rows: list[dict[str, object]],
    processes: list[dict[str, object]],
) -> dict[str, dict[str, str]]:
    process_by_pid = {
        int(process.get("pid") or 0): process
        for process in processes
        if int(process.get("pid") or 0)
    }
    language_servers = [
        process
        for process in processes
        if str(process.get("name") or "").lower() == "arduino-language-server.exe"
        and str(process.get("fqbn") or "").strip()
    ]
    plugin_hosts = [
        process
        for process in processes
        if "backend\\plugin-host" in str(process.get("command_line") or "").lower()
    ]
    plugin_boards: list[tuple[dict[str, object], dict[str, str]]] = []
    for plugin in plugin_hosts:
        plugin_pid = int(plugin.get("pid") or 0)
        server = next(
            (row for row in language_servers if int(row.get("parent_pid") or 0) == plugin_pid),
            None,
        )
        if server is None:
            continue
        plugin_boards.append(
            (
                plugin,
                {
                    "fqbn": str(server.get("fqbn") or "").strip(),
                    "board_name": str(server.get("board_name") or "").strip(),
                },
            )
        )

    candidates: list[tuple[str, int]] = []
    arduino_window_pids = [
        int(window.get("pid") or 0)
        for window in window_rows
        if "arduino ide" in str(window.get("title") or "").lower()
    ]
    if len(arduino_window_pids) != len(set(arduino_window_pids)):
        return {}
    for window in window_rows:
        title = str(window.get("title") or "").strip()
        if "arduino ide" not in title.lower():
            continue
        process = process_by_pid.get(int(window.get("pid") or 0), {})
        created_at = int(process.get("created_at") or 0)
        if created_at:
            candidates.append((title, created_at))

    results: dict[str, dict[str, str]] = {}
    unused = list(plugin_boards)
    for title, window_created_at in sorted(candidates, key=lambda item: item[1]):
        if not unused:
            break
        index, (plugin, board) = min(
            enumerate(unused),
            key=lambda item: abs(int(item[1][0].get("created_at") or 0) - window_created_at),
        )
        delta = abs(int(plugin.get("created_at") or 0) - window_created_at)
        if delta <= 15000:
            results[title] = board
            unused.pop(index)
    return results

def board_match_tokens(board: dict[str, str]) -> list[str]:
    fqbn = board.get("fqbn", "")
    board_name = board.get("board_name", "")
    tokens: list[str] = []
    parts = fqbn.split(":")
    if len(parts) >= 3:
        tokens.append(parts[2])
    for part in board_name.split():
        tokens.append(part)
    normalized: list[str] = []
    for token in tokens:
        clean = "".join(char.lower() for char in token if char.isalnum())
        if len(clean) >= 4 and clean not in normalized:
            normalized.append(clean)
    return normalized

def base_fqbn(fqbn: str) -> str:
    return ":".join(fqbn.split(":")[:3])

def match_project_board(
    project: dict[str, Any],
    boards: list[dict[str, str]],
    workspace_boards: dict[str, dict[str, str]] | None = None,
) -> dict[str, str]:
    project_path = str(project.get("path") or "").lower()
    stored = (workspace_boards or {}).get(project_path)
    if stored:
        stored_fqbn = stored.get("fqbn", "")
        for board in boards:
            if base_fqbn(board.get("fqbn", "")) == base_fqbn(stored_fqbn):
                return board
        return {
            "fqbn": stored_fqbn,
            "board_name": stored.get("name", ""),
        }
    if not boards:
        return {"fqbn": "", "board_name": ""}
    if len(boards) == 1:
        return boards[0]
    text = " ".join(
        str(project.get(key, ""))
        for key in ("title", "sketch", "path")
    )
    normalized_text = "".join(char.lower() if char.isalnum() else " " for char in text)
    compact_text = normalized_text.replace(" ", "")
    scored: list[tuple[int, dict[str, str]]] = []
    for board in boards:
        score = 0
        for token in board_match_tokens(board):
            if token in compact_text:
                score += 3 + len(token)
            elif token in normalized_text.split():
                score += 2 + len(token)
        scored.append((score, board))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][1]
    return boards[0]

def apply_project_board(
    project: dict[str, Any],
    boards: list[dict[str, str]],
    workspace_boards: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    board = match_project_board(project, boards, workspace_boards)
    project["fqbn"] = board.get("fqbn", "")
    project["board_name"] = board.get("board_name", "")
    project["board_source"] = "workspace_state" if (workspace_boards or {}).get(str(project.get("path") or "").lower()) else "process"
    return project

def append_root_with_ancestors(roots: list[Path], root: Path, levels: int = 2) -> None:
    current = root
    for _index in range(levels + 1):
        roots.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent

def arduino_search_roots(config: dict[str, Any], extra_roots: list[Path] | None = None) -> list[Path]:
    roots: list[Path] = []
    configured = configured_workspace(config)
    if configured is not None:
        append_root_with_ancestors(roots, configured, levels=2)
    for root in extra_roots or []:
        append_root_with_ancestors(roots, root, levels=2)
    for raw_root in str(config.get("arduino_search_roots", "")).split(";"):
        root = resolve_workspace(raw_root)
        if root is not None:
            roots.append(root)
    home = Path.home()
    roots.extend(
        [
            home / "Documents" / "Arduino",
            home / "OneDrive" / "Documents" / "Arduino",
            home / "Arduino",
            home / "Desktop",
            home / "Downloads",
        ]
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key not in seen and resolved.exists() and resolved.is_dir():
            unique.append(resolved)
            seen.add(key)
    return unique

def find_sketch_folder(sketch_name: str, roots: list[Path]) -> Path | None:
    sketch_path = Path(sketch_name)
    folder_name = sketch_path.stem
    for root in roots:
        if (root / sketch_path.name).exists():
            return root.resolve()
        direct = root / folder_name
        if (direct / sketch_path.name).exists():
            return direct.resolve()
    for root in roots:
        try:
            for match in root.rglob(sketch_path.name):
                if match.is_file():
                    return match.parent.resolve()
        except OSError:
            continue
    return None

def is_unsaved_arduino_temp_folder(folder: Path) -> bool:
    return any(part.startswith(".arduinoIDE-unsaved") for part in folder.parts)

def find_saved_sketch_folder(sketch_name: str, roots: list[Path]) -> Path | None:
    folder = find_sketch_folder(sketch_name, roots)
    if folder is None or is_unsaved_arduino_temp_folder(folder):
        return None
    return folder

def sketch_project_from_path(
    path_text: str,
    title: str = "",
    source: str = "process",
) -> dict[str, Any] | None:
    path = resolve_workspace(path_text)
    if path is None or not path.exists() or not path.is_file() or path.suffix.lower() != ".ino":
        return None
    folder = path.parent.resolve()
    return {
        "title": title,
        "sketch": path.name,
        "path": str(folder),
        "fqbn": "",
        "board_name": "",
        "valid": True,
        "native": native_available(),
        "source": source,
        "status": "ready",
        "unsaved": False,
        "message": "Open Arduino sketch found.",
    }

def sketch_stem(sketch_name: str) -> str:
    return Path(sketch_name).stem.lower()

def path_matches_title_sketch(path_text: str, title_sketches: list[str]) -> bool:
    path = resolve_workspace(path_text)
    if path is None:
        return False
    title_stems = {sketch_stem(sketch) for sketch in title_sketches}
    return path.stem.lower() in title_stems or path.parent.name.lower() in title_stems

def title_looks_unsaved_sketch(title_sketches: list[str]) -> bool:
    return any(sketch_stem(sketch).startswith("sketch_") for sketch in title_sketches)

def should_ignore_stale_process_paths(
    open_ino_paths: list[str],
    title_sketches: list[str],
    roots: list[Path],
    trusted_roots: list[Path],
) -> bool:
    if len(open_ino_paths) != 1 or not title_sketches:
        return False
    if any(path_matches_title_sketch(path_text, title_sketches) for path_text in open_ino_paths):
        return False
    if title_looks_unsaved_sketch(title_sketches):
        return True
    if any(find_saved_sketch_folder(sketch, trusted_roots) is not None for sketch in title_sketches):
        return False
    return not any(find_saved_sketch_folder(sketch, roots) is not None for sketch in title_sketches)

def discover_arduino_projects(
    config: dict[str, Any],
    titles: list[str] | None = None,
    ino_paths: list[str] | None = None,
    ide_processes: list[dict[str, object]] | None = None,
    tool_processes: list[dict[str, object]] | None = None,
    window_rows: list[dict[str, object]] | None = None,
    open_workspaces: list[dict[str, object]] | None = None,
    workspace_boards: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    auto_detection = titles is None and ino_paths is None
    processes = ide_processes if ide_processes is not None else (
        list_arduino_ide_processes() if auto_detection else []
    )
    arduino_processes = tool_processes if tool_processes is not None else (
        list_arduino_tool_processes() if auto_detection else []
    )
    open_workspace_rows = open_workspaces if open_workspaces is not None else (
        list_arduino_open_workspaces() if auto_detection else []
    )
    workspace_board_map = workspace_boards if workspace_boards is not None else (
        list_arduino_workspace_boards() if auto_detection else {}
    )
    boards = detected_boards(arduino_processes)
    detected_window_rows = window_rows if window_rows is not None else (
        list_window_rows() if auto_detection else []
    )
    title_boards = boards_by_window_title(detected_window_rows, arduino_processes)
    window_titles = titles if titles is not None else []
    if titles is None:
        window_titles.extend(
            str(process.get("title") or "")
            for process in processes
            if str(process.get("title") or "").strip()
        )
        source_titles = [
            str(row.get("title") or "")
            for row in detected_window_rows
            if str(row.get("title") or "").strip()
        ] or open_window_titles()
        for title in source_titles:
            lower = title.lower()
            if ("arduino" in lower or ".ino" in lower) and title not in window_titles:
                window_titles.append(title)
    open_ino_paths = list(ino_paths or [])
    path_sources: dict[str, str] = {}
    if ino_paths is None:
        for process in processes:
            for path in process.get("ino_paths", []):
                if isinstance(path, str):
                    open_ino_paths.append(path)
                    path_sources[path] = "process"
    path_roots: list[Path] = []
    for path_text in open_ino_paths:
        path = resolve_workspace(path_text)
        if path is not None:
            path_roots.append(path.parent if path.suffix else path)
    for workspace in open_workspace_rows:
        folder = resolve_workspace(str(workspace.get("path") or ""))
        if folder is not None:
            path_roots.append(folder)
    trusted_roots = arduino_search_roots(config)
    roots = arduino_search_roots(config, extra_roots=path_roots)
    title_sketches: list[str] = []
    for title in window_titles:
        lower = title.lower()
        if "arduino" not in lower and ".ino" not in lower:
            continue
        for sketch in extract_ino_names(title):
            if sketch not in title_sketches:
                title_sketches.append(sketch)
    if should_ignore_stale_process_paths(open_ino_paths, title_sketches, roots, trusted_roots):
        open_ino_paths = []
    title_folders: dict[tuple[str, str], Path | None] = {}
    live_folder_keys: set[str] = set()
    for title in window_titles:
        for sketch in extract_ino_names(title):
            folder = find_saved_sketch_folder(sketch, roots)
            title_folders[(title, sketch)] = folder
            if folder is not None:
                live_folder_keys.add(str(folder).lower())
    claimed_board_bases = {
        base_fqbn(str(board.get("fqbn") or ""))
        for path, board in workspace_board_map.items()
        if path in live_folder_keys and str(board.get("fqbn") or "")
    }
    projects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path_text in open_ino_paths:
        project = sketch_project_from_path(path_text, source=path_sources.get(path_text, "process"))
        if project is None:
            continue
        apply_project_board(project, boards, workspace_board_map)
        key = str(Path(project["path"]).resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        project.update(project_source_metadata(Path(project["path"])))
        projects.append(project)
    for title in window_titles:
        lower = title.lower()
        if "arduino" not in lower and ".ino" not in lower:
            continue
        for sketch in extract_ino_names(title):
            folder = title_folders.get((title, sketch))
            unsaved = folder is None and title_looks_unsaved_sketch([sketch])
            key = str(folder).lower() if folder else f"{title.lower()}::{sketch.lower()}"
            if key in seen:
                continue
            seen.add(key)
            project = {
                "title": title,
                "sketch": sketch,
                "path": str(folder) if folder else "",
                "fqbn": "",
                "board_name": "",
                "valid": folder is not None,
                "native": native_available(),
                "source": "window_title",
                "status": "ready" if folder else ("unsaved" if unsaved else "folder_missing"),
                "unsaved": unsaved,
                "message": (
                    "Sketch folder found."
                    if folder
                    else (
                        "Unsaved Arduino sketch detected. Save it in Arduino IDE before selecting it as a workspace."
                        if unsaved
                        else "Open Arduino sketch detected, but matching folder was not found in search roots."
                    )
                ),
            }
            project.update(project_source_metadata(folder))
            title_board = title_boards.get(title)
            if title_board:
                project["fqbn"] = title_board["fqbn"]
                project["board_name"] = title_board["board_name"]
                project["board_source"] = "process_tree"
                projects.append(project)
            else:
                exact_board = workspace_board_map.get(str(folder).lower()) if folder is not None else None
                unclaimed_boards = [
                    board for board in boards
                    if base_fqbn(board.get("fqbn", "")) not in claimed_board_bases
                ]
                if exact_board is None and len(unclaimed_boards) == 1:
                    board = unclaimed_boards[0]
                    project["fqbn"] = board["fqbn"]
                    project["board_name"] = board["board_name"]
                    project["board_source"] = "remaining_process"
                    claimed_board_bases.add(base_fqbn(board["fqbn"]))
                    projects.append(project)
                else:
                    projects.append(apply_project_board(project, boards, workspace_board_map))
    return projects

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
    stat = path.stat()
    if stat.st_size > MAX_FILE_BYTES:
        return {"ok": False, "error": f"File is too large to read safely: {stat.st_size} bytes."}
    workspace = configured_workspace(config)
    assert workspace is not None
    return {
        "ok": True,
        "path": path.relative_to(workspace).as_posix(),
        "content": path.read_text(encoding="utf-8", errors="replace"),
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }

def write_workspace_file(config: dict[str, Any], relative_path: str, content: str) -> dict[str, Any]:
    path, error = resolve_workspace_file(config, relative_path)
    if error or path is None:
        return {"ok": False, "error": error}
    workspace = configured_workspace(config)
    assert workspace is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    stat = path.stat()
    return {
        "ok": True,
        "path": path.relative_to(workspace).as_posix(),
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
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

def project_source_metadata(folder: Path | None) -> dict[str, Any]:
    if folder is None or not folder.exists() or not folder.is_dir():
        return {"source_count": 0, "source_files": []}
    files = iter_source_files(folder)
    return {
        "source_count": len(files),
        "source_files": [path.relative_to(folder).as_posix() for path in files],
    }

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

def workspace_map(config: dict[str, Any], latest_verify: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return compact Arduino workspace metadata suitable for a Codex prompt or UI chip."""
    summary = workspace_summary(config)
    files = summary.get("files") if isinstance(summary.get("files"), list) else []
    verify = latest_verify if isinstance(latest_verify, dict) else {}
    result = verify.get("result") if isinstance(verify.get("result"), dict) else {}
    profile = environment_profile(config, str(summary.get("path") or ""))
    source_tabs = [
        {
            "path": str(file.get("path") or ""),
            "lines": int(file.get("lines") or 0),
            "bytes": int(file.get("bytes") or 0),
        }
        for file in files[:24]
        if isinstance(file, dict)
    ]
    return {
        "valid": bool(summary.get("valid")),
        "workspace": str(summary.get("path") or ""),
        "main_sketch": str(summary.get("main_sketch") or ""),
        "board": {"fqbn": str(summary.get("fqbn") or "")},
        "environment_profile": profile,
        "source_tabs": source_tabs,
        "source_tab_count": len(files),
        "diagnostics": {
            "status": str(verify.get("status") or ""),
            "time": str(verify.get("time") or ""),
            "issues": list(result.get("issues") or [])[:12],
            "libraries": list(result.get("libraries") or [])[:12],
            "platforms": list(result.get("platforms") or [])[:6],
        },
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
    target = SANDBOX_ROOT / stamp / workspace.name
    target.parent.mkdir(parents=True, exist_ok=True)

    def ignore(_dir: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            if name in IGNORED_DIRS or name.startswith(".talos_"):
                ignored.add(name)
        return ignored

    shutil.copytree(workspace, target, ignore=ignore)
    return target

def find_arduino_cli() -> str | None:
    global _ARDUINO_CLI_CACHE
    if _ARDUINO_CLI_CACHE:
        return _ARDUINO_CLI_CACHE
    cli = shutil.which("arduino-cli")
    if cli is not None:
        _ARDUINO_CLI_CACHE = cli
        return cli
    for candidate in ARDUINO_CLI_CANDIDATES:
        try:
            if candidate.exists() and candidate.is_file():
                _ARDUINO_CLI_CACHE = str(candidate)
                return _ARDUINO_CLI_CACHE
        except OSError:
            continue
    return None

def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)

def parse_memory_line(pattern: re.Pattern[str], output: str) -> dict[str, int] | None:
    match = pattern.search(output)
    if match is None:
        return None
    return {
        "used": int(match.group("used")),
        "percent": int(match.group("percent")),
        "maximum": int(match.group("maximum")),
    }

def parse_named_table(output: str, title: str) -> list[dict[str, str]]:
    lines = output.splitlines()
    rows: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        if not line.strip().lower().startswith(title.lower()):
            continue
        for row in lines[index + 1:]:
            clean = row.strip()
            if not clean:
                break
            if "version" in clean.lower() and "path" in clean.lower():
                continue
            parts = re.split(r"\s{2,}", clean, maxsplit=2)
            if len(parts) >= 3:
                rows.append({"name": parts[0], "version": parts[1], "path": parts[2]})
        break
    return rows

def parse_compile_issues(output: str) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    for line in output.splitlines():
        match = COMPILE_ISSUE_RE.match(line.strip())
        if match is None:
            continue
        issues.append(
            {
                "file": match.group("file"),
                "line": int(match.group("line")),
                "column": int(match.group("column") or 0),
                "level": match.group("level").lower(),
                "message": match.group("message").strip(),
            }
        )
    return issues

def format_compile_issue_context(issues: list[dict[str, object]]) -> str:
    if not issues:
        return ""
    lines = ["Arduino compile issues:"]
    for issue in issues:
        file_name = re.split(r"[\\/]", str(issue.get("file") or ""))[-1] or "compiler"
        line = int(issue.get("line") or 0)
        column = int(issue.get("column") or 0)
        location = ":".join(
            part for part in (file_name, str(line) if line else "", str(column) if column else "") if part
        )
        level = str(issue.get("level") or "error").upper()
        message = str(issue.get("message") or "Unknown compiler issue.").strip()
        lines.append(f"{level} {location} - {message}")
    return "\n".join(lines)

def parse_compile_output(output: str) -> dict[str, Any]:
    clean_output = strip_ansi(output).strip()
    issues = parse_compile_issues(clean_output)
    return {
        "output": clean_output,
        "memory": {
            "program": parse_memory_line(PROGRAM_MEMORY_RE, clean_output),
            "dynamic": parse_memory_line(DYNAMIC_MEMORY_RE, clean_output),
        },
        "libraries": parse_named_table(clean_output, "Used library"),
        "platforms": parse_named_table(clean_output, "Used platform"),
        "issues": issues,
        "issue_context": format_compile_issue_context(issues),
    }

def run_arduino_compile(
    config: dict[str, Any],
    timeout: int = 120,
    overrides: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    timings: dict[str, float] = {}

    def mark(name: str, step_started_at: float) -> float:
        timings[name] = round(time.perf_counter() - step_started_at, 3)
        return time.perf_counter()

    def with_total(payload: dict[str, Any]) -> dict[str, Any]:
        timings["total"] = round(time.perf_counter() - started_at, 3)
        payload["timings"] = timings.copy()
        return payload

    step_started_at = time.perf_counter()
    summary = workspace_summary(config)
    step_started_at = mark("prepare", step_started_at)
    if not summary["valid"]:
        return with_total({"ok": False, "status": "not_ready", "summary": summary, "output": summary["message"]})
    if not summary["fqbn"]:
        return with_total({
            "ok": False,
            "status": "missing_fqbn",
            "summary": summary,
            "output": "Set an Arduino FQBN first, for example arduino:avr:uno.",
        })
    cli = find_arduino_cli()
    step_started_at = mark("cli_lookup", step_started_at)
    if cli is None:
        return with_total({
            "ok": False,
            "status": "missing_cli",
            "summary": summary,
            "output": "arduino-cli was not found in PATH or in the Arduino IDE bundled resources folder.",
        })
    workspace = Path(summary["path"])
    sandbox = copy_workspace_to_sandbox(workspace)
    for relative_path, content in (overrides or {}).items():
        target = (sandbox / relative_path).resolve()
        try:
            target.relative_to(sandbox)
        except ValueError:
            return with_total({
                "ok": False,
                "status": "invalid_override",
                "summary": summary,
                "sandbox": str(sandbox),
                "output": "Staged change path must stay inside the Arduino sandbox.",
            })
        if not is_source_file(target):
            return with_total({
                "ok": False,
                "status": "invalid_override",
                "summary": summary,
                "sandbox": str(sandbox),
                "output": f"Unsupported staged source file type: {target.suffix or '[none]'}",
            })
        if content is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
    step_started_at = mark("sandbox_copy", step_started_at)
    profile = environment_profile(config, str(summary.get("path") or ""))
    command = [cli, "compile", "--fqbn", summary["fqbn"]]
    if profile["build_flags"]:
        command.extend(["--build-property", f"compiler.cpp.extra_flags={' '.join(profile['build_flags'])}"])
    for build_property in profile["build_properties"]:
        command.extend(["--build-property", build_property])
    command.append(str(sandbox))
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=sandbox,
        )
        step_started_at = mark("compile", step_started_at)
    except subprocess.TimeoutExpired as exc:
        mark("compile", step_started_at)
        parsed = parse_compile_output("\n".join(part for part in (exc.stdout, exc.stderr) if part))
        return with_total({
            "ok": False,
            "status": "timeout",
            "summary": summary,
            "sandbox": str(sandbox),
            "command": " ".join(command),
            "output": parsed["output"] or f"arduino-cli compile timed out after {timeout} seconds.",
            "memory": parsed["memory"],
            "libraries": parsed["libraries"],
            "platforms": parsed["platforms"],
            "issues": parsed["issues"],
            "issue_context": parsed["issue_context"],
        })
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())
    parsed = parse_compile_output(output)
    mark("parse_output", step_started_at)
    return with_total({
        "ok": completed.returncode == 0,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "summary": summary,
        "sandbox": str(sandbox),
        "command": " ".join(command),
        "output": parsed["output"] or f"arduino-cli exited with code {completed.returncode}.",
        "memory": parsed["memory"],
        "libraries": parsed["libraries"],
        "platforms": parsed["platforms"],
        "issues": parsed["issues"],
        "issue_context": parsed["issue_context"],
    })
