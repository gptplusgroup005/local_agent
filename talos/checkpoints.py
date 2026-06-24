from __future__ import annotations

import hashlib
import threading
import uuid
from typing import Any

from talos.arduino import configured_workspace, read_workspace_file, write_workspace_file
from talos.core import ROOT, now, read_json_file, write_json_file

CHECKPOINT_PATH = ROOT / "config" / "checkpoints.json"
CHECKPOINT_LIMIT = 80
CHECKPOINT_LOCK = threading.Lock()

def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def _load() -> list[dict[str, Any]]:
    data = read_json_file(CHECKPOINT_PATH, {})
    checkpoints = data.get("checkpoints") if isinstance(data, dict) else []
    return [item for item in (checkpoints or []) if isinstance(item, dict)][-CHECKPOINT_LIMIT:]

def _store(checkpoints: list[dict[str, Any]]) -> None:
    write_json_file(CHECKPOINT_PATH, {"checkpoints": checkpoints[-CHECKPOINT_LIMIT:]})

def _public(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in checkpoint.items() if key != "content"}

def _workspace_path(config: dict[str, Any]) -> str:
    workspace = configured_workspace(config)
    return str(workspace.resolve()) if workspace is not None else ""

def create_before_save_checkpoint(config: dict[str, Any], relative_path: str) -> dict[str, Any]:
    current = read_workspace_file(config, relative_path)
    if not current.get("ok"):
        return current
    checkpoint = {
        "id": f"checkpoint-{uuid.uuid4().hex}",
        "workspace": _workspace_path(config),
        "path": str(current["path"]),
        "content": str(current.get("content") or ""),
        "before_sha256": _hash(str(current.get("content") or "")),
        "created_at": now(),
        "state": "pending",
    }
    with CHECKPOINT_LOCK:
        checkpoints = _load()
        checkpoints.append(checkpoint)
        _store(checkpoints)
    return {"ok": True, "checkpoint": _public(checkpoint)}

def mark_checkpoint_saved(checkpoint_id: str, saved_content: str) -> dict[str, Any]:
    with CHECKPOINT_LOCK:
        checkpoints = _load()
        checkpoint = next((item for item in reversed(checkpoints) if item.get("id") == checkpoint_id), None)
        if checkpoint is None:
            return {"ok": False, "error": "Talos checkpoint was not found."}
        checkpoint["state"] = "saved"
        checkpoint["saved_at"] = now()
        checkpoint["saved_sha256"] = _hash(saved_content)
        _store(checkpoints)
    return {"ok": True, "checkpoint": _public(checkpoint)}

def discard_checkpoint(checkpoint_id: str) -> None:
    with CHECKPOINT_LOCK:
        checkpoints = [item for item in _load() if item.get("id") != checkpoint_id]
        _store(checkpoints)

def latest_saved_checkpoint(config: dict[str, Any], relative_path: str) -> dict[str, Any]:
    current = read_workspace_file(config, relative_path)
    if not current.get("ok"):
        return current
    workspace = _workspace_path(config)
    path = str(current["path"])
    with CHECKPOINT_LOCK:
        checkpoint = next(
            (
                item for item in reversed(_load())
                if item.get("workspace") == workspace
                and item.get("path") == path
                and item.get("state") == "saved"
            ),
            None,
        )
    return {"ok": True, "checkpoint": _public(checkpoint) if checkpoint else None}

def rollback_last_checkpoint(config: dict[str, Any], relative_path: str) -> dict[str, Any]:
    latest = latest_saved_checkpoint(config, relative_path)
    if not latest.get("ok"):
        return latest
    checkpoint = latest.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return {"ok": False, "error": "No saved Talos checkpoint is available for this file."}
    current = read_workspace_file(config, relative_path)
    if not current.get("ok"):
        return current
    if _hash(str(current.get("content") or "")) != str(checkpoint.get("saved_sha256") or ""):
        return {"ok": False, "error": "Rollback blocked: the Arduino file changed after Talos saved it."}
    with CHECKPOINT_LOCK:
        stored = next((item for item in reversed(_load()) if item.get("id") == checkpoint.get("id")), None)
        if stored is None:
            return {"ok": False, "error": "Talos checkpoint was not found."}
        content = str(stored.get("content") or "")
    result = write_workspace_file(config, relative_path, content)
    if not result.get("ok"):
        return result
    with CHECKPOINT_LOCK:
        checkpoints = _load()
        stored = next((item for item in reversed(checkpoints) if item.get("id") == checkpoint.get("id")), None)
        if stored is not None:
            stored["state"] = "rolled-back"
            stored["rolled_back_at"] = now()
            _store(checkpoints)
    return {"ok": True, "checkpoint": checkpoint, **result}
