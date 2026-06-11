from __future__ import annotations

import ctypes
import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from talos.core import ROOT

ASSET_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
DLL_CANDIDATES = [
    ASSET_ROOT / "native" / "bin" / "talos_native.dll",
    ROOT / "native" / "bin" / "talos_native.dll",
]
TITLE_BUFFER_CHARS = 65536
INO_BUFFER_CHARS = 4096
_CACHE_TTL_SECONDS = 1.25
_CACHE: dict[str, tuple[float, object]] = {}

def _load_library() -> ctypes.CDLL | None:
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
    return library

_LIBRARY = _load_library()

def native_available() -> bool:
    return _LIBRARY is not None

def cached_value(key: str, loader):
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[0] <= _CACHE_TTL_SECONDS:
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

def list_arduino_ide_processes_uncached() -> list[dict[str, object]]:
    processes = list_arduino_ide_processes_wmic()
    if processes:
        return processes
    return list_arduino_ide_processes_powershell()

def list_arduino_tool_processes_uncached() -> list[dict[str, object]]:
    processes = list_arduino_tool_processes_wmic()
    if processes:
        return processes
    return list_arduino_tool_processes_powershell()

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

def arduino_process_row(command_line: str, process_id: str | int, name: str = "Arduino IDE.exe") -> dict[str, object] | None:
    try:
        pid = int(process_id)
    except (TypeError, ValueError):
        pid = 0
    if not command_line:
        return None
    return {
        "name": name,
        "pid": pid,
        "title": "",
        "command_line": command_line,
        "ino_paths": extract_ino_paths(command_line),
        "fqbn": extract_fqbn(command_line),
        "board_name": extract_board_name(command_line),
    }

def list_arduino_ide_processes_powershell() -> list[dict[str, object]]:
    command = powershell_command(
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -ieq 'Arduino IDE.exe' } | "
        "Select-Object Name,ProcessId,CommandLine | ConvertTo-Json -Compress"
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
        process = arduino_process_row(command_line, row.get("ProcessId") or row.get("pid") or 0)
        if process is not None:
            process["name"] = str(row.get("Name") or row.get("name") or "Arduino IDE.exe")
            processes.append(process)
    return processes

def list_arduino_tool_processes_powershell() -> list[dict[str, object]]:
    command = powershell_command(
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -ieq 'Arduino IDE.exe' -or $_.Name -ieq 'arduino-language-server.exe' -or $_.Name -ieq 'arduino-cli.exe' } | "
        "Select-Object Name,ProcessId,CommandLine | ConvertTo-Json -Compress"
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
        )
        if process is not None:
            processes.append(process)
    return processes

def powershell_command(script: str) -> list[str]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return ["powershell", "-NoProfile", "-EncodedCommand", encoded]

def arduino_ide_running() -> bool:
    return bool(list_arduino_ide_processes())

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

def extract_ino_names(title: str) -> list[str]:
    if _LIBRARY is not None:
        buffer = ctypes.create_unicode_buffer(INO_BUFFER_CHARS)
        _LIBRARY.talos_extract_ino_names(title, buffer, INO_BUFFER_CHARS)
        names = [line for line in buffer.value.splitlines() if line.strip()]
        if names:
            return names

    names: list[str] = []
    for match in re.findall(r"(?i)([A-Za-z0-9 _.-]+\.ino)", title):
        name = match.strip(" -|[]()")
        if name and name.lower().endswith(".ino") and name not in names:
            names.append(name)
    if names:
        return names

    if "arduino ide" not in title.lower():
        return []
    sketch_title = re.split(r"\s+[-|]\s+Arduino IDE\b", title, maxsplit=1, flags=re.IGNORECASE)[0]
    sketch_title = sketch_title.strip(" -|[]()")
    if not re.fullmatch(r"[A-Za-z0-9 _.-]+", sketch_title):
        return []
    sketch_title = sketch_title.rstrip(".")
    if not sketch_title:
        return []
    return [f"{sketch_title}.ino"]
