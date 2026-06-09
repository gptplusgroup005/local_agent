from __future__ import annotations

import ctypes
import os
import re
import sys
from pathlib import Path

from talos.core import ROOT

ASSET_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
DLL_CANDIDATES = [
    ASSET_ROOT / "native" / "bin" / "talos_native.dll",
    ROOT / "native" / "bin" / "talos_native.dll",
]
TITLE_BUFFER_CHARS = 65536
INO_BUFFER_CHARS = 4096

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

def list_window_titles() -> list[str]:
    if _LIBRARY is None:
        return []
    buffer = ctypes.create_unicode_buffer(TITLE_BUFFER_CHARS)
    _LIBRARY.talos_list_window_titles(buffer, TITLE_BUFFER_CHARS)
    return [line for line in buffer.value.splitlines() if line.strip()]

def extract_ino_names(title: str) -> list[str]:
    if _LIBRARY is not None:
        buffer = ctypes.create_unicode_buffer(INO_BUFFER_CHARS)
        _LIBRARY.talos_extract_ino_names(title, buffer, INO_BUFFER_CHARS)
        return [line for line in buffer.value.splitlines() if line.strip()]

    names: list[str] = []
    for match in re.findall(r"(?i)([A-Za-z0-9 _.-]+\.ino)", title):
        name = match.strip(" -|[]()")
        if name and name.lower().endswith(".ino") and name not in names:
            names.append(name)
    return names
