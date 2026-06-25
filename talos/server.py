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

from talos.core import (
    ROOT,
    load_app_identity,
    load_config,
    now,
    save_config,
)
from talos.arduino import (
    arduino_ide_status,
    cancel_arduino_compile,
    clear_arduino_compile_cache,
    delete_workspace_file,
    discover_arduino_projects,
    environment_profile,
    read_workspace_file,
    run_arduino_compile,
    save_environment_profile,
    workspace_context,
    workspace_map,
    workspace_summary,
    write_workspace_file,
)
from talos.codex_bridge import CODEX_BRIDGE
from talos.arduino_events import ArduinoEventWatcher
from talos.checkpoints import (
    create_before_save_checkpoint,
    discard_checkpoint,
    latest_saved_checkpoint,
    mark_checkpoint_saved,
    rollback_last_checkpoint,
)
from talos.native_bridge import (
    list_arduino_ide_processes,
    list_arduino_open_workspaces,
    list_arduino_tool_processes,
    list_arduino_workspace_boards,
    list_window_rows,
    native_available,
)
from talos.run_history import (
    record_patch_transition,
    record_patch_verification,
    record_rollback,
    record_verify,
    latest_verify_for_workspace,
    run_history,
)

ASSET_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
FRONTEND = ASSET_ROOT / "web_frontend" if getattr(sys, "frozen", False) else ROOT / "ui" / "web_frontend"
EVENTS: list[str] = []
EVENT_LOCK = threading.Lock()
ARDUINO_SIGNAL_LOCK = threading.Lock()
ARDUINO_SIGNAL_REVISION = 0
ARDUINO_SIGNAL_TIME = ""
ARDUINO_EVENT_WATCHER: ArduinoEventWatcher | None = None

def log_event(message: str) -> None:
    with EVENT_LOCK:
        EVENTS.append(message)
        del EVENTS[:-200]


def notify_arduino_event(reason: str = "window") -> None:
    global ARDUINO_SIGNAL_REVISION, ARDUINO_SIGNAL_TIME
    with ARDUINO_SIGNAL_LOCK:
        ARDUINO_SIGNAL_REVISION += 1
        ARDUINO_SIGNAL_TIME = now()


def arduino_event_status() -> dict[str, Any]:
    with ARDUINO_SIGNAL_LOCK:
        return {
            "revision": ARDUINO_SIGNAL_REVISION,
            "time": ARDUINO_SIGNAL_TIME,
            "event_assisted": bool(ARDUINO_EVENT_WATCHER and ARDUINO_EVENT_WATCHER.available),
        }


def start_arduino_event_watcher() -> None:
    global ARDUINO_EVENT_WATCHER
    if ARDUINO_EVENT_WATCHER is None:
        ARDUINO_EVENT_WATCHER = ArduinoEventWatcher(notify_arduino_event)
        ARDUINO_EVENT_WATCHER.start()


def stop_arduino_event_watcher() -> None:
    global ARDUINO_EVENT_WATCHER
    if ARDUINO_EVENT_WATCHER is not None:
        ARDUINO_EVENT_WATCHER.stop()
    ARDUINO_EVENT_WATCHER = None

def state_payload() -> dict[str, Any]:
    config = load_config()
    app_identity = load_app_identity()
    ide_processes = list_arduino_ide_processes()
    tool_processes = list_arduino_tool_processes()
    window_rows = list_window_rows()
    window_titles = [
        str(row.get("title") or "")
        for row in window_rows
        if str(row.get("title") or "").strip()
    ]
    arduino_projects = discover_arduino_projects(
        config,
        ide_processes=ide_processes,
        tool_processes=tool_processes,
        window_rows=window_rows,
        open_workspaces=list_arduino_open_workspaces(),
        workspace_boards=list_arduino_workspace_boards(),
    )
    arduino_summary = workspace_summary(config)
    arduino_profile = environment_profile(config, str(arduino_summary.get("path") or ""))
    arduino_map = workspace_map(config, latest_verify_for_workspace(str(arduino_summary.get("path") or "")))
    return {
        "name": app_identity["display_name"],
        "role": "Codex local tool server",
        "root": str(ROOT),
        "app": app_identity,
        "native_available": native_available(),
        "config": {
            "theme": config.get("theme", "light"),
            "arduino_workspace_path": config.get("arduino_workspace_path", ""),
            "arduino_fqbn": config.get("arduino_fqbn", ""),
            "arduino_profiles": config.get("arduino_profiles", {}),
        },
        "arduino": arduino_summary,
        "arduino_profile": arduino_profile,
        "arduino_workspace_map": arduino_map,
        "arduino_ide": arduino_ide_status(
            processes=ide_processes,
            tool_processes=tool_processes,
            titles=window_titles,
        ),
        "arduino_projects": arduino_projects,
        "arduino_events": arduino_event_status(),
        "tools": [
            "GET /api/state",
            "GET /api/arduino_context",
            "GET /api/arduino_events?since=...",
            "GET /api/arduino_profile",
            "GET /api/arduino_projects",
            "GET /api/arduino_file?path=...",
            "GET /api/arduino_checkpoint?path=...",
            "POST /api/arduino_file",
            "POST /api/arduino_rollback",
            "POST /api/arduino_delete",
            "POST /api/arduino_verify",
            "POST /api/arduino_verify_cancel",
            "POST /api/arduino_verify_cache_clear",
            "POST /api/arduino_profile",
            "GET /api/codex_status",
            "GET /api/run_history",
            "POST /api/codex_message",
            "POST /api/codex_review_patch",
            "POST /api/codex_apply_patch",
            "POST /api/codex_apply_hunk",
            "POST /api/codex_reject_hunk",
            "POST /api/codex_apply_all",
            "POST /api/codex_reject_all",
            "POST /api/codex_verify_patch",
            "POST /api/codex_save_patch",
            "POST /api/codex_reject_patch",
            "POST /api/codex_keep_external",
            "POST /api/codex_cancel",
            "POST /api/codex_thread",
            "POST /api/codex_conversation",
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
            app_identity = load_app_identity()
            self.send_json({
                "ok": True,
                "service": app_identity["display_name"],
                "role": "Codex local tool server",
                "app": app_identity,
            })
            return
        if parsed.path == "/api/arduino_context":
            config = load_config()
            summary = workspace_summary(config)
            self.send_json({
                "ok": True,
                "context": workspace_context(config),
                "arduino": summary,
                "workspace_map": workspace_map(config, latest_verify_for_workspace(str(summary.get("path") or ""))),
            })
            return
        if parsed.path == "/api/arduino_events":
            self.send_json({"ok": True, **arduino_event_status()})
            return
        if parsed.path == "/api/arduino_profile":
            config = load_config()
            workspace_path = query.get("path", [str(config.get("arduino_workspace_path") or "")])[0]
            self.send_json({"ok": True, "profile": environment_profile(config, workspace_path)})
            return
        if parsed.path == "/api/arduino_projects":
            config = load_config()
            self.send_json({"ok": True, "projects": discover_arduino_projects(config)})
            return
        if parsed.path == "/api/arduino_file":
            result = read_workspace_file(load_config(), query.get("path", [""])[0])
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/arduino_checkpoint":
            result = latest_saved_checkpoint(load_config(), query.get("path", [""])[0])
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/codex_status":
            self.send_json(CODEX_BRIDGE.status())
            return
        if parsed.path == "/api/run_history":
            self.send_json({"ok": True, "events": run_history()})
            return
        path = parsed.path
        if path == "/":
            path = "/index.html"
        self.send_static(path)

    def do_POST(self) -> None:
        payload = self.read_json()
        if self.path == "/api/settings":
            config = load_config()
            for key in ("theme", "arduino_workspace_path", "arduino_fqbn"):
                if key in payload:
                    config[key] = str(payload[key]).strip()
            save_config(config)
            log_event(f"{now()} saved settings")
            self.send_json({"ok": True, "config": load_config()})
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
        if self.path == "/api/arduino_profile":
            config = load_config()
            workspace_path = str(payload.get("path", config.get("arduino_workspace_path", ""))).strip()
            result = save_environment_profile(config, workspace_path, payload)
            if not result.get("ok"):
                self.send_json(result, HTTPStatus.BAD_REQUEST)
                return
            if str(config.get("arduino_workspace_path") or "").strip() == workspace_path:
                profile_fqbn = str(result["profile"].get("fqbn") or "")
                if profile_fqbn:
                    config["arduino_fqbn"] = profile_fqbn
            save_config(config)
            log_event(f"{now()} saved Arduino environment profile: {result.get('path')}")
            self.send_json(result)
            return
        if self.path == "/api/arduino_verify":
            config = load_config()
            if "path" in payload:
                config["arduino_workspace_path"] = str(payload.get("path", "")).strip()
            if "fqbn" in payload:
                config["arduino_fqbn"] = str(payload.get("fqbn", "")).strip()
            save_config(config)
            result = run_arduino_compile(config)
            record_verify(result, str(payload.get("source") or "manual"))
            if str(payload.get("source") or "manual") == "codex_patch":
                record_patch_verification(str(workspace_summary(config).get("path") or ""), result)
            status = "passed" if result.get("ok") else result.get("status", "failed")
            log_event(f"{now()} Arduino verify {status}")
            self.send_json(result)
            return
        if self.path == "/api/arduino_verify_cancel":
            result = cancel_arduino_compile()
            if result.get("ok"):
                log_event(f"{now()} Arduino verify cancellation requested")
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.CONFLICT)
            return
        if self.path == "/api/arduino_verify_cache_clear":
            cleared = clear_arduino_compile_cache()
            log_event(f"{now()} cleared Arduino compile cache ({cleared} entries)")
            self.send_json({"ok": True, "cleared": cleared})
            return
        if self.path == "/api/arduino_file":
            config = load_config()
            checkpoint_result = create_before_save_checkpoint(config, str(payload.get("path", "")))
            if not checkpoint_result.get("ok"):
                self.send_json(checkpoint_result, HTTPStatus.BAD_REQUEST)
                return
            result = write_workspace_file(
                config,
                str(payload.get("path", "")),
                str(payload.get("content", "")),
            )
            checkpoint = checkpoint_result.get("checkpoint") if isinstance(checkpoint_result.get("checkpoint"), dict) else {}
            checkpoint_id = str(checkpoint.get("id") or "")
            if result.get("ok"):
                checkpoint_saved = mark_checkpoint_saved(checkpoint_id, str(payload.get("content", "")))
                result["checkpoint"] = checkpoint_saved.get("checkpoint") if checkpoint_saved.get("ok") else None
                workspace = workspace_summary(config)
                patch_result = CODEX_BRIDGE.mark_patch_saved(str(workspace.get("path") or ""), str(result.get("path") or ""))
                if patch_result.get("saved"):
                    record_patch_transition(patch_result.get("patch") or {}, "saved", str(result.get("path") or ""))
                log_event(f"{now()} wrote Arduino file: {result.get('path')}")
            elif checkpoint_id:
                discard_checkpoint(checkpoint_id)
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/arduino_rollback":
            config = load_config()
            result = rollback_last_checkpoint(config, str(payload.get("path", "")))
            if result.get("ok"):
                record_rollback(str(workspace_summary(config).get("path") or ""), str(result.get("path") or ""))
                log_event(f"{now()} rolled back Arduino file: {result.get('path')}")
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/arduino_delete":
            result = delete_workspace_file(load_config(), str(payload.get("path", "")))
            if result.get("ok"):
                log_event(f"{now()} deleted Arduino file: {result.get('path')}")
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/codex_message":
            config = load_config()
            workspace = workspace_summary(config)
            workspace["map"] = workspace_map(
                config,
                latest_verify_for_workspace(str(workspace.get("path") or "")),
            )
            active_file = payload.get("active_file")
            if not isinstance(active_file, dict):
                active_file = {}
            result = CODEX_BRIDGE.send_message(
                str(payload.get("message", "")),
                workspace,
                active_file,
                str(payload.get("verify_context", "")),
                bool(payload.get("allow_edits", True)),
            )
            if result.get("ok"):
                log_event(f"{now()} started Codex turn")
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/codex_apply_patch":
            workspace = workspace_summary(load_config())
            result = CODEX_BRIDGE.apply_patch(
                str(payload.get("id", "")),
                str(workspace.get("path") or ""),
                str(payload.get("path", "")),
            )
            if result.get("ok"):
                record_patch_transition(result.get("patch") or {}, "editor-applied", str(payload.get("path") or ""))
                log_event(f"{now()} applied Codex patch to Talos editor: {payload.get('path', '')}")
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/codex_apply_hunk":
            workspace = workspace_summary(load_config())
            result = CODEX_BRIDGE.apply_hunk(
                str(payload.get("id", "")),
                str(workspace.get("path") or ""),
                str(payload.get("path", "")),
                str(payload.get("hunk_id", "")),
            )
            if result.get("ok"):
                record_patch_transition(result.get("patch") or {}, "hunk-applied", str(payload.get("path") or ""), {"hunk_id": str(payload.get("hunk_id") or "")})
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/codex_reject_hunk":
            workspace = workspace_summary(load_config())
            result = CODEX_BRIDGE.reject_hunk(
                str(payload.get("id", "")),
                str(workspace.get("path") or ""),
                str(payload.get("path", "")),
                str(payload.get("hunk_id", "")),
            )
            if result.get("ok"):
                record_patch_transition(result.get("patch") or {}, "hunk-rejected", str(payload.get("path") or ""), {"hunk_id": str(payload.get("hunk_id") or "")})
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/codex_apply_all":
            workspace = workspace_summary(load_config())
            result = CODEX_BRIDGE.apply_all(str(payload.get("id", "")), str(workspace.get("path") or ""))
            if result.get("ok"):
                record_patch_transition(result.get("patch") or {}, "turn-applied")
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/codex_reject_all":
            workspace = workspace_summary(load_config())
            result = CODEX_BRIDGE.reject_all(str(payload.get("id", "")), str(workspace.get("path") or ""))
            if result.get("ok"):
                record_patch_transition(result.get("patch") or {}, "turn-rejected")
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/codex_verify_patch":
            config = load_config()
            workspace = workspace_summary(config)
            staged = CODEX_BRIDGE.staged_sandbox_overrides(
                str(payload.get("id", "")),
                str(workspace.get("path") or ""),
            )
            if not staged.get("ok"):
                self.send_json(staged, HTTPStatus.BAD_REQUEST)
                return
            result = run_arduino_compile(config, overrides=staged.get("overrides") or {})
            result["patch_id"] = str(payload.get("id") or "")
            record_verify(result, "codex_patch")
            record_patch_transition(
                staged.get("patch") or {},
                "staged-verified",
                detail={"status": str(result.get("status") or "failed"), "ok": bool(result.get("ok"))},
            )
            log_event(f"{now()} staged Codex patch verify {result.get('status', 'failed')}")
            self.send_json(result)
            return
        if self.path == "/api/codex_review_patch":
            workspace = workspace_summary(load_config())
            result = CODEX_BRIDGE.review_patch(
                str(payload.get("id", "")),
                str(workspace.get("path") or ""),
                str(payload.get("path", "")),
            )
            if result.get("ok"):
                log_event(f"{now()} opened Codex change review: {payload.get('path', '')}")
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/codex_save_patch":
            workspace = workspace_summary(load_config())
            result = CODEX_BRIDGE.mark_patch_saved(
                str(workspace.get("path") or ""),
                str(payload.get("path", "")),
            )
            if result.get("ok") and result.get("saved") is not False:
                log_event(f"{now()} saved Codex change: {payload.get('path', '')}")
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/codex_reject_patch":
            result = CODEX_BRIDGE.reject_patch(str(payload.get("id", "")), str(payload.get("path", "")))
            if result.get("ok"):
                record_patch_transition(result.get("patch") or {}, "rejected", str(payload.get("path") or ""))
                log_event(f"{now()} rejected Codex patch: {payload.get('id', '')}")
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/codex_keep_external":
            workspace = workspace_summary(load_config())
            result = CODEX_BRIDGE.keep_external_conflict(
                str(payload.get("id", "")),
                str(workspace.get("path") or ""),
                str(payload.get("path", "")),
            )
            if result.get("ok"):
                record_patch_transition(result.get("patch") or {}, "kept-external", str(payload.get("path") or ""))
                log_event(f"{now()} kept external Arduino file for Codex conflict: {payload.get('path', '')}")
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/codex_thread":
            result = CODEX_BRIDGE.new_thread()
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/codex_cancel":
            result = CODEX_BRIDGE.cancel_turn()
            self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/codex_conversation":
            result = CODEX_BRIDGE.select_conversation(str(payload.get("id", "")))
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
        if path.name == "index.html":
            theme = str(load_config().get("theme", "light"))
            data = path.read_text(encoding="utf-8").replace("__TALOS_THEME__", theme).encode("utf-8")
        else:
            data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
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
    server = ThreadingHTTPServer((args.host, port), LocalAgentWebHandler)
    start_arduino_event_watcher()
    print(f"Local Agent Web UI: http://{args.host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_arduino_event_watcher()
        CODEX_BRIDGE.shutdown()
        server.server_close()

if __name__ == "__main__":
    main()
