from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote

from talos.core import ROOT

ASSET_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
DLL_CANDIDATES = [
    ASSET_ROOT / "native" / "bin" / "talos_native.dll",
    ROOT / "native" / "bin" / "talos_native.dll",
]
TITLE_BUFFER_CHARS = 65536
WINDOW_ROW_BUFFER_CHARS = 131072
PROCESS_ROW_BUFFER_CHARS = 131072
INO_BUFFER_CHARS = 4096
_CACHE_TTL_SECONDS = 0.45
_COMMAND_LINE_CACHE_TTL_SECONDS = 3.0
_CACHE: dict[str, tuple[float, object]] = {}
_HAS_NATIVE_WINDOW_ROWS = False
_HAS_NATIVE_PROCESS_ROWS = False
ARDUINO_IDE_CONFIG = Path.home() / "AppData" / "Roaming" / "arduino-ide" / "config.json"
ARDUINO_IDE_LEVELDB = Path.home() / "AppData" / "Roaming" / "arduino-ide" / "Local Storage" / "leveldb"
WORKSPACE_BOARD_KEY = b":arduino-ide:boardListHistory"
BOARD_JSON_RE = re.compile(rb'\\"name\\":\\"([^"]+?)\\",\\"fqbn\\":\\"([^"]+?)\\"')

def _load_library() -> ctypes.CDLL | None:
    global _HAS_NATIVE_PROCESS_ROWS, _HAS_NATIVE_WINDOW_ROWS
    if os.name != "nt":
        return None
    dll_path = next((path for path in DLL_CANDIDATES if path.exists()), None)
    if dll_path is None:
        return None
    library = ctypes.CDLL(str(dll_path))
    library.talos_list_window_titles.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
    library.talos_list_window_titles.restype = ctypes.c_int
    library.talos_extract_ino_names.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    library.talos_extract_ino_names.restype = ctypes.c_int
    try:
        library.talos_list_window_rows.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
        library.talos_list_window_rows.restype = ctypes.c_int
        _HAS_NATIVE_WINDOW_ROWS = True
    except AttributeError:
        _HAS_NATIVE_WINDOW_ROWS = False
    try:
        library.talos_list_arduino_process_rows.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
        library.talos_list_arduino_process_rows.restype = ctypes.c_int
        _HAS_NATIVE_PROCESS_ROWS = True
    except AttributeError:
        _HAS_NATIVE_PROCESS_ROWS = False
    return library

_LIBRARY = _load_library()

def native_available() -> bool:
    return _LIBRARY is not None

def cached_value(key: str, loader):
    return cached_value_ttl(key, loader, _CACHE_TTL_SECONDS)

def cached_value_ttl(key: str, loader, ttl_seconds: float):
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[0] <= ttl_seconds:
        return cached[1]
    value = loader()
    _CACHE[key] = (now, value)
    return value

def list_window_titles() -> list[str]:
    if _LIBRARY is not None:
        buffer = ctypes.create_unicode_buffer(TITLE_BUFFER_CHARS)
        _LIBRARY.talos_list_window_titles(buffer, TITLE_BUFFER_CHARS)
        return [line for line in buffer.value.splitlines() if line.strip()]
    return list(cached_value("window_titles", list_window_titles_fallback))

def list_window_rows() -> list[dict[str, object]]:
    if os.name != "nt":
        return []
    if _LIBRARY is not None and _HAS_NATIVE_WINDOW_ROWS:
        buffer = ctypes.create_unicode_buffer(WINDOW_ROW_BUFFER_CHARS)
        _LIBRARY.talos_list_window_rows(buffer, WINDOW_ROW_BUFFER_CHARS)
        return parse_window_rows_payload(buffer.value)
    return list(cached_value("window_rows", list_window_rows_win32))

def parse_window_rows_payload(payload: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for raw_line in str(payload or "").splitlines():
        pid_text, separator, title = raw_line.partition("\t")
        if not separator:
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            pid = 0
        title = title.strip()
        if title:
            rows.append({"pid": pid, "title": title})
    return rows

def list_arduino_process_rows_native() -> list[dict[str, object]]:
    if os.name != "nt" or _LIBRARY is None or not _HAS_NATIVE_PROCESS_ROWS:
        return []
    buffer = ctypes.create_unicode_buffer(PROCESS_ROW_BUFFER_CHARS)
    _LIBRARY.talos_list_arduino_process_rows(buffer, PROCESS_ROW_BUFFER_CHARS)
    return parse_process_rows_payload(buffer.value)

def parse_process_rows_payload(payload: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for raw_line in str(payload or "").splitlines():
        parts = raw_line.split("\t", 3)
        if len(parts) != 4:
            continue
        name, pid_text, parent_text, created_text = parts
        try:
            pid = int(pid_text)
        except ValueError:
            pid = 0
        try:
            parent_pid = int(parent_text)
        except ValueError:
            parent_pid = 0
        try:
            created_at = int(created_text)
        except ValueError:
            created_at = 0
        if name.strip():
            rows.append(
                {
                    "name": name.strip(),
                    "pid": pid,
                    "parent_pid": parent_pid,
                    "created_at": created_at,
                    "title": "",
                    "command_line": "",
                    "ino_paths": [],
                    "fqbn": "",
                    "board_name": "",
                }
            )
    return rows

def list_window_rows_win32() -> list[dict[str, object]]:
    user32 = ctypes.windll.user32
    rows: list[dict[str, object]] = []
    enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def collect(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if not title:
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rows.append({"pid": int(pid.value), "title": title})
        return True

    callback = enum_proc_type(collect)
    user32.EnumWindows(callback, 0)
    return rows

def list_window_titles_fallback() -> list[str]:
    if os.name != "nt":
        return []
    titles = list_window_titles_win32_fallback()
    if titles:
        return titles
    command = powershell_command(
        "Get-Process | Where-Object { $_.MainWindowTitle } | Select-Object -ExpandProperty MainWindowTitle"
    )
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    titles: list[str] = []
    for line in completed.stdout.splitlines():
        title = line.strip()
        if title and title not in titles:
            titles.append(title)
    return titles

def list_window_titles_win32_fallback() -> list[str]:
    script = r"""
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;

public static class TalosWindows {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc enumProc, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
}
"@

$titles = New-Object System.Collections.Generic.List[string]
[TalosWindows]::EnumWindows({
    param([IntPtr]$hwnd, [IntPtr]$lparam)
    if ([TalosWindows]::IsWindowVisible($hwnd)) {
        $builder = New-Object System.Text.StringBuilder 512
        [void][TalosWindows]::GetWindowText($hwnd, $builder, $builder.Capacity)
        $title = $builder.ToString().Trim()
        if ($title) {
            $titles.Add($title)
        }
    }
    return $true
}, [IntPtr]::Zero) | Out-Null
$titles | Sort-Object -Unique
"""
    try:
        completed = subprocess.run(powershell_command(script), capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    titles: list[str] = []
    for line in completed.stdout.splitlines():
        title = line.strip()
        if title and title not in titles:
            titles.append(title)
    return titles

def list_arduino_ide_processes() -> list[dict[str, object]]:
    if os.name != "nt":
        return []
    return list(cached_value("arduino_ide_processes", list_arduino_ide_processes_uncached))

def list_arduino_tool_processes() -> list[dict[str, object]]:
    if os.name != "nt":
        return []
    return list(cached_value("arduino_tool_processes", list_arduino_tool_processes_uncached))

def list_arduino_open_workspaces() -> list[dict[str, object]]:
    return list(cached_value("arduino_open_workspaces", list_arduino_open_workspaces_uncached))

def list_arduino_workspace_boards() -> dict[str, dict[str, str]]:
    cached = cached_value("arduino_workspace_boards", list_arduino_workspace_boards_uncached)
    return dict(cached)

def list_arduino_open_workspaces_uncached() -> list[dict[str, object]]:
    try:
        payload = json.loads(ARDUINO_IDE_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    workspaces = payload.get("workspaces", []) if isinstance(payload, dict) else []
    rows: list[dict[str, object]] = []
    for workspace in workspaces:
        if not isinstance(workspace, dict):
            continue
        raw_path = str(workspace.get("file") or "").strip()
        if not raw_path:
            continue
        try:
            path = str(Path(raw_path).expanduser().resolve())
        except OSError:
            continue
        rows.append({"path": path, "time": int(workspace.get("time") or 0)})
    return rows

def leveldb_workspace_path(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="ignore")
    if text.startswith("/"):
        text = text[1:]
    decoded = unquote(text)
    try:
        return str(Path(decoded).resolve())
    except OSError:
        return decoded

def list_arduino_workspace_boards_uncached() -> dict[str, dict[str, str]]:
    if not ARDUINO_IDE_LEVELDB.exists():
        return {}
    results: dict[str, dict[str, str]] = {}
    try:
        files = sorted(
            (path for path in ARDUINO_IDE_LEVELDB.iterdir() if path.suffix.lower() in {".ldb", ".log"}),
            key=lambda path: path.stat().st_mtime,
        )
    except OSError:
        return {}
    prefix = b"_file://\x00\x01theia:file:///"
    for path in files:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        start = 0
        while True:
            key_at = data.find(WORKSPACE_BOARD_KEY, start)
            if key_at < 0:
                break
            path_at = data.rfind(prefix, max(0, key_at - 1024), key_at)
            if path_at >= 0:
                raw_path = data[path_at + len(prefix):key_at]
                board_match = BOARD_JSON_RE.search(data, key_at, min(len(data), key_at + 4096))
                if board_match is not None:
                    workspace = leveldb_workspace_path(raw_path)
                    results[workspace.lower()] = {
                        "name": board_match.group(1).decode("utf-8", errors="replace"),
                        "fqbn": board_match.group(2).decode("utf-8", errors="replace"),
                    }
            start = key_at + len(WORKSPACE_BOARD_KEY)
    return results

def list_arduino_ide_processes_uncached() -> list[dict[str, object]]:
    native_processes = [
        row for row in list_arduino_process_rows_native()
        if str(row.get("name") or "").lower() == "arduino ide.exe"
    ]
    if native_processes:
        return native_processes
    processes = list_arduino_ide_processes_powershell()
    if processes:
        return processes
    return list_arduino_ide_processes_wmic()

def list_arduino_tool_processes_uncached() -> list[dict[str, object]]:
    native_processes = list_arduino_process_rows_native()
    if native_processes:
        command_processes = list(
            cached_value_ttl(
                "arduino_tool_command_processes",
                list_arduino_tool_processes_commandline_uncached,
                _COMMAND_LINE_CACHE_TTL_SECONDS,
            )
        )
        return merge_native_process_rows(native_processes, command_processes)
    return list_arduino_tool_processes_commandline_uncached()

def list_arduino_tool_processes_commandline_uncached() -> list[dict[str, object]]:
    processes = list_arduino_tool_processes_powershell()
    if processes:
        return processes
    return list_arduino_tool_processes_wmic()

def merge_native_process_rows(
    native_processes: list[dict[str, object]],
    command_processes: list[dict[str, object]],
) -> list[dict[str, object]]:
    command_by_pid = {
        int(process.get("pid") or 0): process
        for process in command_processes
        if int(process.get("pid") or 0)
    }
    merged: list[dict[str, object]] = []
    for native_process in native_processes:
        row = dict(native_process)
        command_row = command_by_pid.get(int(row.get("pid") or 0))
        if command_row:
            for key in ("command_line", "ino_paths", "fqbn", "board_name"):
                value = command_row.get(key)
                if value:
                    row[key] = value
        merged.append(row)
    return merged

def list_arduino_ide_processes_wmic() -> list[dict[str, object]]:
    command = [
        "cmd",
        "/c",
        "wmic process get Name,ProcessId,CommandLine /format:csv",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    processes: list[dict[str, object]] = []
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line or ",Arduino IDE.exe," not in line:
            continue
        match = re.match(r"^[^,]*,(.*),Arduino IDE\.exe,(\d+)$", line)
        if match is None:
            continue
        process = arduino_process_row(match.group(1), match.group(2))
        if process is not None:
            processes.append(process)
    return processes

def list_arduino_tool_processes_wmic() -> list[dict[str, object]]:
    command = [
        "cmd",
        "/c",
        "wmic process get Name,ProcessId,CommandLine /format:csv",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    processes: list[dict[str, object]] = []
    pattern = re.compile(r"^[^,]*,(.*),(Arduino IDE|arduino-language-server|arduino-cli)\.exe,(\d+)$", re.IGNORECASE)
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = pattern.match(line)
        if match is None:
            continue
        process = arduino_process_row(match.group(1), match.group(3), name=f"{match.group(2)}.exe")
        if process is not None:
            processes.append(process)
    return processes

def process_creation_time(value: object) -> int:
    match = re.search(r"Date\((\d+)", str(value or ""))
    return int(match.group(1)) if match else 0

def arduino_process_row(
    command_line: str,
    process_id: str | int,
    name: str = "Arduino IDE.exe",
    parent_process_id: str | int = 0,
    creation_date: object = "",
) -> dict[str, object] | None:
    try:
        pid = int(process_id)
    except (TypeError, ValueError):
        pid = 0
    try:
        parent_pid = int(parent_process_id)
    except (TypeError, ValueError):
        parent_pid = 0
    if not command_line:
        return None
    return {
        "name": name,
        "pid": pid,
        "parent_pid": parent_pid,
        "created_at": process_creation_time(creation_date),
        "title": "",
        "command_line": command_line,
        "ino_paths": extract_ino_paths(command_line),
        "fqbn": extract_fqbn(command_line),
        "board_name": extract_board_name(command_line),
    }

def list_arduino_ide_processes_powershell() -> list[dict[str, object]]:
    command = powershell_command(
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -ieq 'Arduino IDE.exe' } | "
        "Select-Object Name,ProcessId,ParentProcessId,CreationDate,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    rows = payload if isinstance(payload, list) else [payload]
    processes: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        command_line = str(row.get("CommandLine") or row.get("command_line") or "")
        process = arduino_process_row(
            command_line,
            row.get("ProcessId") or row.get("pid") or 0,
            parent_process_id=row.get("ParentProcessId") or row.get("parent_pid") or 0,
            creation_date=row.get("CreationDate") or row.get("created_at") or "",
        )
        if process is not None:
            process["name"] = str(row.get("Name") or row.get("name") or "Arduino IDE.exe")
            processes.append(process)
    return processes

def list_arduino_tool_processes_powershell() -> list[dict[str, object]]:
    command = powershell_command(
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -ieq 'Arduino IDE.exe' -or $_.Name -ieq 'arduino-language-server.exe' -or $_.Name -ieq 'arduino-cli.exe' } | "
        "Select-Object Name,ProcessId,ParentProcessId,CreationDate,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    rows = payload if isinstance(payload, list) else [payload]
    processes: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        command_line = str(row.get("CommandLine") or row.get("command_line") or "")
        process = arduino_process_row(
            command_line,
            row.get("ProcessId") or row.get("pid") or 0,
            name=str(row.get("Name") or row.get("name") or ""),
            parent_process_id=row.get("ParentProcessId") or row.get("parent_pid") or 0,
            creation_date=row.get("CreationDate") or row.get("created_at") or "",
        )
        if process is not None:
            processes.append(process)
    return processes

def powershell_command(script: str) -> list[str]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return ["powershell", "-NoProfile", "-EncodedCommand", encoded]

def extract_ino_paths(text: str) -> list[str]:
    paths: list[str] = []
    quoted = re.findall(r'"([^"]+?\.ino)"', text, flags=re.IGNORECASE)
    unquoted = re.findall(r"(?i)([A-Za-z]:\\[^\r\n\"']+?\.ino)\b", text)
    for raw_path in quoted + unquoted:
        path = raw_path.strip()
        if path and path not in paths:
            paths.append(path)
    return paths

def extract_fqbn(text: str) -> str:
    match = re.search(r"(?i)(?:^|\s)-fqbn\s+([^\s\"]+)", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"(?i)(?:^|\s)--fqbn\s+([^\s\"]+)", text)
    return match.group(1).strip() if match else ""

def extract_board_name(text: str) -> str:
    match = re.search(r'(?i)(?:^|\s)-board-name\s+"([^"]+)"', text)
    if match:
        return match.group(1).strip()
    match = re.search(r"(?i)(?:^|\s)-board-name\s+([^\s]+)", text)
    return match.group(1).strip() if match else ""

def sketch_name_from_arduino_title(title: str) -> str:
    if "arduino ide" not in title.lower():
        return ""
    workspace_title = re.split(
        r"\s+[|]\s+Arduino IDE\b",
        title,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" -|[]()")
    tab_match = re.match(
        r"^(?P<sketch>.+?)\s+-\s+[^\\/]+?\.(?:ino|h|hpp|c|cc|cpp|cxx|s)$",
        workspace_title,
        flags=re.IGNORECASE,
    )
    if tab_match:
        workspace_title = tab_match.group("sketch").strip()
    if workspace_title.lower().endswith(".ino"):
        workspace_title = workspace_title[:-4]
    workspace_title = workspace_title.rstrip(".")
    if not workspace_title or not re.fullmatch(r"[A-Za-z0-9 _.-]+", workspace_title):
        return ""
    return f"{workspace_title}.ino"

def extract_ino_names(title: str) -> list[str]:
    title_sketch = sketch_name_from_arduino_title(title)
    if _LIBRARY is not None:
        buffer = ctypes.create_unicode_buffer(INO_BUFFER_CHARS)
        _LIBRARY.talos_extract_ino_names(title, buffer, INO_BUFFER_CHARS)
        names = [line for line in buffer.value.splitlines() if line.strip()]
        if names:
            if title_sketch and any(
                name.lower().endswith((".cpp.ino", ".c.ino", ".h.ino", ".hpp.ino"))
                or " - " in name
                for name in names
            ):
                return [title_sketch]
            return names

    names: list[str] = []
    for match in re.findall(r"(?i)([A-Za-z0-9 _.-]+\.ino)", title):
        name = match.strip(" -|[]()")
        if name and name.lower().endswith(".ino") and name not in names:
            names.append(name)
    if names:
        return names

    return [title_sketch] if title_sketch else []
