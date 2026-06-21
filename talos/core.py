from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent

CONFIG_PATH = ROOT / "config" / "config.json"
APP_IDENTITY_PATH = ROOT / "config" / "app_identity.json"
WEBVIEW_MIN_WIDTH = 520
WEBVIEW_MIN_HEIGHT = 420

DEFAULT_APP_IDENTITY: dict[str, str] = {
    "app_name": "Talos",
    "display_name": "Talos",
    "publisher": "T-Engine",
    "version": "0.1.0",
    "channel": "Beta",
    "support": "",
    "icon_source": "talos_icon.png",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "theme": "light",
    "arduino_workspace_path": "",
    "arduino_fqbn": "",
}

def read_json_file(path: Path, fallback: Any, encoding: str = "utf-8") -> Any:
    if not path.exists():
        return fallback
    try:
        with path.open("r", encoding=encoding) as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError):
        return fallback

def write_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp_path, path)

def load_config() -> dict[str, Any]:
    data = read_json_file(CONFIG_PATH, {}, encoding="utf-8-sig")
    config = DEFAULT_CONFIG | data if isinstance(data, dict) else DEFAULT_CONFIG.copy()
    if str(config.get("theme", "light")) not in {"light", "dark", "neutral"}:
        config["theme"] = "light"
    return config

def save_config(config: dict[str, Any]) -> None:
    write_json_file(CONFIG_PATH, DEFAULT_CONFIG | config)

def load_app_identity() -> dict[str, str]:
    data = read_json_file(APP_IDENTITY_PATH, {}, encoding="utf-8-sig")
    identity = DEFAULT_APP_IDENTITY | data if isinstance(data, dict) else DEFAULT_APP_IDENTITY.copy()
    return {key: str(value).strip() for key, value in identity.items()}

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
