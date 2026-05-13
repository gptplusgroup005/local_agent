from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from desktop_app import (
    ROOT,
    TASKS_PATH,
    TASK_STATUSES,
    MEMORY_PATH,
    ConversationMemory,
    TaskStore,
    check_ollama,
    language_code,
    language_label,
    load_config,
    now,
    preview_text,
    process_prompt,
    save_config,
)

ASSET_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
FRONTEND = ASSET_ROOT / "web_frontend"
STORE = TaskStore(TASKS_PATH)
MEMORY = ConversationMemory(MEMORY_PATH)
EVENTS: list[str] = []
EVENT_LOCK = threading.Lock()
STOP_EVENT = threading.Event()


def log_event(message: str) -> None:
    with EVENT_LOCK:
        EVENTS.append(message)
        del EVENTS[:-200]


def worker_loop() -> None:
    while not STOP_EVENT.is_set():
        task = STORE.claim()
        if not task:
            STOP_EVENT.wait(0.7)
            continue
        log_event(f"{now()} running task #{task['id']}")
        try:
            config = load_config()
            result = process_prompt(task["prompt"], config, MEMORY)
            STORE.update(task["id"], status="done", result=result, error="")
            log_event(f"{now()} completed task #{task['id']}")
        except Exception as exc:
            STORE.update(task["id"], status="failed", error=str(exc))
            log_event(f"{now()} failed task #{task['id']}: {exc}")


def state_payload() -> dict[str, Any]:
    config = load_config()
    tasks = sorted(STORE.read(), key=lambda item: item["id"], reverse=True)
    counts = {status: 0 for status in TASK_STATUSES}
    for task in tasks:
        status = str(task.get("status", ""))
        if status in counts:
            counts[status] += 1
    return {
        "root": str(ROOT),
        "language": language_label(config),
        "language_code": language_code(config),
        "mode": "Prototype mode" if not config.get("model_enabled", False) else config.get("model", ""),
        "shell": "shell allowlist" if config.get("allow_shell", False) else "shell locked",
        "config": {
            "model": config.get("model", ""),
            "ollama_url": config.get("ollama_url", ""),
            "num_ctx": config.get("num_ctx", 4096),
            "temperature": config.get("temperature", 0.4),
            "model_enabled": bool(config.get("model_enabled", False)),
            "allow_shell": bool(config.get("allow_shell", False)),
            "language": config.get("language", "vi"),
        },
        "counts": counts,
        "tasks": [
            {
                "id": task.get("id"),
                "status": task.get("status", ""),
                "created_at": task.get("created_at", ""),
                "updated_at": task.get("updated_at", ""),
                "prompt": task.get("prompt", ""),
                "preview": preview_text(str(task.get("prompt", ""))),
                "result": task.get("result", ""),
                "error": task.get("error", ""),
            }
            for task in tasks
        ],
        "events": list(EVENTS),
    }


class LocalAgentWebHandler(BaseHTTPRequestHandler):
    server_version = "LocalAgentWeb/1.0"

    def do_GET(self) -> None:
        if self.path == "/api/state":
            self.send_json(state_payload())
            return
        path = self.path.split("?", 1)[0]
        if path == "/":
            path = "/index.html"
        self.send_static(path)

    def do_POST(self) -> None:
        payload = self.read_json()
        if self.path == "/api/tasks":
            prompt = str(payload.get("prompt", "")).strip()
            if not prompt:
                self.send_json({"error": "Prompt is required."}, HTTPStatus.BAD_REQUEST)
                return
            task_id = STORE.create(prompt)
            log_event(f"{now()} queued task #{task_id}")
            self.send_json({"id": task_id})
            return
        if self.path == "/api/clear_done":
            STORE.clear_done()
            log_event(f"{now()} cleared completed tasks")
            self.send_json({"ok": True})
            return
        if self.path == "/api/clear_selected":
            ids = {int(item) for item in payload.get("ids", []) if str(item).isdigit()}
            STORE.clear_ids(ids)
            log_event(f"{now()} cleared {len(ids)} selected task(s)")
            self.send_json({"ok": True})
            return
        if self.path == "/api/settings":
            config = load_config()
            for key in ("model", "ollama_url", "language"):
                if key in payload:
                    config[key] = str(payload[key]).strip()
            for key in ("model_enabled", "allow_shell"):
                if key in payload:
                    config[key] = bool(payload[key])
            try:
                if "num_ctx" in payload:
                    config["num_ctx"] = int(payload["num_ctx"])
                if "temperature" in payload:
                    config["temperature"] = float(payload["temperature"])
            except (TypeError, ValueError):
                self.send_json({"error": "Context must be an integer and temperature must be a number."}, HTTPStatus.BAD_REQUEST)
                return
            save_config(config)
            log_event(f"{now()} saved settings")
            self.send_json({"ok": True})
            return
        if self.path == "/api/check_model":
            ok, message = check_ollama(load_config())
            log_event(f"{now()} model check: {'Ready' if ok else 'Not ready'}")
            self.send_json({"ok": ok, "message": message})
            return
        self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_static(self, raw_path: str) -> None:
        relative = unquote(raw_path.lstrip("/"))
        path = (FRONTEND / relative).resolve()
        frontend_root = FRONTEND.resolve()
        try:
            path.relative_to(frontend_root)
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def find_port(host: str, start_port: int) -> int:
    for port in range(start_port, start_port + 20):
        try:
            server = ThreadingHTTPServer((host, port), LocalAgentWebHandler)
        except OSError:
            continue
        server.server_close()
        return port
    raise RuntimeError(f"No available port found from {start_port}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Local Agent web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    args = parser.parse_args()

    port = find_port(args.host, args.port)
    threading.Thread(target=worker_loop, daemon=True).start()
    server = ThreadingHTTPServer((args.host, port), LocalAgentWebHandler)
    print(f"Local Agent Web UI: http://{args.host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STOP_EVENT.set()
        server.server_close()


if __name__ == "__main__":
    main()
