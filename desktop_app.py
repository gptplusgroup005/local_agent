from __future__ import annotations

import ast
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any
from urllib.parse import quote, urlparse

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
TASKS_PATH = ROOT / "tasks.json"
MEMORY_PATH = ROOT / "memory.json"
TASK_STATUSES = ("queued", "running", "done", "failed")
PROMPT_PREVIEW_LIMIT = 120
MEMORY_TURN_LIMIT = 24
QUEUE_PANE_MIN_HEIGHT = 160
DETAIL_PANE_MIN_HEIGHT = 120
QUEUE_SPLIT_INITIAL_RATIO = 0.38
QUEUE_SPLITTER_HEIGHT = 14

CYBER = {
    "bg": "#01040b",
    "bg_2": "#031226",
    "panel": "#061629",
    "panel_2": "#0a2340",
    "field": "#020a16",
    "rail": "#01030a",
    "line": "#126a9f",
    "line_soft": "#0a3556",
    "text": "#e8fbff",
    "muted": "#86c9e8",
    "cyan": "#00e5ff",
    "blue": "#1683ff",
    "deep_blue": "#0b3d91",
    "green": "#29ffc6",
    "amber": "#ff9f43",
    "warn": "#ffd166",
    "fail": "#ff5c8a",
    "glow": "#14f1ff",
    "glow_soft": "#073c5d",
    "violet": "#7a5cff",
}

LANGUAGES = {
    "auto": {"label": "Auto", "instruction": "the same language as the user's command; if unsure, use English", "time": "Current local time", "date": "Current local date", "prototype": "Prototype mode"},
    "vi": {"label": "Tiếng Việt", "instruction": "Vietnamese", "time": "Giờ địa phương", "date": "Ngày địa phương", "prototype": "Chế độ prototype"},
    "en": {"label": "English", "instruction": "English", "time": "Current local time", "date": "Current local date", "prototype": "Prototype mode"},
    "fr": {"label": "Français", "instruction": "French", "time": "Heure locale", "date": "Date locale", "prototype": "Mode prototype"},
    "ja": {"label": "日本語", "instruction": "Japanese", "time": "現地時刻", "date": "現地日付", "prototype": "プロトタイプモード"},
    "zh": {"label": "中文", "instruction": "Chinese", "time": "本地时间", "date": "本地日期", "prototype": "原型模式"},
}

DEFAULT_CONFIG: dict[str, Any] = {
    "model": "qwen3:8b",
    "ollama_url": "http://127.0.0.1:11434/api/chat",
    "host": "127.0.0.1",
    "port": 8765,
    "workspace": ".",
    "temperature": 0.3,
    "num_ctx": 4096,
    "model_enabled": False,
    "language": "vi",
    "allow_shell": False,
    "allowed_commands": [],
}


def language_code(config: dict[str, Any]) -> str:
    code = str(config.get("language", "vi"))
    return code if code in LANGUAGES else "vi"


def language_label(config: dict[str, Any]) -> str:
    return LANGUAGES[language_code(config)]["label"]


def detect_language(prompt: str) -> str:
    text = prompt.strip().lower()
    if not text:
        return "en"
    if re.search(r"[\u3040-\u30ff]", prompt):
        return "ja"
    if re.search(r"[\u4e00-\u9fff]", prompt):
        return "zh"
    vietnamese_marks = "ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"
    if any(char in text for char in vietnamese_marks):
        return "vi"
    french_marks = "àâæçéèêëîïôœùûüÿ"
    if any(char in text for char in french_marks):
        return "fr"

    tokens = set(re.findall(r"[a-z']+", text))
    vi_words = {"toi", "tôi", "ban", "bạn", "hay", "hãy", "ngon", "ngữ", "tinh", "tính", "mo", "mở", "ngay", "hôm", "gio"}
    fr_words = {"bonjour", "salut", "merci", "calcule", "calculer", "ouvre", "ouvrir", "aujourd", "heure", "date", "resultat", "résultat"}
    en_words = {"the", "what", "please", "open", "calculate", "compute", "result", "time", "date", "today", "run"}
    scores = {
        "vi": len(tokens & vi_words),
        "fr": len(tokens & fr_words),
        "en": len(tokens & en_words),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "en"


def response_language(config: dict[str, Any], prompt: str) -> str:
    code = language_code(config)
    if code == "auto":
        return detect_language(prompt)
    return code


def read_json_file(path: Path, fallback: Any, encoding: str = "utf-8") -> Any:
    if not path.exists():
        return fallback
    try:
        with path.open("r", encoding=encoding) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return fallback


def write_json_file(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())


def load_config() -> dict[str, Any]:
    data = read_json_file(CONFIG_PATH, {}, encoding="utf-8-sig")
    config = DEFAULT_CONFIG | data if isinstance(data, dict) else DEFAULT_CONFIG.copy()
    if language_code(config) != config.get("language"):
        config["language"] = "vi"
    return config


def save_config(config: dict[str, Any]) -> None:
    write_json_file(CONFIG_PATH, DEFAULT_CONFIG | config)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def preview_text(text: str, limit: int = PROMPT_PREVIEW_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."

class TaskStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        if not self.path.exists():
            self.write([])

    def read(self) -> list[dict[str, Any]]:
        with self.lock:
            return self._read_unlocked()

    def write(self, tasks: list[dict[str, Any]]) -> None:
        with self.lock:
            self._write_unlocked(tasks)

    def create(self, prompt: str) -> int:
        with self.lock:
            tasks = self._read_unlocked()
            task_id = max((task["id"] for task in tasks), default=0) + 1
            stamp = now()
            tasks.append(
                {
                    "id": task_id,
                    "prompt": prompt,
                    "status": "queued",
                    "result": "",
                    "error": "",
                    "created_at": stamp,
                    "updated_at": stamp,
                }
            )
            self._write_unlocked(tasks)
            return task_id

    def claim(self) -> dict[str, Any] | None:
        with self.lock:
            tasks = sorted(self._read_unlocked(), key=lambda item: item["id"])
            for task in tasks:
                if task["status"] == "queued":
                    task["status"] = "running"
                    task["updated_at"] = now()
                    self._write_unlocked(tasks)
                    return task
        return None

    def update(self, task_id: int, **changes: Any) -> None:
        with self.lock:
            tasks = self._read_unlocked()
            for task in tasks:
                if task["id"] == task_id:
                    task.update(changes)
                    task["updated_at"] = now()
                    break
            self._write_unlocked(tasks)

    def clear_done(self) -> None:
        with self.lock:
            tasks = [task for task in self._read_unlocked() if task["status"] != "done"]
            self._write_unlocked(tasks)

    def clear_ids(self, task_ids: set[int]) -> None:
        with self.lock:
            tasks = [task for task in self._read_unlocked() if task["id"] not in task_ids]
            self._write_unlocked(tasks)

    def _read_unlocked(self) -> list[dict[str, Any]]:
        data = read_json_file(self.path, [])
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict) and isinstance(item.get("id"), int)]

    def _write_unlocked(self, tasks: list[dict[str, Any]]) -> None:
        write_json_file(self.path, tasks)


class ConversationMemory:
    def __init__(self, path: Path, limit: int = MEMORY_TURN_LIMIT) -> None:
        self.path = path
        self.limit = limit
        self.lock = threading.Lock()
        if not self.path.exists():
            self.write([])

    def read(self) -> list[dict[str, str]]:
        with self.lock:
            items = self._read_unlocked()
            return [item for item in items if isinstance(item, dict)][-self.limit :]

    def append(self, role: str, content: str) -> None:
        content = content.strip()
        if not content:
            return
        with self.lock:
            items = self._read_unlocked()
            items.append({"role": role, "content": content, "created_at": now()})
            self._write_unlocked(items[-self.limit :])

    def append_turn(self, prompt: str, response: str) -> None:
        prompt = prompt.strip()
        response = response.strip()
        if not prompt and not response:
            return
        with self.lock:
            items = self._read_unlocked()
            stamp = now()
            if prompt:
                items.append({"role": "user", "content": prompt, "created_at": stamp})
            if response:
                items.append({"role": "assistant", "content": response, "created_at": stamp})
            self._write_unlocked(items[-self.limit :])

    def write(self, items: list[dict[str, str]]) -> None:
        with self.lock:
            self._write_unlocked(items[-self.limit :])

    def _write_unlocked(self, items: list[dict[str, str]]) -> None:
        write_json_file(self.path, items)

    def _read_unlocked(self) -> list[dict[str, str]]:
        data = read_json_file(self.path, [])
        return data if isinstance(data, list) else []


class ComputerTools:
    APP_ALIASES = {
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "calc": "calc.exe",
        "paint": "mspaint.exe",
        "explorer": "explorer.exe",
        "cmd": "cmd.exe",
        "powershell": "powershell.exe",
        "terminal": "wt.exe",
        "settings": "ms-settings:",
        "edge": "msedge.exe",
        "chrome": "chrome.exe",
        "vscode": "code",
        "code": "code",
    }

    KEY_ALIASES = {
        "enter": "{ENTER}",
        "return": "{ENTER}",
        "tab": "{TAB}",
        "escape": "{ESC}",
        "esc": "{ESC}",
        "backspace": "{BACKSPACE}",
        "delete": "{DELETE}",
        "del": "{DELETE}",
        "space": " ",
        "left": "{LEFT}",
        "right": "{RIGHT}",
        "up": "{UP}",
        "down": "{DOWN}",
        "home": "{HOME}",
        "end": "{END}",
        "pageup": "{PGUP}",
        "pagedown": "{PGDN}",
    }

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def handle(self, prompt: str) -> str | None:
        text = prompt.strip()
        lower = text.lower()
        if lower.startswith("launch "):
            target = text[7:].strip().strip('"')
            return self.open_target(target)
        if lower.startswith("open "):
            target = text[5:].strip().strip('"')
            return self.open_target(target)
        if lower.startswith("focus "):
            title = text[6:].strip().strip('"')
            return self.focus_window(title)
        if lower.startswith("type text "):
            content = text[10:].strip()
            return self.type_text(content)
        if lower.startswith("type "):
            content = text[5:].strip()
            return self.type_text(content)
        if lower.startswith("press "):
            key = text[6:].strip()
            return self.press_key(key)
        if lower.startswith("hotkey "):
            combo = text[7:].strip()
            return self.hotkey(combo)
        if lower.startswith("run "):
            command = text[4:].strip()
            return self.run_allowlisted(command)
        return None

    def open_target(self, target: str) -> str:
        if not target:
            return "No target provided."
        target = self.APP_ALIASES.get(target.lower(), target)
        subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)
        return f"Opened: {target}"

    def focus_window(self, title: str) -> str:
        if not title:
            return "No window title provided."
        script = (
            "$wshell = New-Object -ComObject WScript.Shell; "
            f"$ok = $wshell.AppActivate({self.ps_quote(title)}); "
            "if ($ok) { 'focused' } else { 'not found' }"
        )
        completed = self.run_powershell(script)
        if completed.returncode == 0 and "focused" in completed.stdout.lower():
            return f"Focused window matching: {title}"
        return f"No active window matched: {title}"

    def type_text(self, content: str) -> str:
        content = content.strip()
        if not content:
            return "No text provided."
        script = (
            f"Set-Clipboard -Value {self.ps_quote(content)}; "
            "$wshell = New-Object -ComObject WScript.Shell; "
            "Start-Sleep -Milliseconds 80; "
            "$wshell.SendKeys('^v')"
        )
        completed = self.run_powershell(script)
        if completed.returncode != 0:
            return completed.stderr.strip() or "Typing failed."
        return "Typed text into the active window."

    def press_key(self, key: str) -> str:
        send_key = self.KEY_ALIASES.get(key.strip().lower())
        if send_key is None:
            return f"Unsupported key: {key}"
        completed = self.send_keys(send_key)
        if completed.returncode != 0:
            return completed.stderr.strip() or "Key press failed."
        return f"Pressed: {key}"

    def hotkey(self, combo: str) -> str:
        send_key = self.hotkey_to_sendkeys(combo)
        if send_key is None:
            return f"Unsupported hotkey: {combo}"
        completed = self.send_keys(send_key)
        if completed.returncode != 0:
            return completed.stderr.strip() or "Hotkey failed."
        return f"Pressed hotkey: {combo}"

    def hotkey_to_sendkeys(self, combo: str) -> str | None:
        parts = [part.strip().lower() for part in re.split(r"[+ ]+", combo) if part.strip()]
        if not parts:
            return None
        modifiers = ""
        key = parts[-1]
        for part in parts[:-1]:
            if part in {"ctrl", "control"}:
                modifiers += "^"
            elif part == "shift":
                modifiers += "+"
            elif part in {"alt", "option"}:
                modifiers += "%"
            else:
                return None
        send_key = self.KEY_ALIASES.get(key, key)
        if len(send_key) == 1:
            return modifiers + send_key
        return modifiers + send_key

    def send_keys(self, keys: str) -> subprocess.CompletedProcess[str]:
        script = "$wshell = New-Object -ComObject WScript.Shell; " f"$wshell.SendKeys({self.ps_quote(keys)})"
        return self.run_powershell(script)

    def run_powershell(self, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=ROOT,
        )

    def ps_quote(self, value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def run_allowlisted(self, command: str) -> str:
        if not self.config.get("allow_shell", False):
            return "Shell is locked. Enable allow_shell in Settings first."
        allowed = self.config.get("allowed_commands", [])
        if command not in allowed:
            return f"Command is not allowlisted: {command}"
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            shell=True,
            timeout=60,
            cwd=ROOT,
        )
        output = completed.stdout.strip() or completed.stderr.strip()
        return output or f"Command completed with exit code {completed.returncode}."

class LocalTaskEngine:
    ALLOWED_BINOPS = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
        ast.FloorDiv: lambda a, b: a // b,
        ast.Mod: lambda a, b: a % b,
        ast.Pow: lambda a, b: a**b,
    }
    ALLOWED_UNARY = {
        ast.UAdd: lambda value: value,
        ast.USub: lambda value: -value,
    }
    ALLOWED_FUNCS = {
        "abs": abs,
        "round": round,
        "sqrt": math.sqrt,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "log": math.log,
        "log10": math.log10,
    }
    ALLOWED_NAMES = {
        "pi": math.pi,
        "e": math.e,
    }

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.tools = ComputerTools(config)

    def handle(self, prompt: str) -> str | None:
        text = prompt.strip()
        if not text:
            return "No task content."

        tool_result = self.tools.handle(text)
        if tool_result is not None:
            return tool_result

        time_result = self.handle_time(text)
        if time_result is not None:
            return time_result

        weather_result = self.handle_weather(text)
        if weather_result is not None:
            return weather_result

        sports_result = self.handle_sports(text)
        if sports_result is not None:
            return sports_result

        math_result = self.handle_math(text)
        if math_result is not None:
            return math_result

        return None

    def handle_time(self, prompt: str) -> str | None:
        lower = prompt.lower()
        lang = LANGUAGES[response_language(self.config, prompt)]
        if lower in {"time", "what time is it", "current time", "now", "gio hien tai", "mấy giờ rồi"}:
            return f"{lang['time']}: {datetime.now().strftime('%H:%M:%S')}"
        if lower in {"date", "today", "current date", "ngay hom nay", "hôm nay"}:
            return f"{lang['date']}: {datetime.now().strftime('%Y-%m-%d')}"
        return None

    def handle_weather(self, prompt: str) -> str | None:
        location = self.extract_weather_location(prompt)
        if location is None:
            return None
        lang_code = response_language(self.config, prompt)
        return self.fetch_weather(location, lang_code)

    def extract_weather_location(self, prompt: str) -> str | None:
        text = prompt.strip()
        lower = text.lower()
        weather_terms = ("weather", "forecast", "temperature", "thoi tiet", "thời tiết")
        if not any(term in lower for term in weather_terms):
            return None

        patterns = [
            r"today'?s\s+weather\s+in\s+(.+)",
            r"weather\s+today\s+in\s+(.+)",
            r"weather\s+in\s+(.+)",
            r"forecast\s+for\s+(.+)",
            r"temperature\s+in\s+(.+)",
            r"thời tiết(?:\s+hôm nay)?\s+(?:ở|tại)\s+(.+)",
            r"thoi tiet(?:\s+hom nay)?\s+(?:o|tai)\s+(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip(" .?!")
        if lower in {"weather", "today's weather", "weather today", "forecast"}:
            return "current location"
        return None

    def fetch_weather(self, location: str, lang_code: str) -> str:
        query = "Vietnam" if location.lower() in {"vietnam", "viet nam", "việt nam"} else location
        url = f"https://wttr.in/{quote(query)}?format=j1"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Talos/1.0"})
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if lang_code == "vi":
                return (
                    f"Không lấy được dữ liệu thời tiết trực tiếp cho {location}.\n\n"
                    "Talos có nhận diện được đây là yêu cầu thời tiết, nhưng provider realtime hiện không phản hồi.\n"
                    f"Chi tiết: {exc}"
                )
            return (
                f"I could not fetch live weather data for {location}.\n\n"
                "Talos recognized this as a weather request, but the realtime weather provider is not responding.\n"
                f"Details: {exc}"
            )

        current = (payload.get("current_condition") or [{}])[0]
        today = (payload.get("weather") or [{}])[0]
        area = (payload.get("nearest_area") or [{}])[0]
        area_name = self.weather_value(area.get("areaName")) or location
        country = self.weather_value(area.get("country"))
        description = self.weather_value(current.get("weatherDesc")) or "unknown"
        temp = current.get("temp_C", "?")
        feels = current.get("FeelsLikeC", "?")
        humidity = current.get("humidity", "?")
        wind = current.get("windspeedKmph", "?")
        precip = current.get("precipMM", "?")
        min_temp = today.get("mintempC", "?")
        max_temp = today.get("maxtempC", "?")
        place = f"{area_name}, {country}" if country else area_name

        if lang_code == "vi":
            return (
                f"Thời tiết hiện tại ở {place}:\n"
                f"- Trạng thái: {description}\n"
                f"- Nhiệt độ: {temp}°C, cảm giác như {feels}°C\n"
                f"- Hôm nay: {min_temp}°C - {max_temp}°C\n"
                f"- Độ ẩm: {humidity}%\n"
                f"- Gió: {wind} km/h\n"
                f"- Mưa ghi nhận: {precip} mm\n\n"
                "Nguồn realtime: wttr.in"
            )
        return (
            f"Current weather in {place}:\n"
            f"- Condition: {description}\n"
            f"- Temperature: {temp}°C, feels like {feels}°C\n"
            f"- Today: {min_temp}°C - {max_temp}°C\n"
            f"- Humidity: {humidity}%\n"
            f"- Wind: {wind} km/h\n"
            f"- Recorded precipitation: {precip} mm\n\n"
            "Realtime source: wttr.in"
        )

    def weather_value(self, values: Any) -> str:
        if isinstance(values, list) and values:
            item = values[0]
            if isinstance(item, dict):
                return str(item.get("value", "")).strip()
            return str(item).strip()
        return ""

    NBA_TEAMS = {
        "timberwolves": ("MIN", "Minnesota Timberwolves"),
        "wolves": ("MIN", "Minnesota Timberwolves"),
        "minnesota": ("MIN", "Minnesota Timberwolves"),
        "spurs": ("SA", "San Antonio Spurs"),
        "san antonio": ("SA", "San Antonio Spurs"),
        "lakers": ("LAL", "Los Angeles Lakers"),
        "warriors": ("GS", "Golden State Warriors"),
        "celtics": ("BOS", "Boston Celtics"),
        "knicks": ("NY", "New York Knicks"),
        "nuggets": ("DEN", "Denver Nuggets"),
        "thunder": ("OKC", "Oklahoma City Thunder"),
        "mavericks": ("DAL", "Dallas Mavericks"),
        "mavs": ("DAL", "Dallas Mavericks"),
        "heat": ("MIA", "Miami Heat"),
        "bucks": ("MIL", "Milwaukee Bucks"),
        "suns": ("PHX", "Phoenix Suns"),
        "clippers": ("LAC", "LA Clippers"),
        "sixers": ("PHI", "Philadelphia 76ers"),
        "76ers": ("PHI", "Philadelphia 76ers"),
        "bulls": ("CHI", "Chicago Bulls"),
        "nets": ("BKN", "Brooklyn Nets"),
        "rockets": ("HOU", "Houston Rockets"),
        "kings": ("SAC", "Sacramento Kings"),
        "grizzlies": ("MEM", "Memphis Grizzlies"),
        "pelicans": ("NO", "New Orleans Pelicans"),
        "cavaliers": ("CLE", "Cleveland Cavaliers"),
        "cavs": ("CLE", "Cleveland Cavaliers"),
        "magic": ("ORL", "Orlando Magic"),
        "pacers": ("IND", "Indiana Pacers"),
        "hawks": ("ATL", "Atlanta Hawks"),
        "raptors": ("TOR", "Toronto Raptors"),
        "hornets": ("CHA", "Charlotte Hornets"),
        "pistons": ("DET", "Detroit Pistons"),
        "jazz": ("UTAH", "Utah Jazz"),
        "blazers": ("POR", "Portland Trail Blazers"),
        "trail blazers": ("POR", "Portland Trail Blazers"),
        "wizards": ("WSH", "Washington Wizards"),
    }

    def handle_sports(self, prompt: str) -> str | None:
        lower = prompt.lower()
        sports_terms = ("nba", "score", "scores", "tỉ số", "ti so", "kết quả", "ket qua", "trận", "tran")
        if not any(term in lower for term in sports_terms):
            return None
        teams = self.extract_nba_teams(lower)
        if len(teams) < 2:
            return None
        return self.fetch_nba_matchup_score(teams[0], teams[1], response_language(self.config, prompt))

    def extract_nba_teams(self, lower_prompt: str) -> list[tuple[str, str]]:
        found: list[tuple[int, str, str]] = []
        for alias, team in self.NBA_TEAMS.items():
            match = re.search(rf"\b{re.escape(alias)}\b", lower_prompt)
            if match:
                found.append((match.start(), team[0], team[1]))
        unique: list[tuple[str, str]] = []
        seen: set[str] = set()
        for _pos, abbrev, name in sorted(found):
            if abbrev not in seen:
                unique.append((abbrev, name))
                seen.add(abbrev)
        return unique[:2]

    def fetch_nba_matchup_score(self, team_a: tuple[str, str], team_b: tuple[str, str], lang_code: str) -> str:
        try:
            event = self.find_nba_matchup(team_a[0], team_b[0])
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if lang_code == "vi":
                return (
                    f"Không lấy được dữ liệu tỉ số trực tiếp cho {team_a[1]} vs {team_b[1]}.\n\n"
                    "Talos đã nhận diện đây là yêu cầu tỉ số NBA, nhưng nguồn dữ liệu thể thao realtime hiện không phản hồi.\n"
                    f"Chi tiết: {exc}"
                )
            return (
                f"I could not fetch live score data for {team_a[1]} vs {team_b[1]}.\n\n"
                "Talos recognized this as an NBA score request, but the realtime sports provider is not responding.\n"
                f"Details: {exc}"
            )
        if event is None:
            if lang_code == "vi":
                return f"Không tìm thấy trận gần đây giữa {team_a[1]} và {team_b[1]} trong dữ liệu ESPN mà Talos truy cập được."
            return f"I could not find a recent game between {team_a[1]} and {team_b[1]} in the ESPN data Talos can access."
        return self.format_nba_event(event, team_a, team_b, lang_code)

    def find_nba_matchup(self, abbrev_a: str, abbrev_b: str) -> dict[str, Any] | None:
        for event in self.fetch_nba_team_schedule(abbrev_a):
            competitors = (((event.get("competitions") or [{}])[0]).get("competitors") or [])
            abbrevs = {str(item.get("team", {}).get("abbreviation", "")).upper() for item in competitors}
            normalized = {self.normalize_nba_abbrev(item) for item in abbrevs}
            if self.normalize_nba_abbrev(abbrev_a) in normalized and self.normalize_nba_abbrev(abbrev_b) in normalized:
                return event
        return None

    def fetch_nba_team_schedule(self, abbrev: str) -> list[dict[str, Any]]:
        team_slug = self.espn_team_slug(abbrev)
        urls = [
            f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_slug}/schedule?limit=200",
            f"https://site.web.api.espn.com/apis/v2/sports/basketball/nba/teams/{team_slug}/schedule?limit=200",
        ]
        last_error: Exception | None = None
        for url in urls:
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "Talos/1.0"})
                with urllib.request.urlopen(request, timeout=10) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                events = payload.get("events") or payload.get("team", {}).get("events") or []
                if isinstance(events, list):
                    return sorted(
                        [event for event in events if isinstance(event, dict)],
                        key=lambda item: item.get("date", ""),
                        reverse=True,
                    )
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        return []

    def espn_team_slug(self, abbrev: str) -> str:
        mapping = {
            "MIN": "min",
            "SA": "sa",
            "GS": "gs",
            "NO": "no",
            "NY": "ny",
            "WSH": "wsh",
            "UTAH": "utah",
        }
        return mapping.get(abbrev, abbrev.lower())

    def normalize_nba_abbrev(self, abbrev: str) -> str:
        return {"SAS": "SA", "GSW": "GS", "NOP": "NO", "NYK": "NY", "UTA": "UTAH", "WAS": "WSH"}.get(abbrev.upper(), abbrev.upper())

    def format_nba_event(self, event: dict[str, Any], team_a: tuple[str, str], team_b: tuple[str, str], lang_code: str) -> str:
        competition = (event.get("competitions") or [{}])[0]
        competitors = competition.get("competitors") or []
        lines = []
        for item in competitors:
            team = item.get("team", {})
            name = team.get("displayName") or team.get("shortDisplayName") or team.get("name") or "Unknown"
            score = item.get("score", "?")
            home_away = item.get("homeAway", "")
            lines.append((home_away, name, score))
        status = (competition.get("status") or event.get("status") or {}).get("type", {})
        status_text = status.get("description") or status.get("shortDetail") or "Unknown status"
        event_date = event.get("date", "")
        score_line = " - ".join(f"{name} {score}" for _home_away, name, score in lines)
        if lang_code == "vi":
            return (
                f"Tỉ số NBA gần nhất Talos tìm được cho {team_a[1]} vs {team_b[1]}:\n"
                f"- {score_line}\n"
                f"- Trạng thái: {status_text}\n"
                f"- Thời điểm: {event_date}\n\n"
                "Nguồn realtime: ESPN API"
            )
        return (
            f"Latest NBA score Talos found for {team_a[1]} vs {team_b[1]}:\n"
            f"- {score_line}\n"
            f"- Status: {status_text}\n"
            f"- Date: {event_date}\n\n"
            "Realtime source: ESPN API"
        )

    def handle_math(self, prompt: str) -> str | None:
        expression = self.extract_expression(prompt)
        if expression is None:
            return None
        value = self.safe_eval(expression)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return f"{expression} = {value}"

    def extract_expression(self, prompt: str) -> str | None:
        lower = prompt.lower().strip()
        prefixes = [
            "print the result of",
            "calculate",
            "compute",
            "solve",
            "what is",
            "result of",
            "tinh",
            "tính",
        ]
        for prefix in prefixes:
            if lower.startswith(prefix):
                expression = prompt[len(prefix) :].strip(" :?=")
                return expression if self.looks_like_math(expression) else None
        return prompt if self.looks_like_math(prompt) else None

    def looks_like_math(self, expression: str) -> bool:
        if not expression or len(expression) > 160:
            return False
        if not re.search(r"\d", expression):
            return False
        return re.fullmatch(r"[0-9a-zA-Z_+\-*/%.(),\s]+", expression) is not None

    def safe_eval(self, expression: str) -> int | float:
        tree = ast.parse(expression, mode="eval")
        return self.eval_node(tree.body)

    def eval_node(self, node: ast.AST) -> int | float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in self.ALLOWED_BINOPS:
                raise ValueError("Unsupported operator.")
            return self.ALLOWED_BINOPS[op_type](self.eval_node(node.left), self.eval_node(node.right))
        if isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in self.ALLOWED_UNARY:
                raise ValueError("Unsupported unary operator.")
            return self.ALLOWED_UNARY[op_type](self.eval_node(node.operand))
        if isinstance(node, ast.Name):
            if node.id not in self.ALLOWED_NAMES:
                raise ValueError(f"Unknown name: {node.id}")
            return self.ALLOWED_NAMES[node.id]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in self.ALLOWED_FUNCS:
                raise ValueError(f"Unsupported function: {node.func.id}")
            args = [self.eval_node(arg) for arg in node.args]
            return self.ALLOWED_FUNCS[node.func.id](*args)
        raise ValueError("Expression is not supported.")

def memory_messages(memory: list[dict[str, str]]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in memory[-MEMORY_TURN_LIMIT:]:
        role = item.get("role", "")
        content = item.get("content", "")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    return messages


def process_prompt(prompt: str, config: dict[str, Any], memory: ConversationMemory | None = None) -> str:
    local_engine = LocalTaskEngine(config)
    local_result = local_engine.handle(prompt)
    result = local_result if local_result is not None else call_model(prompt, config, memory.read() if memory else [])
    if memory is not None:
        memory.append_turn(prompt, result)
    return result


def call_model(prompt: str, config: dict[str, Any], memory: list[dict[str, str]] | None = None) -> str:
    if not config.get("model_enabled", False):
        lang = LANGUAGES[response_language(config, prompt)]
        return (
            f"{lang['prototype']}\n\n"
            "The desktop app worker received this task. Model calls are disabled.\n\n"
            f"Task:\n{prompt}"
        )
    lang = LANGUAGES[response_language(config, prompt)]
    body = {
        "model": config["model"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Talos, a local desktop assistant that helps the user get work done. "
                    f"Answer in {lang['instruction']}. "
                    "Be concise, practical, and action-oriented. "
                    "Do not tell the user to search Google, open a weather site, or use another app. "
                    "If live data is required but unavailable, state the limitation clearly and say what integration is missing. "
                    "Use the conversation history to resolve follow-up requests, pronouns, corrections, and references to previous answers. "
                    "When a task requires an action you cannot perform yet, say exactly what capability is missing and what you can do instead. "
                    "Available local computer actions include opening apps/files/URLs, focusing windows by title, typing text, pressing keys, and hotkeys when the user explicitly requests them. "
                    "Think internally, but never reveal hidden reasoning, chain-of-thought, or <think> tags."
                ),
            },
            *memory_messages(memory or []),
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {
            "temperature": config.get("temperature", 0.4),
            "num_ctx": config.get("num_ctx", 4096),
        },
    }
    if str(config.get("model", "")).startswith("qwen3:"):
        body["think"] = False
    request = urllib.request.Request(
        config["ollama_url"],
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("message", {}).get("content", "").strip()
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace").strip()
        hint = ""
        if "CUDA error" in details:
            hint = (
                "\n\nHint: Ollama reached the model runner, but GPU initialization failed. "
                "Restart Ollama, update/reinstall the NVIDIA driver, or run a smaller/CPU-compatible model."
            )
        raise RuntimeError(
            "Ollama returned an error while processing the request.\n\n"
            f"Configured endpoint: {config.get('ollama_url')}\n"
            f"HTTP status: {exc.code} {exc.reason}\n"
            f"Details: {details or exc}"
            f"{hint}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Ollama backend is not reachable.\n\n"
            "Install Ollama, then run:\n"
            f"ollama pull {config.get('model', 'qwen2.5:7b-instruct-q3_K_L')}\n\n"
            f"Configured endpoint: {config.get('ollama_url')}\n"
            f"Details: {exc}"
        ) from exc

def ollama_base_url(config: dict[str, Any]) -> str:
    parsed = urlparse(config.get("ollama_url", "http://127.0.0.1:11434/api/chat"))
    return f"{parsed.scheme}://{parsed.netloc}"

def check_ollama(config: dict[str, Any], timeout: float = 2.0) -> tuple[bool, str]:
    base_url = ollama_base_url(config)
    tags_url = f"{base_url}/api/tags"
    try:
        with urllib.request.urlopen(tags_url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        return False, (
            "Ollama is not reachable.\n"
            "Install/start Ollama, then pull the configured model.\n"
            f"Endpoint: {tags_url}\n"
            f"Details: {exc}"
        )

    model = config.get("model", "")
    models = [item.get("name", "") for item in payload.get("models", [])]
    if any(name == model or name.startswith(f"{model}:") for name in models):
        return True, f"Ollama is ready. Model found: {model}"
    available = ", ".join(models[:8]) if models else "none"
    return False, (
        f"Ollama is running, but the configured model is missing: {model}\n"
        f"Run: ollama pull {model}\n"
        f"Available models: {available}"
    )


class HoloPanel(tk.Canvas):
    def __init__(
        self,
        parent: tk.Widget,
        *,
        bg_color: str,
        line_color: str,
        glow_color: str,
        min_height: int = 140,
        min_width: int = 240,
        inset: int = 10,
    ) -> None:
        super().__init__(
            parent,
            bg=CYBER["bg"],
            bd=0,
            highlightthickness=0,
            width=min_width,
            height=min_height,
        )
        self.bg_color = bg_color
        self.line_color = line_color
        self.glow_color = glow_color
        self.inset = inset
        self.inner = tk.Frame(self, bg=bg_color)
        self.inner_window = self.create_window(inset, inset, anchor="nw", window=self.inner)
        self.bind("<Configure>", self.draw)

    def draw(self, _event: tk.Event | None = None) -> None:
        self.delete("surface")
        width = max(self.winfo_width(), 2)
        height = max(self.winfo_height(), 2)
        inset = self.inset
        x1, y1 = inset, inset
        x2, y2 = width - inset, height - inset
        cut = 18

        self.coords(self.inner_window, inset + 3, inset + 3)
        self.itemconfigure(self.inner_window, width=max(1, width - (inset + 3) * 2), height=max(1, height - (inset + 3) * 2))

        self.create_rectangle(0, 0, width, height, fill=CYBER["bg"], outline="", tags="surface")
        self.create_polygon(
            x1 + 8,
            y1 + 12,
            x2 + 6,
            y1 + 12,
            x2 + 6,
            y2 + 7,
            x1 + 8,
            y2 + 7,
            fill="#00040b",
            outline="",
            tags="surface",
        )
        for expand, color in ((8, CYBER["glow_soft"]), (4, CYBER["line_soft"])):
            self.create_polygon(
                x1 - expand + cut,
                y1 - expand,
                x2 + expand,
                y1 - expand,
                x2 + expand,
                y2 + expand - cut,
                x2 + expand - cut,
                y2 + expand,
                x1 - expand,
                y2 + expand,
                x1 - expand,
                y1 - expand + cut,
                outline=color,
                fill="",
                width=1,
                tags="surface",
            )
        self.create_polygon(
            x1 + cut,
            y1,
            x2,
            y1,
            x2,
            y2 - cut,
            x2 - cut,
            y2,
            x1,
            y2,
            x1,
            y1 + cut,
            fill=self.bg_color,
            outline=self.line_color,
            width=1,
            tags="surface",
        )
        self.create_line(x1 + cut + 2, y1 + 2, x2 - 6, y1 + 2, fill=self.glow_color, width=2, tags="surface")
        self.create_line(x1 + 2, y1 + cut + 2, x1 + 2, y2 - 6, fill=CYBER["line_soft"], width=1, tags="surface")
        self.create_line(x1 + 20, y2 - 3, x2 - cut - 2, y2 - 3, fill="#02101f", width=3, tags="surface")
        self.create_line(x2 - 2, y1 + 20, x2 - 2, y2 - cut - 2, fill="#02101f", width=3, tags="surface")
        self.create_line(x1 + 10, y1 + 16, x1 + cut, y1 + 2, fill=CYBER["text"], width=1, tags="surface")
        self.create_line(x2 - cut, y2 - 2, x2 - 2, y2 - cut, fill=self.glow_color, width=1, tags="surface")
        self.tag_lower("surface", self.inner_window)


class AutoScrollbar(ttk.Scrollbar):
    def __init__(self, parent: tk.Widget, **options: Any) -> None:
        super().__init__(parent, **options)
        self.visible = True

    def set(self, first: str, last: str) -> None:
        if float(first) <= 0.0 and float(last) >= 1.0:
            if self.visible:
                self.grid_remove()
                self.visible = False
        elif not self.visible:
            self.grid()
            self.visible = True
        super().set(first, last)


class LocalAgentDesktop(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Talos")
        self.overrideredirect(True)
        self.geometry("1120x720")
        self.minsize(920, 620)
        self.configure(bg=CYBER["bg"])
        self.is_maximized = False
        self.is_minimized = False
        self.normal_geometry = "1120x720"
        self.drag_start_x = 0
        self.drag_start_y = 0

        self.config_data = load_config()
        self.store = TaskStore(TASKS_PATH)
        self.memory = ConversationMemory(MEMORY_PATH)
        self.events: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.selected_task_ids: set[int] = set()
        self.all_tasks_selected = False

        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.configure_style()
        self.build_ui()
        self.refresh_all()

        self.worker = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker.start()
        self.after(1000, self.tick)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Map>", self.restore_window_chrome)

    def configure_style(self) -> None:
        self.style.configure("TFrame", background=CYBER["bg"])
        self.style.configure("Rail.TFrame", background=CYBER["rail"])
        self.style.configure("Panel.TFrame", background=CYBER["panel"], relief="flat")
        self.style.configure("Card.TFrame", background=CYBER["panel_2"], relief="flat")
        self.style.configure("TLabel", background=CYBER["bg"], foreground=CYBER["text"], font=("Segoe UI", 10))
        self.style.configure("Muted.TLabel", background=CYBER["panel"], foreground=CYBER["muted"], font=("Cascadia Mono", 9))
        self.style.configure("Panel.TLabel", background=CYBER["panel"], foreground=CYBER["text"], font=("Cascadia Mono", 10, "bold"))
        self.style.configure("Hero.TLabel", background=CYBER["bg"], foreground=CYBER["cyan"], font=("Cascadia Mono", 23, "bold"))
        self.style.configure(
            "TButton",
            padding=(12, 8),
            background=CYBER["panel_2"],
            foreground=CYBER["text"],
            bordercolor=CYBER["line"],
            lightcolor=CYBER["line_soft"],
            darkcolor=CYBER["rail"],
            focuscolor=CYBER["cyan"],
            relief="flat",
            borderwidth=1,
            font=("Cascadia Mono", 9),
        )
        self.style.configure(
            "Accent.TButton",
            padding=(14, 9),
            background=CYBER["cyan"],
            foreground=CYBER["bg"],
            bordercolor=CYBER["glow"],
            lightcolor=CYBER["text"],
            darkcolor=CYBER["deep_blue"],
            focuscolor=CYBER["glow"],
            relief="flat",
            borderwidth=1,
            font=("Cascadia Mono", 9, "bold"),
        )
        self.style.map(
            "TButton",
            background=[("pressed", CYBER["glow_soft"]), ("active", CYBER["deep_blue"])],
            bordercolor=[("active", CYBER["glow"])],
            foreground=[("active", CYBER["text"])],
        )
        self.style.map(
            "Accent.TButton",
            background=[("pressed", CYBER["green"]), ("active", CYBER["blue"])],
            foreground=[("active", CYBER["text"])],
        )
        self.style.configure("TNotebook", background=CYBER["bg"], bordercolor=CYBER["line"], tabmargins=(0, 0, 0, 0))
        self.style.configure(
            "TNotebook.Tab",
            padding=(18, 10),
            background=CYBER["field"],
            foreground=CYBER["muted"],
            bordercolor=CYBER["line_soft"],
            font=("Cascadia Mono", 9, "bold"),
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", CYBER["panel_2"]), ("active", CYBER["glow_soft"])],
            foreground=[("selected", CYBER["cyan"]), ("active", CYBER["text"])],
            bordercolor=[("selected", CYBER["glow"])],
        )
        self.style.configure(
            "Treeview",
            rowheight=31,
            background=CYBER["field"],
            fieldbackground=CYBER["field"],
            foreground=CYBER["text"],
            bordercolor=CYBER["line"],
            lightcolor=CYBER["line_soft"],
            darkcolor=CYBER["rail"],
            borderwidth=0,
            font=("Cascadia Mono", 9),
        )
        self.style.configure(
            "Treeview.Heading",
            padding=(8, 9),
            background=CYBER["panel_2"],
            foreground=CYBER["cyan"],
            bordercolor=CYBER["line"],
            font=("Cascadia Mono", 9, "bold"),
        )
        self.style.map("Treeview", background=[("selected", CYBER["deep_blue"])], foreground=[("selected", CYBER["text"])])
        self.style.configure(
            "TEntry",
            fieldbackground=CYBER["field"],
            background=CYBER["field"],
            foreground=CYBER["text"],
            insertcolor=CYBER["cyan"],
            bordercolor=CYBER["line_soft"],
            lightcolor=CYBER["line_soft"],
            darkcolor=CYBER["rail"],
            padding=8,
        )
        self.style.map("TEntry", bordercolor=[("focus", CYBER["glow"])])
        self.style.configure(
            "TCombobox",
            fieldbackground=CYBER["field"],
            background=CYBER["panel_2"],
            foreground=CYBER["text"],
            arrowcolor=CYBER["cyan"],
            bordercolor=CYBER["line_soft"],
            padding=6,
        )
        self.style.map("TCombobox", fieldbackground=[("readonly", CYBER["field"])], bordercolor=[("focus", CYBER["glow"])])
        self.style.configure(
            "TCheckbutton",
            background=CYBER["panel"],
            foreground=CYBER["text"],
            focuscolor=CYBER["glow"],
            font=("Cascadia Mono", 9),
        )
        self.style.map("TCheckbutton", foreground=[("active", CYBER["cyan"])], background=[("active", CYBER["panel"])])

    def build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        outer = tk.Frame(self, bg=CYBER["glow"], padx=1, pady=1)
        outer.grid(row=0, column=0, rowspan=2, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        titlebar = tk.Frame(outer, bg=CYBER["rail"], height=42)
        titlebar.grid(row=0, column=0, sticky="ew")
        titlebar.grid_propagate(False)
        titlebar.columnconfigure(1, weight=1)
        titlebar.bind("<ButtonPress-1>", self.start_window_drag)
        titlebar.bind("<B1-Motion>", self.drag_window)
        titlebar.bind("<Double-Button-1>", lambda _event: self.toggle_maximize())

        tk.Label(
            titlebar,
            text="LA",
            bg=CYBER["blue"],
            fg=CYBER["text"],
            font=("Cascadia Mono", 10, "bold"),
            width=4,
        ).grid(row=0, column=0, sticky="ns", padx=(10, 8), pady=7)
        title_label = tk.Label(
            titlebar,
            text="TALOS :: ONLINE",
            bg=CYBER["rail"],
            fg=CYBER["cyan"],
            font=("Cascadia Mono", 10, "bold"),
        )
        title_label.grid(row=0, column=1, sticky="w")
        title_label.bind("<ButtonPress-1>", self.start_window_drag)
        title_label.bind("<B1-Motion>", self.drag_window)
        title_label.bind("<Double-Button-1>", lambda _event: self.toggle_maximize())

        window_buttons = tk.Frame(titlebar, bg=CYBER["rail"])
        window_buttons.grid(row=0, column=2, sticky="e")
        self.make_window_button(window_buttons, "-", self.minimize_window).pack(side="left")
        self.make_window_button(window_buttons, "[]", self.toggle_maximize).pack(side="left")
        self.make_window_button(window_buttons, "X", self.on_close, danger=True).pack(side="left")

        app_frame = ttk.Frame(outer, style="TFrame")
        app_frame.grid(row=1, column=0, sticky="nsew")
        app_frame.columnconfigure(1, weight=1)
        app_frame.rowconfigure(0, weight=1)

        rail = ttk.Frame(app_frame, width=92, style="Rail.TFrame")
        rail.grid(row=0, column=0, sticky="ns")
        rail.grid_propagate(False)

        rail_glow = tk.Canvas(rail, width=54, height=54, bg=CYBER["rail"], highlightthickness=0)
        rail_glow.pack(pady=(18, 18))
        rail_glow.create_oval(5, 5, 49, 49, outline=CYBER["glow_soft"], width=4)
        rail_glow.create_oval(10, 10, 44, 44, outline=CYBER["cyan"], width=2)
        rail_glow.create_text(27, 27, text="LA", fill=CYBER["text"], font=("Cascadia Mono", 14, "bold"))
        for label, command in [
            ("Dash", self.show_dashboard),
            ("Queue", self.show_queue),
            ("Logs", self.show_logs),
            ("Set", self.show_settings),
        ]:
            ttk.Button(rail, text=label, command=command).pack(fill="x", padx=10, pady=5)

        self.content = ttk.Frame(app_frame, padding=18, style="TFrame")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(1, weight=1)

        header = ttk.Frame(self.content, style="TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Talos", style="Hero.TLabel").grid(row=0, column=0, sticky="w")
        self.mode_label = ttk.Label(header, text="", foreground=CYBER["muted"])
        self.mode_label.grid(row=1, column=0, sticky="w")
        self.header_reactor = tk.Canvas(header, width=118, height=54, bg=CYBER["bg"], highlightthickness=0)
        self.header_reactor.grid(row=0, column=1, rowspan=2, padx=(10, 12), sticky="e")
        self.header_reactor.bind("<Configure>", self.draw_header_reactor)
        ttk.Button(header, text="Refresh", command=self.refresh_all).grid(row=0, column=2, rowspan=2, padx=(0, 0))

        notebook_outer, notebook_inner = self.make_glow_frame(self.content, bg=CYBER["panel"], glow=CYBER["glow_soft"])
        notebook_outer.grid(row=1, column=0, sticky="nsew")
        notebook_inner.columnconfigure(0, weight=1)
        notebook_inner.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(notebook_inner)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        self.dashboard_tab = ttk.Frame(self.notebook, padding=14, style="Panel.TFrame")
        self.queue_tab = ttk.Frame(self.notebook, padding=14, style="Panel.TFrame")
        self.logs_tab = ttk.Frame(self.notebook, padding=14, style="Panel.TFrame")
        self.settings_tab = ttk.Frame(self.notebook, padding=14, style="Panel.TFrame")
        self.notebook.add(self.dashboard_tab, text="Dashboard")
        self.notebook.add(self.queue_tab, text="Queue")
        self.notebook.add(self.logs_tab, text="Logs")
        self.notebook.add(self.settings_tab, text="Settings")

        self.build_dashboard()
        self.build_queue()
        self.build_logs()
        self.build_settings()

    def make_glow_frame(
        self,
        parent: tk.Widget,
        *,
        bg: str | None = None,
        glow: str | None = None,
        line: str | None = None,
        depth: int = 2,
    ) -> tuple[tk.Widget, tk.Frame]:
        glow_color = glow or CYBER["glow"]
        line_color = line or CYBER["line"]
        body_color = bg or CYBER["field"]
        min_height = 150 + depth * 12
        panel = HoloPanel(
            parent,
            bg_color=body_color,
            line_color=line_color,
            glow_color=glow_color,
            min_height=min_height,
            inset=8 + depth,
        )
        return panel, panel.inner

    def draw_header_reactor(self, _event: tk.Event | None = None) -> None:
        self.header_reactor.delete("all")
        width = self.header_reactor.winfo_width()
        height = self.header_reactor.winfo_height()
        cy = height // 2
        self.header_reactor.create_line(0, cy, width, cy, fill=CYBER["line_soft"], width=1)
        for radius, color, line_width in ((21, CYBER["glow_soft"], 5), (17, CYBER["cyan"], 2), (9, CYBER["green"], 2)):
            self.header_reactor.create_oval(
                width - 55 - radius,
                cy - radius,
                width - 55 + radius,
                cy + radius,
                outline=color,
                width=line_width,
            )
        self.header_reactor.create_arc(width - 82, cy - 27, width - 28, cy + 27, start=25, extent=125, outline=CYBER["amber"], width=2, style="arc")
        self.header_reactor.create_line(8, cy, width - 82, cy, fill=CYBER["cyan"], width=2)
        self.header_reactor.create_line(18, cy - 9, width - 95, cy - 9, fill=CYBER["line_soft"], width=1)

    def make_window_button(self, parent: tk.Widget, text: str, command: Any, danger: bool = False) -> tk.Button:
        bg = CYBER["rail"]
        active = CYBER["fail"] if danger else CYBER["deep_blue"]
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=CYBER["text"],
            activebackground=active,
            activeforeground=CYBER["text"],
            bd=0,
            highlightthickness=0,
            width=5,
            height=2,
            font=("Cascadia Mono", 10, "bold"),
        )

    def make_text_box(self, parent: tk.Widget, **options: Any) -> tk.Text:
        defaults = {
            "bg": CYBER["field"],
            "fg": CYBER["text"],
            "insertbackground": CYBER["cyan"],
            "selectbackground": CYBER["deep_blue"],
            "highlightthickness": 1,
            "highlightbackground": CYBER["line"],
            "highlightcolor": CYBER["glow"],
            "relief": "flat",
            "bd": 0,
            "padx": 14,
            "pady": 12,
            "wrap": "word",
            "font": ("Cascadia Mono", 10),
        }
        defaults.update(options)
        return tk.Text(parent, **defaults)

    def start_window_drag(self, event: tk.Event) -> None:
        if self.is_maximized:
            return
        self.drag_start_x = event.x_root - self.winfo_x()
        self.drag_start_y = event.y_root - self.winfo_y()

    def drag_window(self, event: tk.Event) -> None:
        if self.is_maximized:
            return
        x = event.x_root - self.drag_start_x
        y = event.y_root - self.drag_start_y
        self.geometry(f"+{x}+{y}")

    def minimize_window(self) -> None:
        self.is_minimized = True
        self.overrideredirect(False)
        self.update_idletasks()
        self.iconify()

    def restore_window_chrome(self, _event: tk.Event | None = None) -> None:
        if not self.is_minimized or self.state() == "iconic":
            return
        self.is_minimized = False
        self.after(10, lambda: self.overrideredirect(True))

    def toggle_maximize(self) -> None:
        if self.is_maximized:
            self.geometry(self.normal_geometry)
            self.is_maximized = False
            return
        self.normal_geometry = self.geometry()
        self.geometry(
            f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0"
        )
        self.is_maximized = True

    def build_dashboard(self) -> None:
        self.dashboard_tab.columnconfigure(0, weight=1)
        self.dashboard_tab.rowconfigure(3, weight=1)
        self.scanline = tk.Canvas(self.dashboard_tab, height=22, bg=CYBER["panel"], highlightthickness=0)
        self.scanline.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.scanline.bind("<Configure>", self.draw_scanline)

        self.stats_label = ttk.Label(self.dashboard_tab, text="", style="Panel.TLabel", font=("Cascadia Mono", 12, "bold"), foreground=CYBER["green"])
        self.stats_label.grid(row=1, column=0, sticky="w", pady=(0, 12))
        ttk.Label(self.dashboard_tab, text="COMMAND_DECK", style="Panel.TLabel", font=("Cascadia Mono", 13, "bold"), foreground=CYBER["cyan"]).grid(row=2, column=0, sticky="w")

        prompt_outer, prompt_inner = self.make_glow_frame(self.dashboard_tab, bg=CYBER["field"], glow=CYBER["line_soft"], line=CYBER["glow"], depth=1)
        prompt_outer.grid(row=3, column=0, sticky="nsew", pady=8)
        prompt_inner.columnconfigure(0, weight=1)
        prompt_inner.rowconfigure(0, weight=1)
        self.prompt_text = self.make_text_box(prompt_inner, height=7)
        self.prompt_text.grid(row=0, column=0, sticky="nsew")
        actions = ttk.Frame(self.dashboard_tab, style="Panel.TFrame")
        actions.grid(row=4, column=0, sticky="ew")
        ttk.Button(actions, text="Queue Task", style="Accent.TButton", command=self.queue_prompt).pack(side="right")
        ttk.Label(actions, text="Use: open notepad | open C:\\path | run python --version", style="Muted.TLabel").pack(side="left")

    def draw_scanline(self, _event: tk.Event | None = None) -> None:
        self.scanline.delete("all")
        width = self.scanline.winfo_width()
        self.scanline.create_rectangle(0, 0, width, 22, fill=CYBER["panel"], outline="")
        for x in range(0, width, 42):
            self.scanline.create_line(x, 8, min(x + 24, width), 8, fill=CYBER["glow"], width=2)
            self.scanline.create_line(x + 6, 15, min(x + 14, width), 15, fill=CYBER["amber"], width=1)
        self.scanline.create_line(0, 20, width, 20, fill=CYBER["line"], width=1)

    def build_queue(self) -> None:
        self.queue_tab.columnconfigure(0, weight=1)
        self.queue_tab.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self.queue_tab, style="Panel.TFrame")
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.select_all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(toolbar, text="Select all", variable=self.select_all_var, command=self.toggle_select_all).pack(side="left")
        self.selection_label = ttk.Label(toolbar, text="0 selected", style="Muted.TLabel")
        self.selection_label.pack(side="left", padx=12)
        ttk.Button(toolbar, text="Clear Selected", command=self.clear_selected).pack(side="left")
        ttk.Button(toolbar, text="Refresh", command=self.refresh_all).pack(side="right")
        ttk.Button(toolbar, text="Clear Done", command=self.clear_done).pack(side="right", padx=(0, 12))

        deck_outer, deck_inner = self.make_glow_frame(self.queue_tab, bg=CYBER["panel"], glow=CYBER["glow_soft"], line=CYBER["line"], depth=2)
        deck_outer.grid(row=1, column=0, columnspan=2, sticky="nsew")
        deck_inner.columnconfigure(0, weight=1)
        deck_inner.rowconfigure(0, weight=1)
        deck_inner.grid_propagate(False)

        self.queue_split = tk.Frame(deck_inner, bg=CYBER["panel"])
        self.queue_split.grid(row=0, column=0, sticky="nsew")
        self.queue_split.bind("<Configure>", self.clamp_queue_split)
        self.queue_split.grid_propagate(False)

        self.queue_area = tk.Frame(self.queue_split, bg=CYBER["panel"])
        self.queue_area.grid_propagate(False)
        self.queue_area.columnconfigure(0, weight=1)
        self.queue_area.rowconfigure(0, weight=1)

        queue_inner = tk.Frame(self.queue_area, bg=CYBER["field"], highlightthickness=1, highlightbackground=CYBER["line_soft"])
        queue_inner.grid(row=0, column=0, sticky="nsew")
        queue_inner.grid_propagate(False)
        queue_inner.columnconfigure(0, weight=1)
        queue_inner.rowconfigure(0, weight=1)
        self.task_tree = ttk.Treeview(queue_inner, columns=("select", "status", "created", "prompt"), show="headings", height=1)
        tree_y_scroll = AutoScrollbar(queue_inner, orient="vertical", command=self.task_tree.yview)
        tree_x_scroll = AutoScrollbar(queue_inner, orient="horizontal", command=self.task_tree.xview)
        self.task_tree.configure(yscrollcommand=tree_y_scroll.set, xscrollcommand=tree_x_scroll.set)
        self.task_tree.heading("select", text="")
        self.task_tree.heading("status", text="Status")
        self.task_tree.heading("created", text="Created")
        self.task_tree.heading("prompt", text="Prompt")
        self.task_tree.column("select", width=48, stretch=False, anchor="center")
        self.task_tree.column("status", width=90, stretch=False)
        self.task_tree.column("created", width=150, stretch=False)
        self.task_tree.column("prompt", width=760, minwidth=360, stretch=False)
        self.task_tree.grid(row=0, column=0, sticky="nsew")
        tree_y_scroll.grid(row=0, column=1, sticky="ns")
        tree_x_scroll.grid(row=1, column=0, sticky="ew")
        self.task_tree.tag_configure("queued", foreground=CYBER["muted"])
        self.task_tree.tag_configure("running", foreground=CYBER["cyan"])
        self.task_tree.tag_configure("done", foreground=CYBER["green"])
        self.task_tree.tag_configure("failed", foreground=CYBER["fail"])
        self.task_tree.bind("<<TreeviewSelect>>", self.show_selected_task)
        self.task_tree.bind("<Button-1>", self.on_task_tree_click)

        self.queue_sash = tk.Frame(self.queue_split, bg=CYBER["line"], cursor="sb_v_double_arrow", height=QUEUE_SPLITTER_HEIGHT)
        self.queue_sash.grid_propagate(False)
        queue_sash_line = tk.Frame(self.queue_sash, bg=CYBER["amber"], height=1, width=220)
        queue_sash_line.place(relx=0.5, rely=0.5, anchor="center")
        self.queue_sash.bind("<ButtonPress-1>", self.start_queue_split_drag)
        self.queue_sash.bind("<B1-Motion>", self.drag_queue_split)
        queue_sash_line.bind("<ButtonPress-1>", self.start_queue_split_drag)
        queue_sash_line.bind("<B1-Motion>", self.drag_queue_split)

        self.detail_area = tk.Frame(self.queue_split, bg=CYBER["panel"])
        self.detail_area.grid_propagate(False)
        self.detail_area.columnconfigure(0, weight=1)
        self.detail_area.rowconfigure(1, weight=1)
        ttk.Label(self.detail_area, text="TASK_DETAIL", style="Panel.TLabel", foreground=CYBER["cyan"]).grid(row=0, column=0, sticky="w", pady=(0, 6))
        detail_inner = tk.Frame(self.detail_area, bg=CYBER["field"], highlightthickness=1, highlightbackground=CYBER["line_soft"])
        detail_inner.grid(row=1, column=0, sticky="nsew")
        detail_inner.grid_propagate(False)
        detail_inner.columnconfigure(0, weight=1)
        detail_inner.rowconfigure(0, weight=1)
        self.task_detail = self.make_text_box(detail_inner, height=1)
        detail_y_scroll = AutoScrollbar(detail_inner, orient="vertical", command=self.task_detail.yview)
        self.task_detail.configure(yscrollcommand=detail_y_scroll.set)
        self.task_detail.grid(row=0, column=0, sticky="nsew")
        detail_y_scroll.grid(row=0, column=1, sticky="ns")

        self.queue_pane_height = QUEUE_PANE_MIN_HEIGHT
        self.queue_split_positioned = False
        self.queue_split.after_idle(self.position_queue_split)

    def position_queue_split(self) -> None:
        if not hasattr(self, "queue_split"):
            return
        if self.queue_split_positioned:
            return
        height = self.queue_split.winfo_height()
        if height <= 1:
            self.queue_split.after(50, self.position_queue_split)
            return
        self.queue_split_positioned = True
        self.place_queue_sash(int(height * QUEUE_SPLIT_INITIAL_RATIO))

    def place_queue_sash(self, y: int) -> None:
        height = self.queue_split.winfo_height()
        if height <= 1:
            return
        available = max(1, height - QUEUE_SPLITTER_HEIGHT)
        if available <= QUEUE_PANE_MIN_HEIGHT + DETAIL_PANE_MIN_HEIGHT:
            min_y = max(1, min(QUEUE_PANE_MIN_HEIGHT, available // 2))
            max_y = max(min_y, available - 1)
        else:
            min_y = QUEUE_PANE_MIN_HEIGHT
            max_y = available - DETAIL_PANE_MIN_HEIGHT
        self.queue_pane_height = min(max(y, min_y), max_y)
        detail_y = self.queue_pane_height + QUEUE_SPLITTER_HEIGHT
        detail_height = max(1, height - detail_y)
        self.queue_area.place(x=0, y=0, relwidth=1, height=self.queue_pane_height)
        self.queue_sash.place(x=0, y=self.queue_pane_height, relwidth=1, height=QUEUE_SPLITTER_HEIGHT)
        self.detail_area.place(x=0, y=detail_y, relwidth=1, height=detail_height)

    def clamp_queue_split(self, _event: tk.Event | None = None) -> None:
        if not hasattr(self, "queue_split"):
            return
        if not self.queue_split_positioned:
            return
        self.place_queue_sash(getattr(self, "queue_pane_height", QUEUE_PANE_MIN_HEIGHT))

    def start_queue_split_drag(self, event: tk.Event) -> None:
        self.queue_split_positioned = True
        self.queue_split_drag_start_y = event.y_root
        self.queue_split_drag_start_height = self.queue_area.winfo_height()

    def drag_queue_split(self, event: tk.Event) -> None:
        delta = event.y_root - self.queue_split_drag_start_y
        self.place_queue_sash(self.queue_split_drag_start_height + delta)

    def build_logs(self) -> None:
        self.logs_tab.columnconfigure(0, weight=1)
        self.logs_tab.rowconfigure(0, weight=1)
        log_outer, log_inner = self.make_glow_frame(self.logs_tab, bg=CYBER["field"], glow=CYBER["glow_soft"], depth=2)
        log_outer.grid(row=0, column=0, sticky="nsew")
        log_inner.columnconfigure(0, weight=1)
        log_inner.rowconfigure(0, weight=1)
        self.log_text = self.make_text_box(log_inner)
        self.log_text.grid(row=0, column=0, sticky="nsew")

    def build_settings(self) -> None:
        self.settings_tab.columnconfigure(1, weight=1)
        self.model_var = tk.StringVar(value=self.config_data.get("model", ""))
        self.url_var = tk.StringVar(value=self.config_data.get("ollama_url", ""))
        self.ctx_var = tk.StringVar(value=str(self.config_data.get("num_ctx", 4096)))
        self.temp_var = tk.StringVar(value=str(self.config_data.get("temperature", 0.4)))
        self.model_enabled_var = tk.BooleanVar(value=bool(self.config_data.get("model_enabled", False)))
        self.shell_var = tk.BooleanVar(value=bool(self.config_data.get("allow_shell", False)))
        self.language_var = tk.StringVar(value=language_code(self.config_data))
        self.model_status_var = tk.StringVar(value="Model status has not been checked.")

        rows = [
            ("Model", self.model_var),
            ("Ollama URL", self.url_var),
            ("Context", self.ctx_var),
            ("Temperature", self.temp_var),
        ]
        for row, (label, var) in enumerate(rows):
            ttk.Label(self.settings_tab, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=6)
            ttk.Entry(self.settings_tab, textvariable=var).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Label(self.settings_tab, text="Language", style="Panel.TLabel").grid(row=4, column=0, sticky="w", pady=6)
        language_select = ttk.Combobox(
            self.settings_tab,
            textvariable=self.language_var,
            values=list(LANGUAGES.keys()),
            state="readonly",
            width=20,
        )
        language_select.grid(row=4, column=1, sticky="w", pady=6)
        ttk.Label(
            self.settings_tab,
            text="auto: detect command language, fallback English | vi | en | fr | ja | zh",
            style="Muted.TLabel",
        ).grid(row=5, column=1, sticky="w", pady=(0, 6))
        ttk.Checkbutton(self.settings_tab, text="Enable model calls", variable=self.model_enabled_var).grid(row=6, column=1, sticky="w", pady=6)
        ttk.Checkbutton(self.settings_tab, text="Allow shell commands from allowlist", variable=self.shell_var).grid(row=7, column=1, sticky="w", pady=6)
        ttk.Label(self.settings_tab, textvariable=self.model_status_var, style="Muted.TLabel", wraplength=720).grid(row=8, column=1, sticky="ew", pady=8)
        action_row = ttk.Frame(self.settings_tab, style="Panel.TFrame")
        action_row.grid(row=9, column=1, sticky="e", pady=12)
        ttk.Button(action_row, text="Test AI Model", command=self.test_model_status).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="Save Settings", style="Accent.TButton", command=self.save_settings).pack(side="left")

    def queue_prompt(self) -> None:
        prompt = self.prompt_text.get("1.0", "end").strip()
        if not prompt:
            return
        task_id = self.store.create(prompt)
        self.prompt_text.delete("1.0", "end")
        self.events.put(f"{now()} queued task #{task_id}")
        self.refresh_all()

    def worker_loop(self) -> None:
        while not self.stop_event.is_set():
            task = self.store.claim()
            if not task:
                self.stop_event.wait(1.0)
                continue
            self.events.put(f"{now()} running task #{task['id']}")
            try:
                config = load_config()
                result = process_prompt(task["prompt"], config, self.memory)
                self.store.update(task["id"], status="done", result=result, error="")
                self.events.put(f"{now()} completed task #{task['id']}")
            except Exception as exc:
                self.store.update(task["id"], status="failed", error=str(exc))
                self.events.put(f"{now()} failed task #{task['id']}: {exc}")

    def refresh_all(self) -> None:
        self.config_data = load_config()
        mode = "Prototype mode" if not self.config_data.get("model_enabled", False) else self.config_data.get("model", "")
        shell = "shell allowlist" if self.config_data.get("allow_shell", False) else "shell locked"
        self.mode_label.configure(text=f"{mode} | {language_label(self.config_data)} | {shell} | {ROOT}")

        tasks = sorted(self.store.read(), key=lambda item: item["id"], reverse=True)
        current_ids = {task["id"] for task in tasks}
        self.selected_task_ids.intersection_update(current_ids)
        counts = Counter(task.get("status", "") for task in tasks)
        self.stats_label.configure(
            text="    ".join(f"{status.title()} {counts[status]}" for status in TASK_STATUSES)
        )

        selected = self.task_tree.selection()
        for item in self.task_tree.get_children():
            self.task_tree.delete(item)
        for task in tasks:
            check = "[x]" if task["id"] in self.selected_task_ids else "[ ]"
            status = str(task.get("status", ""))
            self.task_tree.insert(
                "",
                "end",
                iid=str(task["id"]),
                values=(
                    check,
                    status,
                    task.get("created_at", ""),
                    preview_text(str(task.get("prompt", ""))),
                ),
                tags=(status,),
            )
        if selected:
            for item in selected:
                if self.task_tree.exists(item):
                    self.task_tree.selection_set(item)
                    break
        self.update_selection_state(len(tasks))

    def show_selected_task(self, _event: object | None = None) -> None:
        selected = self.task_tree.selection()
        if not selected:
            return
        task = next((item for item in self.store.read() if str(item["id"]) == selected[0]), None)
        if not task:
            return
        text = (
            f"Task #{task['id']} [{task['status']}]\n"
            f"Created: {task['created_at']}\nUpdated: {task['updated_at']}\n\n"
            f"Prompt:\n{task['prompt']}\n\n"
            f"Result:\n{task.get('result', '')}\n\n"
            f"Error:\n{task.get('error', '')}"
        )
        self.task_detail.delete("1.0", "end")
        self.task_detail.insert("1.0", text)

    def on_task_tree_click(self, event: tk.Event) -> str | None:
        region = self.task_tree.identify("region", event.x, event.y)
        if region != "cell":
            return None
        row_id = self.task_tree.identify_row(event.y)
        column = self.task_tree.identify_column(event.x)
        if not row_id:
            return None
        if column == "#1":
            self.toggle_task_selection(int(row_id))
            return "break"
        return None

    def toggle_task_selection(self, task_id: int) -> None:
        if task_id in self.selected_task_ids:
            self.selected_task_ids.remove(task_id)
        else:
            self.selected_task_ids.add(task_id)
        self.refresh_all()

    def toggle_select_all(self) -> None:
        tasks = self.store.read()
        if self.select_all_var.get():
            self.selected_task_ids = {task["id"] for task in tasks}
        else:
            self.selected_task_ids.clear()
        self.refresh_all()

    def update_selection_state(self, total: int | None = None) -> None:
        if total is None:
            total = len(self.store.read())
        selected = len(self.selected_task_ids)
        self.selection_label.configure(text=f"{selected} selected")
        self.select_all_var.set(total > 0 and selected == total)

    def clear_selected(self) -> None:
        if not self.selected_task_ids:
            return
        count = len(self.selected_task_ids)
        if not messagebox.askyesno("Clear selected tasks", f"Clear {count} selected task(s)?"):
            return
        self.store.clear_ids(set(self.selected_task_ids))
        self.selected_task_ids.clear()
        self.task_detail.delete("1.0", "end")
        self.events.put(f"{now()} cleared {count} selected task(s)")
        self.refresh_all()

    def show_dashboard(self) -> None:
        self.notebook.select(self.dashboard_tab)

    def show_queue(self) -> None:
        self.notebook.select(self.queue_tab)

    def show_logs(self) -> None:
        self.notebook.select(self.logs_tab)

    def show_settings(self) -> None:
        self.notebook.select(self.settings_tab)

    def save_settings(self) -> None:
        config = load_config()
        try:
            num_ctx = int(self.ctx_var.get())
            temperature = float(self.temp_var.get())
        except ValueError:
            messagebox.showerror("Invalid settings", "Context must be an integer and temperature must be a number.")
            return

        config["model"] = self.model_var.get().strip()
        config["ollama_url"] = self.url_var.get().strip()
        config["num_ctx"] = num_ctx
        config["temperature"] = temperature
        config["model_enabled"] = bool(self.model_enabled_var.get())
        config["language"] = self.language_var.get()
        config["allow_shell"] = bool(self.shell_var.get())
        save_config(config)
        self.events.put(f"{now()} saved settings")
        self.refresh_all()

    def test_model_status(self) -> None:
        self.save_settings()
        self.model_status_var.set("Checking Ollama...")

        def run_check() -> None:
            ok, message = check_ollama(load_config())
            prefix = "Ready" if ok else "Not ready"
            self.events.put(f"{now()} model check: {prefix}")
            self.after(0, lambda: self.model_status_var.set(message))

        threading.Thread(target=run_check, daemon=True).start()

    def clear_done(self) -> None:
        self.store.clear_done()
        self.events.put(f"{now()} cleared completed tasks")
        self.refresh_all()

    def tick(self) -> None:
        updated = False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", event + "\n")
            self.log_text.see("end")
            updated = True
        if updated:
            self.refresh_all()
        self.after(1000, self.tick)

    def on_close(self) -> None:
        self.stop_event.set()
        self.destroy()


def run_legacy_tk_app() -> None:
    app = LocalAgentDesktop()
    app.mainloop()


def run_desktop_shell() -> None:
    sys.modules.setdefault("desktop_app", sys.modules[__name__])

    try:
        import webview
    except ImportError:
        messagebox.showerror(
            "Talos",
            "Desktop WebView runtime is missing.\n\nRun:\npython -m pip install pywebview",
        )
        return

    from http.server import ThreadingHTTPServer

    from web_app import LocalAgentWebHandler, STOP_EVENT, find_port, worker_loop

    host = "127.0.0.1"
    port = find_port(host, 8787)
    threading.Thread(target=worker_loop, daemon=True).start()
    server = ThreadingHTTPServer((host, port), LocalAgentWebHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    window_ref: dict[str, Any] = {"window": None, "maximized": False}

    class WindowApi:
        def minimize(self) -> None:
            window = window_ref["window"]
            if window is not None:
                window.minimize()

        def toggle_maximize(self) -> None:
            window = window_ref["window"]
            if window is None:
                return
            if window_ref["maximized"]:
                window.restore()
                window_ref["maximized"] = False
            else:
                window.maximize()
                window_ref["maximized"] = True

        def close(self) -> None:
            window = window_ref["window"]
            if window is not None:
                window.destroy()

    def on_closed() -> None:
        STOP_EVENT.set()
        server.shutdown()
        server.server_close()

    window = webview.create_window(
        "Talos",
        f"http://{host}:{port}",
        width=1440,
        height=900,
        min_size=(1024, 680),
        background_color=CYBER["bg"],
        frameless=True,
        easy_drag=False,
        js_api=WindowApi(),
    )
    window_ref["window"] = window
    window.events.closed += on_closed
    webview.start(debug="--debug-webview" in sys.argv)


if __name__ == "__main__":
    try:
        if "--legacy-tk" in sys.argv:
            run_legacy_tk_app()
        else:
            run_desktop_shell()
    except Exception as exc:
        messagebox.showerror("Talos", str(exc))
