from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from talos.core import ROOT, now

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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
    sections.append(
        "Work only inside the selected Arduino workspace. Use the available local tools "
        "to inspect or edit files when needed, and summarize any changes clearly."
    )
    return "\n\n".join(section for section in sections if section)


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
        self._starting = False
        self._initialized = threading.Event()
        self._thread_id = ""
        self._thread_cwd = ""
        self._turn_running = False
        self._assistant_message_id = ""

    def status(self, start: bool = True) -> dict[str, Any]:
        if start:
            self.start_async()
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
                "error": self._error,
                "messages": list(self._messages[-100:]),
                "activity": list(self._activity[-40:]),
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
            self._start_process()
            self._request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "talos",
                        "title": "Talos",
                        "version": "0.1.0",
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
            workspace_path = str(workspace.get("path") or ROOT)
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
        prompt = build_codex_prompt(text, workspace, active_file, verify_context)
        threading.Thread(
            target=self._run_turn,
            args=(prompt, workspace_path),
            daemon=True,
        ).start()
        return {"ok": True, "status": "started"}

    def new_thread(self) -> dict[str, Any]:
        with self._lock:
            if self._turn_running:
                return {"ok": False, "error": "Wait for the active Codex turn to finish."}
            self._thread_id = ""
            self._thread_cwd = ""
            self._assistant_message_id = ""
            self._messages.clear()
            self._activity.clear()
            self._append_activity("Started a new local conversation.")
        return {"ok": True}

    def shutdown(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            self._turn_running = False
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

    def _run_turn(self, prompt: str, cwd: str) -> None:
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
                        "sandbox": "workspaceWrite",
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
            self._request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "cwd": cwd,
                    "approvalPolicy": "never",
                    "sandboxPolicy": {
                        "type": "workspaceWrite",
                        "writableRoots": [cwd],
                        "networkAccess": False,
                    },
                },
                timeout=30,
            )
        except Exception as error:
            with self._lock:
                self._error = str(error)
                self._turn_running = False
                self._append_activity(f"Codex error: {error}")

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
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                self._turn_running = False
                self._assistant_message_id = ""
                status = str(turn.get("status") or "completed")
                self._append_activity(f"Codex turn {status}.")
                error = turn.get("error") or {}
                if error:
                    self._error = str(error.get("message") or error)
            elif method == "error":
                error = params.get("error") or {}
                self._error = str(error.get("message") or error or "Unknown Codex error.")
                self._append_activity(f"Codex error: {self._error}")
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
            self._append_activity(f"Applied {len(changes)} file change(s).")

    def _append_activity(self, text: str) -> None:
        self._activity.append(f"{now()} {text}")
        del self._activity[:-100]


CODEX_BRIDGE = CodexBridge()
