from __future__ import annotations

import hashlib
import json
import queue
import shutil
import subprocess
import threading
import time
from difflib import unified_diff
from pathlib import Path
from typing import Any

from talos.core import ROOT, load_app_identity, now
from talos.arduino import is_source_file

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PATCH_IGNORED_DIRS = {".git", ".talos_sandbox", "__pycache__", "build", "dist"}
PATCH_FILE_LIMIT = 2_000_000
STAGING_ROOT = ROOT / ".talos_staging"
THREAD_SANDBOX_MODE = "workspace-write"
CODEX_TURN_TIMEOUT_SECONDS = 300
CODEX_THREAD_REFRESH_SECONDS = 15

def _clean_thread_text(text: str) -> str:
    value = str(text or "").strip()
    marker = "\n\nTalos Arduino context:"
    if marker in value:
        value = value.split(marker, 1)[0].strip()
    return value

def normalize_codex_thread(thread: dict[str, Any], active_id: str = "") -> dict[str, Any]:
    preview = _clean_thread_text(str(thread.get("preview") or ""))
    title = str(thread.get("name") or "").strip() or preview.splitlines()[0].strip()
    if len(title) > 72:
        title = f"{title[:69].rstrip()}..."
    return {
        "id": str(thread.get("id") or ""),
        "title": title or "Untitled conversation",
        "preview": preview,
        "workspace": str(thread.get("cwd") or ""),
        "created_at": thread.get("createdAt") or 0,
        "updated_at": thread.get("updatedAt") or 0,
        "source": thread.get("source") or "",
        "active": str(thread.get("id") or "") == active_id,
    }

def messages_from_codex_thread(thread: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for turn in thread.get("turns") or []:
        turn_time = turn.get("startedAt") or turn.get("completedAt") or ""
        for item in turn.get("items") or []:
            item_type = str(item.get("type") or "")
            if item_type == "userMessage":
                text = "\n".join(
                    str(content.get("text") or "")
                    for content in item.get("content") or []
                    if content.get("type") == "text" and content.get("text")
                )
                role = "user"
            elif item_type == "agentMessage":
                text = str(item.get("text") or "")
                role = "assistant"
            else:
                continue
            text = _clean_thread_text(text)
            if text:
                messages.append(
                    {
                        "id": str(item.get("id") or f"{role}-{len(messages) + 1}"),
                        "role": role,
                        "text": text,
                        "time": turn_time,
                    }
                )
    return messages[-100:]

def find_codex_executable() -> str:
    executable = shutil.which("codex")
    if executable:
        return executable
    extension_root = Path.home() / ".vscode" / "extensions"
    candidates = sorted(
        extension_root.glob("openai.chatgpt-*/bin/windows-x86_64/codex.exe"),
        reverse=True,
    )
    return str(candidates[0]) if candidates else ""

def build_codex_prompt(
    message: str,
    workspace: dict[str, Any] | None = None,
    active_file: dict[str, Any] | None = None,
    verify_context: str = "",
    allow_edits: bool = True,
) -> str:
    workspace = workspace or {}
    active_file = active_file or {}
    sections = [message.strip()]
    context = [
        "Talos Arduino context:",
        f"- Workspace: {workspace.get('path') or 'not selected'}",
        f"- Main sketch: {workspace.get('main_sketch') or 'unknown'}",
        f"- Board FQBN: {workspace.get('fqbn') or 'not detected'}",
    ]
    if active_file.get("path"):
        context.append(f"- Active file: {active_file['path']}")
    sections.append("\n".join(context))
    content = str(active_file.get("content") or "")
    if content:
        sections.append(
            "Current active file content:\n"
            f"```cpp\n{content}\n```"
        )
    if verify_context.strip():
        sections.append(f"Latest sandbox verify context:\n{verify_context.strip()}")
    if allow_edits:
        sections.append(
            "You may edit relevant files inside the Talos staging copy of the selected Arduino workspace. "
            "Talos will show the resulting diff for review before applying it to the real Arduino sketch. "
            "Keep changes focused, preserve unrelated code, and summarize every changed file."
        )
    else:
        sections.append(
            "This turn is read-only. Inspect the selected Arduino workspace, but do not modify files."
        )
    return "\n\n".join(section for section in sections if section)

def snapshot_workspace(workspace: str | Path) -> dict[str, dict[str, Any]]:
    root = Path(workspace).resolve()
    snapshot: dict[str, dict[str, Any]] = {}
    if not root.exists() or not root.is_dir():
        return snapshot
    try:
        paths = root.rglob("*")
        for path in paths:
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if any(part in PATCH_IGNORED_DIRS or part.startswith(".talos_") for part in relative.parts[:-1]):
                continue
            try:
                size = path.stat().st_size
                if size > PATCH_FILE_LIMIT:
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            snapshot[relative.as_posix()] = {"sha256": digest, "bytes": size}
    except OSError:
        return snapshot
    return snapshot

def diff_workspace_snapshots(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for path in sorted(set(before) | set(after), key=str.lower):
        old = before.get(path)
        new = after.get(path)
        if old is None and new is not None:
            kind = "add"
        elif old is not None and new is None:
            kind = "delete"
        elif old != new:
            kind = "update"
        else:
            continue
        changes.append(
            {
                "path": path,
                "kind": kind,
                "before_bytes": int((old or {}).get("bytes") or 0),
                "after_bytes": int((new or {}).get("bytes") or 0),
            }
        )
    return changes

def prepare_staging_workspace(source_workspace: str | Path) -> Path:
    source = Path(source_workspace).resolve()
    if not source.is_dir():
        raise RuntimeError("The selected Arduino workspace is not available for staging.")
    key = hashlib.sha256(str(source).lower().encode("utf-8")).hexdigest()[:16]
    staging = STAGING_ROOT / key
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(
        source,
        staging,
        ignore=shutil.ignore_patterns(*PATCH_IGNORED_DIRS, ".talos_*"),
    )
    return staging.resolve()

def staged_patch_files(
    source_workspace: str | Path,
    staging_workspace: str | Path,
    changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source = Path(source_workspace).resolve()
    staging = Path(staging_workspace).resolve()
    files: list[dict[str, Any]] = []
    for change in changes:
        relative = str(change.get("path") or "").replace("\\", "/")
        source_path = (source / relative).resolve()
        staging_path = (staging / relative).resolve()
        try:
            source_path.relative_to(source)
            staging_path.relative_to(staging)
        except ValueError:
            continue
        if not is_source_file(staging_path if staging_path.exists() else source_path):
            continue
        before = source_path.read_text(encoding="utf-8", errors="replace") if source_path.exists() else ""
        after = staging_path.read_text(encoding="utf-8", errors="replace") if staging_path.exists() else ""
        kind = str(change.get("kind") or "update")
        files.append(
            {
                **change,
                "path": relative,
                "kind": kind,
                "diff": "".join(unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile=relative,
                    tofile=relative,
                )),
                "review_status": "staged",
                **({"content": after} if kind != "delete" else {}),
            }
        )
    return files

class CodexBridge:
    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._lock = threading.RLock()
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._request_id = 0
        self._messages: list[dict[str, Any]] = []
        self._activity: list[str] = []
        self._account: dict[str, Any] | None = None
        self._error = ""
        self._turn_error = ""
        self._starting = False
        self._initialized = threading.Event()
        self._thread_id = ""
        self._thread_cwd = ""
        self._turn_running = False
        self._turn_id = ""
        self._assistant_message_id = ""
        self._patches: list[dict[str, Any]] = []
        self._patch_revision = 0
        self._patch_event_revision = 0
        self._turn_workspace = ""
        self._turn_staging_workspace = ""
        self._turn_track_changes = False
        self._turn_snapshot: dict[str, dict[str, Any]] = {}
        self._turn_protocol_changes: dict[str, dict[str, Any]] = {}
        self._turn_protocol_revision = 0
        self._turn_diff = ""
        self._remote_threads: list[dict[str, Any]] = []
        self._remote_threads_updated_at = 0.0
        self._remote_threads_loading = False

    def status(self, start: bool = True) -> dict[str, Any]:
        if start:
            self.start_async()
        self._schedule_thread_refresh()
        with self._lock:
            process_running = self._process is not None and self._process.poll() is None
            return {
                "ok": process_running and self._initialized.is_set() and bool(self._account) and not self._error,
                "available": bool(find_codex_executable()),
                "connected": process_running,
                "initializing": self._starting,
                "busy": self._turn_running,
                "thread_id": self._thread_id,
                "thread_cwd": self._thread_cwd,
                "account": self._account,
                "error": self._error or self._turn_error,
                "messages": list(self._messages[-100:]),
                "activity": list(self._activity[-40:]),
                "patches": list(self._patches[-20:]),
                "patch_revision": self._patch_revision,
                "patch_event_revision": self._patch_event_revision,
                "pending_patch": {
                    "workspace": self._turn_workspace,
                    "files": list(self._turn_protocol_changes.values()),
                    "diff": self._turn_diff,
                    "revision": self._turn_protocol_revision,
                } if self._turn_running and self._turn_protocol_changes else None,
                "conversations": self._thread_summaries(),
            }

    def start_async(self) -> None:
        with self._lock:
            process_running = self._process is not None and self._process.poll() is None
            if self._initialized.is_set() and process_running:
                return
            if self._starting:
                return
            self._starting = True
            self._error = ""
        threading.Thread(target=self._start_worker, daemon=True).start()

    def ensure_started(self, timeout: float = 20) -> None:
        self.start_async()
        if not self._initialized.wait(timeout):
            with self._lock:
                error = self._error
            raise RuntimeError(error or "Codex app-server initialization timed out.")
        with self._lock:
            if self._error:
                raise RuntimeError(self._error)

    def _start_worker(self) -> None:
        try:
            app_identity = load_app_identity()
            self._start_process()
            self._request(
                "initialize",
                {
                    "clientInfo": {
                        "name": app_identity["app_name"].lower().replace(" ", "-"),
                        "title": app_identity["display_name"],
                        "version": app_identity["version"],
                    }
                },
            )
            self._notify("initialized", {})
            account_result = self._request("account/read", {"refreshToken": False})
            with self._lock:
                self._account = account_result.get("account")
                if account_result.get("requiresOpenaiAuth") and not self._account:
                    self._error = "Codex is not signed in. Sign in through the Codex extension or CLI first."
                self._append_activity("Codex app-server connected.")
            try:
                self._refresh_threads()
            except Exception as error:
                with self._lock:
                    self._append_activity(f"Codex history refresh warning: {error}")
        except Exception as error:
            with self._lock:
                self._error = str(error)
                self._append_activity(f"Codex startup failed: {error}")
        finally:
            with self._lock:
                self._starting = False
                self._initialized.set()

    def _start_process(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return
            executable = find_codex_executable()
            if not executable:
                raise RuntimeError("Codex CLI was not found. Install or enable the OpenAI Codex extension.")
            self._error = ""
            self._initialized.clear()
            self._process = subprocess.Popen(
                [executable, "app-server", "--stdio"],
                cwd=str(ROOT),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
            self._reader = threading.Thread(target=self._read_stdout, daemon=True)
            self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
            self._reader.start()
            self._stderr_reader.start()

    def send_message(
        self,
        message: str,
        workspace: dict[str, Any],
        active_file: dict[str, Any] | None = None,
        verify_context: str = "",
        allow_edits: bool = True,
    ) -> dict[str, Any]:
        text = message.strip()
        if not text:
            return {"ok": False, "error": "Message is empty."}
        self.ensure_started()
        with self._lock:
            if self._turn_running:
                return {"ok": False, "error": "Codex is still working on the previous request."}
            if not self._account:
                return {"ok": False, "error": self._error or "Codex is not signed in."}
            workspace_path = str(Path(workspace.get("path") or ROOT).resolve())
            staging_path = str(prepare_staging_workspace(workspace_path)) if allow_edits else workspace_path
            self._messages.append(
                {
                    "id": f"user-{len(self._messages) + 1}",
                    "role": "user",
                    "text": text,
                    "time": now(),
                }
            )
            self._turn_running = True
            self._error = ""
            self._turn_error = ""
            self._turn_workspace = workspace_path
            self._turn_staging_workspace = staging_path
            self._turn_track_changes = allow_edits
            self._turn_snapshot = snapshot_workspace(staging_path) if allow_edits else {}
            self._turn_protocol_changes = {}
            self._turn_protocol_revision = 0
            self._turn_diff = ""
        prompt = build_codex_prompt(
            text,
            workspace,
            active_file,
            verify_context,
            allow_edits,
        )
        threading.Thread(
            target=self._run_turn,
            args=(prompt, staging_path, allow_edits),
            daemon=True,
        ).start()
        return {"ok": True, "status": "started"}

    def apply_patch(self, patch_id: str, workspace: str, relative_path: str) -> dict[str, Any]:
        with self._lock:
            patch = next((item for item in self._patches if item.get("id") == patch_id), None)
            if patch is None:
                return {"ok": False, "error": "Codex patch was not found."}
            if Path(str(patch.get("workspace") or "")).resolve() != Path(workspace).resolve():
                return {"ok": False, "error": "Codex patch belongs to a different workspace."}
            target = str(relative_path or "").replace("\\", "/")
            file = next((item for item in patch.get("files") or [] if item.get("path") == target), None)
            if file is None:
                return {"ok": False, "error": "Codex patch does not change the selected file."}
            if file.get("review_status", "staged") not in {"staged", "reviewing"}:
                return {"ok": False, "error": "This Codex change is no longer available for review."}
            if file.get("kind") == "delete":
                return {"ok": False, "error": "Deleting a file from the editor is not supported yet."}
            file["review_status"] = "applied-to-editor"
            self._sync_patch_status(patch)
            self._append_activity(f"Applied Codex change to Talos editor: {target}.")
            return {"ok": True, "patch": dict(patch), "file": dict(file)}

    def review_patch(self, patch_id: str, workspace: str, relative_path: str) -> dict[str, Any]:
        with self._lock:
            patch = next((item for item in self._patches if item.get("id") == patch_id), None)
            if patch is None:
                return {"ok": False, "error": "Codex patch was not found."}
            if Path(str(patch.get("workspace") or "")).resolve() != Path(workspace).resolve():
                return {"ok": False, "error": "Codex patch belongs to a different workspace."}
            target = str(relative_path or "").replace("\\", "/")
            file = next((item for item in patch.get("files") or [] if item.get("path") == target), None)
            if file is None:
                return {"ok": False, "error": "Codex patch does not change the selected file."}
            if file.get("review_status") == "staged":
                file["review_status"] = "reviewing"
                self._sync_patch_status(patch)
                self._append_activity(f"Reviewing staged Codex change: {target}.")
            return {"ok": True, "patch": dict(patch), "file": dict(file)}

    def mark_patch_saved(self, workspace: str, relative_path: str) -> dict[str, Any]:
        with self._lock:
            target = str(relative_path or "").replace("\\", "/")
            for patch in reversed(self._patches):
                if Path(str(patch.get("workspace") or "")).resolve() != Path(workspace).resolve():
                    continue
                file = next((item for item in patch.get("files") or [] if item.get("path") == target), None)
                if file is None or file.get("review_status") != "applied-to-editor":
                    continue
                file["review_status"] = "saved"
                self._sync_patch_status(patch)
                self._append_activity(f"Saved Codex change to Arduino workspace: {target}.")
                return {"ok": True, "saved": True, "patch": dict(patch), "file": dict(file)}
            return {"ok": True, "saved": False}

    def reject_patch(self, patch_id: str, relative_path: str) -> dict[str, Any]:
        with self._lock:
            patch = next((item for item in self._patches if item.get("id") == patch_id), None)
            if patch is None:
                return {"ok": False, "error": "Codex patch was not found."}
            target = str(relative_path or "").replace("\\", "/")
            file = next((item for item in patch.get("files") or [] if item.get("path") == target), None)
            if file is None or file.get("review_status", "staged") not in {"staged", "reviewing"}:
                return {"ok": False, "error": "This file is no longer pending review."}
            file["review_status"] = "rejected"
            self._sync_patch_status(patch)
            self._append_activity(f"Rejected Codex change: {target}.")
            return {"ok": True, "patch": dict(patch), "file": dict(file)}

    @staticmethod
    def _sync_patch_status(patch: dict[str, Any]) -> None:
        statuses = {str(file.get("review_status") or "staged") for file in patch.get("files") or []}
        if "conflict" in statuses:
            patch["review_status"] = "conflict"
        elif statuses & {"staged", "reviewing"}:
            patch["review_status"] = "reviewing"
        elif "applied-to-editor" in statuses:
            patch["review_status"] = "applied-to-editor"
        elif statuses == {"saved"}:
            patch["review_status"] = "saved"
        elif statuses == {"rejected"}:
            patch["review_status"] = "rejected"
        else:
            patch["review_status"] = "staged"

    def new_thread(self) -> dict[str, Any]:
        with self._lock:
            if self._turn_running:
                return {"ok": False, "error": "Wait for the active Codex turn to finish."}
            self._thread_id = ""
            self._thread_cwd = ""
            self._assistant_message_id = ""
            self._turn_id = ""
            self._turn_error = ""
            self._messages.clear()
            self._activity.clear()
            self._patches.clear()
            self._patch_revision += 1
            self._append_activity("Ready for a new Codex thread.")
        return {"ok": True}

    def select_conversation(self, conversation_id: str) -> dict[str, Any]:
        selected_id = conversation_id.strip()
        if not selected_id:
            return {"ok": False, "error": "Conversation id is empty."}
        self.ensure_started()
        with self._lock:
            if self._turn_running:
                return {"ok": False, "error": "Wait for the active Codex turn to finish."}
        try:
            result = self._request(
                "thread/resume",
                {
                    "threadId": selected_id,
                    "approvalPolicy": "never",
                    "sandbox": THREAD_SANDBOX_MODE,
                },
                timeout=30,
            )
        except Exception as error:
            return {"ok": False, "error": f"Could not load Codex conversation: {error}"}
        thread = result.get("thread") or {}
        if not thread.get("id"):
            return {"ok": False, "error": "Codex returned an empty conversation."}
        messages = messages_from_codex_thread(thread)
        with self._lock:
            self._thread_id = selected_id
            self._thread_cwd = str(thread.get("cwd") or "")
            self._assistant_message_id = ""
            self._turn_id = ""
            self._turn_error = ""
            self._messages = messages
            self._patches = []
            self._patch_revision += 1
            self._activity.clear()
            self._append_activity("Loaded Codex conversation history.")
            self._remote_threads = [
                normalize_codex_thread(item, selected_id)
                for item in self._remote_threads
            ]
        return {"ok": True}

    def shutdown(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            self._turn_running = False
            self._turn_id = ""
            self._starting = False
            self._initialized.clear()
            self._pending.clear()
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()

    def _run_turn(self, prompt: str, cwd: str, allow_edits: bool) -> None:
        try:
            with self._lock:
                thread_id = self._thread_id
                thread_cwd = self._thread_cwd
            if not thread_id or Path(thread_cwd) != Path(cwd):
                result = self._request(
                    "thread/start",
                    {
                        "cwd": cwd,
                        "approvalPolicy": "never",
                        "sandbox": THREAD_SANDBOX_MODE,
                        "serviceName": "talos",
                    },
                    timeout=30,
                )
                thread_id = str(result.get("thread", {}).get("id") or "")
                if not thread_id:
                    raise RuntimeError("Codex did not return a thread id.")
                with self._lock:
                    self._thread_id = thread_id
                    self._thread_cwd = cwd
                    self._append_activity(f"Thread ready for {cwd}.")
            result = self._request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "cwd": cwd,
                    "approvalPolicy": "never",
                    "sandboxPolicy": {
                        "type": "workspaceWrite" if allow_edits else "readOnly",
                        **({"writableRoots": [cwd]} if allow_edits else {}),
                        "networkAccess": False,
                    },
                },
                timeout=30,
            )
            turn_id = str(result.get("turn", {}).get("id") or "")
            with self._lock:
                self._turn_id = turn_id
            if turn_id:
                threading.Thread(
                    target=self._watch_turn_timeout,
                    args=(thread_id, turn_id),
                    daemon=True,
                ).start()
        except Exception as error:
            with self._lock:
                self._turn_error = str(error)
                self._turn_running = False
                self._turn_id = ""
                self._clear_turn_patch_state()
                self._append_activity(f"Codex error: {error}")

    def cancel_turn(self, reason: str = "Codex turn cancelled.") -> dict[str, Any]:
        with self._lock:
            if not self._turn_running:
                return {"ok": False, "error": "No Codex turn is running."}
            thread_id = self._thread_id
            turn_id = self._turn_id
        if thread_id and turn_id:
            try:
                self._request(
                    "turn/interrupt",
                    {"threadId": thread_id, "turnId": turn_id},
                    timeout=10,
                )
            except Exception as error:
                with self._lock:
                    self._append_activity(f"Codex interrupt warning: {error}")
        with self._lock:
            if self._turn_id == turn_id:
                self._turn_running = False
                self._turn_id = ""
                self._turn_error = reason
                self._clear_turn_patch_state()
                self._append_activity(reason)
        return {"ok": True}

    def _watch_turn_timeout(self, thread_id: str, turn_id: str) -> None:
        threading.Event().wait(CODEX_TURN_TIMEOUT_SECONDS)
        with self._lock:
            still_running = (
                self._turn_running
                and self._thread_id == thread_id
                and self._turn_id == turn_id
            )
        if still_running:
            self.cancel_turn(
                f"Codex turn timed out after {CODEX_TURN_TIMEOUT_SECONDS // 60} minutes."
            )

    def _request(self, method: str, params: dict[str, Any], timeout: float = 15) -> dict[str, Any]:
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            self._pending[request_id] = response_queue
            self._write({"method": method, "id": request_id, "params": params})
        try:
            response = response_queue.get(timeout=timeout)
        except queue.Empty as error:
            with self._lock:
                self._pending.pop(request_id, None)
            raise RuntimeError(f"Codex request timed out: {method}") from error
        if "error" in response:
            detail = response.get("error", {})
            raise RuntimeError(str(detail.get("message") or detail))
        return response.get("result", {})

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        with self._lock:
            self._write({"method": method, "params": params})

    def _write(self, message: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None or self._process.poll() is not None:
            raise RuntimeError("Codex app-server is not running.")
        self._process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self._process.stdin.flush()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_id = message.get("id")
            if request_id is not None and ("result" in message or "error" in message):
                with self._lock:
                    response_queue = self._pending.pop(int(request_id), None)
                if response_queue:
                    response_queue.put(message)
                continue
            if request_id is not None and message.get("method"):
                self._handle_server_request(message)
                continue
            self._handle_notification(message)
        with self._lock:
            if self._process is process and process.poll() is not None:
                self._error = f"Codex app-server exited with code {process.returncode}."
                self._turn_running = False
                pending = list(self._pending.values())
                self._pending.clear()
            else:
                pending = []
        for response_queue in pending:
            response_queue.put(
                {
                    "error": {
                        "message": self._error or "Codex app-server stopped unexpectedly."
                    }
                }
            )

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            text = line.strip()
            if text and not text.startswith("WARNING:"):
                with self._lock:
                    self._append_activity(text)

    def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        decision: dict[str, Any] = {"decision": "decline"}
        if "approval" not in method.lower():
            decision = {}
        with self._lock:
            self._write({"id": message["id"], "result": decision})
            self._append_activity(f"Handled Codex request: {method}.")

    def _handle_notification(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        with self._lock:
            if method == "item/agentMessage/delta":
                self._append_assistant_delta(str(params.get("delta") or ""))
            elif method == "item/completed":
                self._complete_item(params.get("item") or {})
            elif method == "item/started":
                self._started_item(params.get("item") or {})
            elif method == "item/fileChange/patchUpdated":
                self._capture_protocol_changes(params.get("changes") or [])
            elif method == "turn/diff/updated":
                self._turn_diff = str(params.get("diff") or "")
                self._turn_protocol_revision += 1
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                self._finalize_turn_patch()
                self._turn_running = False
                self._turn_id = ""
                self._assistant_message_id = ""
                status = str(turn.get("status") or "completed")
                self._append_activity(f"Codex turn {status}.")
                error = turn.get("error") or {}
                if error:
                    self._turn_error = str(error.get("message") or error)
                self._schedule_thread_refresh(force=True)
            elif method == "error":
                error = params.get("error") or {}
                message_text = str(error.get("message") or error or "Unknown Codex error.")
                if self._turn_running:
                    self._turn_error = message_text
                    self._turn_running = False
                    self._turn_id = ""
                    self._clear_turn_patch_state()
                else:
                    self._error = message_text
                self._append_activity(f"Codex error: {message_text}")
            elif method == "account/updated":
                self._append_activity("Codex account state changed.")

    def _append_assistant_delta(self, delta: str) -> None:
        if not delta:
            return
        if not self._assistant_message_id:
            self._assistant_message_id = f"assistant-{len(self._messages) + 1}"
            self._messages.append(
                {
                    "id": self._assistant_message_id,
                    "role": "assistant",
                    "text": "",
                    "time": now(),
                }
            )
        for message in reversed(self._messages):
            if message.get("id") == self._assistant_message_id:
                message["text"] = str(message.get("text") or "") + delta
                break

    def _started_item(self, item: dict[str, Any]) -> None:
        item_type = str(item.get("type") or "")
        if item_type == "commandExecution":
            self._append_activity(f"Running: {item.get('command') or 'command'}")
        elif item_type == "fileChange":
            self._append_activity("Preparing file changes.")

    def _complete_item(self, item: dict[str, Any]) -> None:
        item_type = str(item.get("type") or "")
        if item_type == "agentMessage":
            text = str(item.get("text") or "")
            if text:
                if self._assistant_message_id:
                    for message in reversed(self._messages):
                        if message.get("id") == self._assistant_message_id:
                            message["text"] = text
                            break
                else:
                    self._messages.append(
                        {
                            "id": f"assistant-{len(self._messages) + 1}",
                            "role": "assistant",
                            "text": text,
                            "time": now(),
                        }
                    )
        elif item_type == "commandExecution":
            command = item.get("command") or "command"
            self._append_activity(f"Completed: {command}")
        elif item_type == "fileChange":
            changes = item.get("changes") or []
            self._capture_protocol_changes(changes)
            self._append_activity(f"Applied {len(changes)} file change(s).")

    def _capture_protocol_changes(self, changes: list[dict[str, Any]]) -> None:
        workspace = Path(self._turn_staging_workspace).resolve() if self._turn_staging_workspace else None
        if workspace is None:
            return
        for change in changes:
            raw_path = str(change.get("path") or "").strip()
            if not raw_path:
                continue
            try:
                path = Path(raw_path)
                resolved = path.resolve() if path.is_absolute() else (workspace / path).resolve()
                relative = resolved.relative_to(workspace).as_posix()
            except (OSError, ValueError):
                self._append_activity(f"Ignored out-of-workspace change: {raw_path}")
                continue
            kind_value = change.get("kind") or {}
            kind = str(kind_value.get("type") if isinstance(kind_value, dict) else kind_value or "update")
            self._turn_protocol_changes[relative] = {
                "path": relative,
                "kind": kind,
                "diff": str(change.get("diff") or ""),
            }
            self._turn_protocol_revision += 1

    def _finalize_turn_patch(self) -> None:
        workspace = self._turn_workspace
        staging_workspace = self._turn_staging_workspace
        before = self._turn_snapshot
        if not workspace or not staging_workspace or not self._turn_track_changes:
            self._clear_turn_patch_state()
            return
        after = snapshot_workspace(staging_workspace)
        snapshot_changes = diff_workspace_snapshots(before, after)
        merged: dict[str, dict[str, Any]] = {
            change["path"]: dict(change)
            for change in snapshot_changes
        }
        for path, change in self._turn_protocol_changes.items():
            merged.setdefault(path, {}).update(change)
        files = staged_patch_files(workspace, staging_workspace, [merged[path] for path in sorted(merged, key=str.lower)])
        if files:
            self._patch_revision += 1
            self._patch_event_revision += 1
            patch = {
                "id": f"patch-{self._patch_revision}",
                "time": now(),
                "workspace": workspace,
                "staging_workspace": staging_workspace,
                "files": files,
                "diff": self._turn_diff,
                "event_revision": self._patch_event_revision,
                "review_status": "staged",
            }
            self._patches.append(patch)
            del self._patches[:-20]
            self._append_activity(f"Prepared Codex patch review across {len(files)} file(s).")
        self._clear_turn_patch_state()

    def _clear_turn_patch_state(self) -> None:
        self._turn_workspace = ""
        self._turn_staging_workspace = ""
        self._turn_track_changes = False
        self._turn_snapshot = {}
        self._turn_protocol_changes = {}
        self._turn_protocol_revision = 0
        self._turn_diff = ""

    def _append_activity(self, text: str) -> None:
        self._activity.append(f"{now()} {text}")
        del self._activity[:-100]

    def _schedule_thread_refresh(self, force: bool = False) -> None:
        with self._lock:
            process_running = self._process is not None and self._process.poll() is None
            stale = time.monotonic() - self._remote_threads_updated_at >= CODEX_THREAD_REFRESH_SECONDS
            if (
                not process_running
                or not self._initialized.is_set()
                or self._remote_threads_loading
                or (not force and not stale)
            ):
                return
            self._remote_threads_loading = True
        threading.Thread(target=self._refresh_threads_worker, daemon=True).start()

    def _refresh_threads_worker(self) -> None:
        try:
            self._refresh_threads()
        except Exception as error:
            with self._lock:
                self._append_activity(f"Codex history refresh warning: {error}")
        finally:
            with self._lock:
                self._remote_threads_loading = False

    def _refresh_threads(self) -> None:
        result = self._request(
            "thread/list",
            {
                "limit": 50,
                "sortKey": "updated_at",
                "sortDirection": "desc",
                "archived": False,
            },
            timeout=30,
        )
        threads = result.get("data") or result.get("threads") or []
        with self._lock:
            self._remote_threads = [
                normalize_codex_thread(item, self._thread_id)
                for item in threads
                if isinstance(item, dict) and item.get("id")
            ]
            self._remote_threads_updated_at = time.monotonic()

    def _thread_summaries(self) -> list[dict[str, Any]]:
        return [
            {
                **item,
                "active": item.get("id") == self._thread_id,
            }
            for item in self._remote_threads
        ]

CODEX_BRIDGE = CodexBridge()
