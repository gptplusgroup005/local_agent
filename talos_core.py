from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
APP_MIN_WIDTH = 920
APP_MIN_HEIGHT = 620
WEBVIEW_MIN_WIDTH = 520
WEBVIEW_MIN_HEIGHT = 420
QUEUE_PANE_MIN_HEIGHT = 132
DETAIL_PANE_MIN_HEIGHT = 140
QUEUE_SPLIT_INITIAL_RATIO = 0.38
QUEUE_SPLITTER_HEIGHT = 14
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"

CYBER = {
    "bg": "#08020f",
    "bg_2": "#140625",
    "panel": "#170a2b",
    "panel_2": "#241047",
    "field": "#0d0418",
    "rail": "#07020d",
    "line": "#8f4dff",
    "line_soft": "#3a1a68",
    "text": "#e8fbff",
    "muted": "#c8a8ff",
    "cyan": "#e15cff",
    "blue": "#9b5cff",
    "deep_blue": "#4b1d95",
    "green": "#29ffc6",
    "amber": "#ff8bd8",
    "warn": "#ffd166",
    "fail": "#ff5c8a",
    "glow": "#f05cff",
    "glow_soft": "#30105a",
    "violet": "#b56cff",
}

LANGUAGES = {
    "auto": {"label": "Auto", "instruction": "the same language as the user's command; if unsure, use English", "time": "Current local time", "date": "Current local date", "prototype": "Prototype mode"},
    "vi": {"label": "Tiáº¿ng Viá»‡t", "instruction": "Vietnamese", "time": "Giá» Ä‘á»‹a phÆ°Æ¡ng", "date": "NgÃ y Ä‘á»‹a phÆ°Æ¡ng", "prototype": "Cháº¿ Ä‘á»™ prototype"},
    "en": {"label": "English", "instruction": "English", "time": "Current local time", "date": "Current local date", "prototype": "Prototype mode"},
    "fr": {"label": "FranÃ§ais", "instruction": "French", "time": "Heure locale", "date": "Date locale", "prototype": "Mode prototype"},
    "ja": {"label": "æ—¥æœ¬èªž", "instruction": "Japanese", "time": "ç¾åœ°æ™‚åˆ»", "date": "ç¾åœ°æ—¥ä»˜", "prototype": "ãƒ—ãƒ­ãƒˆã‚¿ã‚¤ãƒ—ãƒ¢ãƒ¼ãƒ‰"},
    "zh": {"label": "ä¸­æ–‡", "instruction": "Chinese", "time": "æœ¬åœ°æ—¶é—´", "date": "æœ¬åœ°æ—¥æœŸ", "prototype": "åŽŸåž‹æ¨¡å¼"},
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
    "language": "vi",
    "arduino_workspace_path": "",
    "arduino_fqbn": "",
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
    if any(char in text for char in "ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"):
        return "vi"
    if any(char in text for char in "àâæçéèêëîïôœùûüÿ"):
        return "fr"
    vietnamese_marks = "ÄƒÃ¢Ä‘ÃªÃ´Æ¡Æ°Ã¡Ã áº£Ã£áº¡áº¥áº§áº©áº«áº­áº¯áº±áº³áºµáº·Ã©Ã¨áº»áº½áº¹áº¿á»á»ƒá»…á»‡Ã­Ã¬á»‰Ä©á»‹Ã³Ã²á»Ãµá»á»‘á»“á»•á»—á»™á»›á»á»Ÿá»¡á»£ÃºÃ¹á»§Å©á»¥á»©á»«á»­á»¯á»±Ã½á»³á»·á»¹á»µ"
    if any(char in text for char in vietnamese_marks):
        return "vi"
    french_marks = "Ã Ã¢Ã¦Ã§Ã©Ã¨ÃªÃ«Ã®Ã¯Ã´Å“Ã¹Ã»Ã¼Ã¿"
    if any(char in text for char in french_marks):
        return "fr"

    tokens = set(re.findall(r"[a-z']+", text))
    vi_words = {"toi", "tÃ´i", "ban", "báº¡n", "hay", "hÃ£y", "ngon", "ngá»¯", "tinh", "tÃ­nh", "mo", "má»Ÿ", "ngay", "hÃ´m", "gio"}
    fr_words = {"bonjour", "salut", "merci", "calcule", "calculer", "ouvre", "ouvrir", "aujourd", "heure", "date", "resultat", "rÃ©sultat"}
    vi_words.update({"toi", "tôi", "ban", "bạn", "hay", "hãy", "ngon", "ngữ", "tinh", "tính", "mo", "mở", "ngay", "hôm", "gio"})
    fr_words.update({"bonjour", "salut", "merci", "calcule", "calculer", "ouvre", "ouvrir", "aujourd", "heure", "date", "resultat", "résultat"})
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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)

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

def queue_split_initial_sash_y(total_height: int) -> int:
    pane_space = max(1, total_height - QUEUE_SPLITTER_HEIGHT)
    requested = round(pane_space * QUEUE_SPLIT_INITIAL_RATIO)
    if pane_space < QUEUE_PANE_MIN_HEIGHT + DETAIL_PANE_MIN_HEIGHT:
        return min(max(1, requested), max(1, pane_space - 1))
    return min(
        max(QUEUE_PANE_MIN_HEIGHT, requested),
        pane_space - DETAIL_PANE_MIN_HEIGHT,
    )

class TaskStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self._cache: list[dict[str, Any]] | None = None
        self._cache_mtime_ns: int | None = None
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
                    self._write_unlocked(tasks)
                    break

    def clear_done(self) -> None:
        with self.lock:
            current = self._read_unlocked()
            tasks = [task for task in current if task["status"] != "done"]
            if len(tasks) != len(current):
                self._write_unlocked(tasks)

    def clear_ids(self, task_ids: set[int]) -> None:
        if not task_ids:
            return
        with self.lock:
            current = self._read_unlocked()
            tasks = [task for task in current if task["id"] not in task_ids]
            if len(tasks) != len(current):
                self._write_unlocked(tasks)

    def _read_unlocked(self) -> list[dict[str, Any]]:
        try:
            mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            mtime_ns = None
        if self._cache is not None and self._cache_mtime_ns == mtime_ns:
            return copy.deepcopy(self._cache)
        data = read_json_file(self.path, [])
        if not isinstance(data, list):
            return []
        tasks = [item for item in data if isinstance(item, dict) and isinstance(item.get("id"), int)]
        self._cache = copy.deepcopy(tasks)
        self._cache_mtime_ns = mtime_ns
        return copy.deepcopy(tasks)

    def _write_unlocked(self, tasks: list[dict[str, Any]]) -> None:
        write_json_file(self.path, tasks)
        try:
            self._cache_mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            self._cache_mtime_ns = None
        self._cache = copy.deepcopy(tasks)

class ConversationMemory:
    def __init__(self, path: Path, limit: int = MEMORY_TURN_LIMIT) -> None:
        self.path = path
        self.limit = limit
        self.lock = threading.Lock()
        self._cache: list[dict[str, str]] | None = None
        self._cache_mtime_ns: int | None = None
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
        try:
            self._cache_mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            self._cache_mtime_ns = None
        self._cache = copy.deepcopy(items)

    def _read_unlocked(self) -> list[dict[str, str]]:
        try:
            mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            mtime_ns = None
        if self._cache is not None and self._cache_mtime_ns == mtime_ns:
            return copy.deepcopy(self._cache)
        data = read_json_file(self.path, [])
        items = data if isinstance(data, list) else []
        self._cache = copy.deepcopy(items)
        self._cache_mtime_ns = mtime_ns
        return copy.deepcopy(items)

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

class LocalComputerActionEngine:
    def __init__(self, config: dict[str, Any]) -> None:
        self.tools = ComputerTools(config)

    def handle(self, prompt: str) -> str | None:
        text = prompt.strip()
        if not text:
            return "No task content."

        tool_result = self.tools.handle(text)
        if tool_result is not None:
            return tool_result

        return None

def memory_messages(memory: list[dict[str, str]]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in memory[-MEMORY_TURN_LIMIT:]:
        role = item.get("role", "")
        content = item.get("content", "")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    return messages

def process_prompt(prompt: str, config: dict[str, Any], memory: ConversationMemory | None = None) -> str:
    action_engine = LocalComputerActionEngine(config)
    local_result = action_engine.handle(prompt)
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
                    "Do not tell the user to search Google or use another app. "
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
            f"ollama pull {config.get('model', DEFAULT_MODEL)}\n\n"
            f"Configured endpoint: {config.get('ollama_url')}\n"
            f"Details: {exc}"
        ) from exc

def ollama_base_url(config: dict[str, Any]) -> str:
    parsed = urlparse(config.get("ollama_url", DEFAULT_OLLAMA_CHAT_URL))
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

