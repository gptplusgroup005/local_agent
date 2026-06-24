from __future__ import annotations

import threading
import uuid
from typing import Any

from talos.core import ROOT, now, read_json_file, write_json_file

RUN_HISTORY_PATH = ROOT / "config" / "run_history.json"
RUN_HISTORY_LIMIT = 40
RUN_HISTORY_LOCK = threading.Lock()

def _load_events() -> list[dict[str, Any]]:
    data = read_json_file(RUN_HISTORY_PATH, {})
    events = data.get("events") if isinstance(data, dict) else []
    return [event for event in (events or []) if isinstance(event, dict)][-RUN_HISTORY_LIMIT:]

def _store_event(event: dict[str, Any]) -> dict[str, Any]:
    with RUN_HISTORY_LOCK:
        events = _load_events()
        events.append(event)
        write_json_file(RUN_HISTORY_PATH, {"events": events[-RUN_HISTORY_LIMIT:]})
    return event

def record_verify(result: dict[str, Any], source: str = "manual") -> dict[str, Any]:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    event = {
        "id": f"verify-{uuid.uuid4().hex}",
        "type": "verify",
        "time": now(),
        "source": source if source in {"manual", "codex_patch"} else "manual",
        "workspace": str(summary.get("path") or ""),
        "main_sketch": str(summary.get("main_sketch") or ""),
        "fqbn": str(summary.get("fqbn") or ""),
        "status": str(result.get("status") or ("passed" if result.get("ok") else "failed")),
        "ok": bool(result.get("ok")),
        "result": {
            "ok": bool(result.get("ok")),
            "status": str(result.get("status") or ""),
            "command": str(result.get("command") or "")[:12000],
            "sandbox": str(result.get("sandbox") or ""),
            "output": str(result.get("output") or "")[:50000],
            "memory": result.get("memory") or {},
            "libraries": result.get("libraries") or [],
            "platforms": result.get("platforms") or [],
            "issues": result.get("issues") or [],
            "issue_context": str(result.get("issue_context") or "")[:12000],
        },
    }
    return _store_event(event)

def record_patch(patch: dict[str, Any]) -> dict[str, Any]:
    with RUN_HISTORY_LOCK:
        events = _load_events()
        event = _upsert_patch_event(events, patch)
        _store_events(events)
    return event

def record_patch_transition(
    patch: dict[str, Any],
    action: str,
    relative_path: str = "",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with RUN_HISTORY_LOCK:
        events = _load_events()
        event = _upsert_patch_event(events, patch)
        entry = {"time": now(), "action": action}
        if relative_path:
            entry["path"] = relative_path
        if detail:
            entry["detail"] = detail
        event.setdefault("timeline", []).append(entry)
        event["time"] = entry["time"]
        _store_events(events)
    return event

def record_patch_verification(workspace: str, result: dict[str, Any]) -> dict[str, Any] | None:
    with RUN_HISTORY_LOCK:
        events = _load_events()
        event = next(
            (
                item for item in reversed(events)
                if item.get("type") == "patch" and item.get("workspace") == workspace
            ),
            None,
        )
        if event is None:
            return None
        entry = {
            "time": now(),
            "action": "verified",
            "detail": {"status": str(result.get("status") or "failed"), "ok": bool(result.get("ok"))},
        }
        event.setdefault("timeline", []).append(entry)
        event["time"] = entry["time"]
        _store_events(events)
    return event

def record_rollback(workspace: str, relative_path: str) -> dict[str, Any] | None:
    with RUN_HISTORY_LOCK:
        events = _load_events()
        event = next(
            (
                item for item in reversed(events)
                if item.get("type") == "patch"
                and item.get("workspace") == workspace
                and any(file.get("path") == relative_path for file in item.get("files") or [])
            ),
            None,
        )
        if event is None:
            return None
        entry = {"time": now(), "action": "rolled-back", "path": relative_path}
        event.setdefault("timeline", []).append(entry)
        event["time"] = entry["time"]
        _store_events(events)
    return event

def _patch_files(patch: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "path": str(file.get("path") or ""),
            "kind": str(file.get("kind") or "update"),
            "status": str(file.get("review_status") or "staged"),
            "hunks": len(file.get("hunks") or []),
        }
        for file in (patch.get("files") or [])
        if isinstance(file, dict)
    ]

def _upsert_patch_event(events: list[dict[str, Any]], patch: dict[str, Any]) -> dict[str, Any]:
    patch_id = str(patch.get("id") or "")
    event = next((item for item in reversed(events) if item.get("type") == "patch" and item.get("patch_id") == patch_id), None)
    if event is None:
        event = {
            "id": f"patch-event-{uuid.uuid4().hex}",
            "type": "patch",
            "time": str(patch.get("time") or now()),
            "source": "codex",
            "workspace": str(patch.get("workspace") or ""),
            "patch_id": patch_id,
            "timeline": [{"time": str(patch.get("time") or now()), "action": "staged"}],
        }
        events.append(event)
    event["status"] = str(patch.get("review_status") or "staged")
    event["files"] = _patch_files(patch)
    return event

def _store_events(events: list[dict[str, Any]]) -> None:
    write_json_file(RUN_HISTORY_PATH, {"events": events[-RUN_HISTORY_LIMIT:]})

def run_history() -> list[dict[str, Any]]:
    with RUN_HISTORY_LOCK:
        return list(reversed(_load_events()))

def latest_verify_for_workspace(workspace: str) -> dict[str, Any] | None:
    target = str(workspace or "")
    if not target:
        return None
    with RUN_HISTORY_LOCK:
        return next(
            (
                event for event in reversed(_load_events())
                if event.get("type") == "verify" and event.get("workspace") == target
            ),
            None,
        )
