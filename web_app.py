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
from urllib.parse import parse_qs, unquote, urlparse

from talos_core import (
    ROOT,
    language_code,
    language_label,
    load_config,
    now,
    save_config,
)
from talos_arduino import (
    delete_workspace_file,
    discover_arduino_projects,
    read_workspace_file,
    run_arduino_compile,
    workspace_context,
    workspace_summary,
    write_workspace_file,
)

ASSET_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
FRONTEND = ASSET_ROOT / "web_frontend"
EVENTS: list[str] = []
EVENT_LOCK = threading.Lock()
STOP_EVENT = threading.Event()

def log_event(message: str) -> None:
    with EVENT_LOCK:
        EVENTS.append(message)
        del EVENTS[:-200]

def worker_loop() -> None:
    while not STOP_EVENT.is_set():
        STOP_EVENT.wait(60)

def state_payload() -> dict[str, Any]:
    config = load_config()
    return {
        "name": "Talos",
        "role": "Codex local tool server",
        "root": str(ROOT),
        "language": language_label(config),
        "language_code": language_code(config),
        "config": {
            "language": config.get("language", "vi"),
            "arduino_workspace_path": config.get("arduino_workspace_path", ""),
            "arduino_fqbn": config.get("arduino_fqbn", ""),
        },
        "arduino": workspace_summary(config),
        "arduino_projects": discover_arduino_projects(config),
        "tools": [
            "GET /api/state",
            "GET /api/arduino_context",
            "GET /api/arduino_projects",
            "GET /api/arduino_file?path=...",
            "POST /api/arduino_file",
            "POST /api/arduino_delete",
            "POST /api/arduino_verify",
        ],
        "events": list(EVENTS),
    }

class LocalAgentWebHandler(BaseHTTPRequestHandler):
    server_version = "LocalAgentWeb/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if self.path == "/api/state":
            self.send_json(state_payload())
            return
        if parsed.path == "/api/health":
            self.send_json({"ok": True, "service": "Talos", "role": "Codex local tool server"})
            return
        if parsed.path == "/api/arduino_context":
            config = load_config()
            self.send_json({"ok": True, "context": workspace_context(config), "arduino": workspace_summary(config)})
            return
        if parsed.path == "/api/arduino_projects":
            config = load_config()
            self.send_json({"ok": True, "projects": discover_arduino_projects(config)})
            return
        if parsed.path == "/api/arduino_file":
            result = read_workspace_file(load_config(), query.get("path", [""])[0])
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        path = parsed.path
        if path == "/":
            path = "/index.html"
        self.send_static(path)

    def do_POST(self) -> None:
        payload = self.read_json()
        if self.path == "/api/settings":
            config = load_config()
            for key in ("language", "arduino_workspace_path", "arduino_fqbn"):
                if key in payload:
                    config[key] = str(payload[key]).strip()
            save_config(config)
            log_event(f"{now()} saved settings")
            self.send_json({"ok": True})
            return
        if self.path == "/api/arduino_workspace":
            config = load_config()
            config["arduino_workspace_path"] = str(payload.get("path", "")).strip()
            config["arduino_fqbn"] = str(payload.get("fqbn", config.get("arduino_fqbn", ""))).strip()
            save_config(config)
            summary = workspace_summary(config)
            log_event(f"{now()} configured Arduino workspace: {summary.get('path') or 'none'}")
            self.send_json({"ok": summary["valid"], "arduino": summary})
            return
        if self.path == "/api/arduino_verify":
            config = load_config()
            if "path" in payload:
                config["arduino_workspace_path"] = str(payload.get("path", "")).strip()
            if "fqbn" in payload:
                config["arduino_fqbn"] = str(payload.get("fqbn", "")).strip()
            save_config(config)
            result = run_arduino_compile(config)
            status = "passed" if result.get("ok") else result.get("status", "failed")
            log_event(f"{now()} Arduino verify {status}")
            self.send_json(result)
            return
        if self.path == "/api/arduino_file":
            result = write_workspace_file(
                load_config(),
                str(payload.get("path", "")),
                str(payload.get("content", "")),
            )
            if result.get("ok"):
                log_event(f"{now()} wrote Arduino file: {result.get('path')}")
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/arduino_delete":
            result = delete_workspace_file(load_config(), str(payload.get("path", "")))
            if result.get("ok"):
                log_event(f"{now()} deleted Arduino file: {result.get('path')}")
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
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
