import json
import re
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from talos import checkpoints as checkpoint_store
from talos import core, native_bridge
from talos import run_history as run_history_store
from talos.arduino_events import ArduinoEventWatcher, is_arduino_window_title
from talos.arduino import (
    cached_compile_result,
    clear_arduino_compile_cache,
    compile_cache_key,
    boards_by_window_title,
    copy_workspace_to_sandbox,
    delete_workspace_file,
    discover_arduino_projects,
    environment_profile,
    extract_ino_names,
    format_compile_issue_context,
    parse_compile_output,
    read_workspace_file,
    run_arduino_compile,
    save_environment_profile,
    workspace_context,
    workspace_map,
    workspace_summary,
    write_workspace_file,
)
from talos.native_bridge import (
    extract_board_name,
    extract_fqbn,
    native_available,
    parse_process_rows_payload,
    parse_window_rows_payload,
)
from talos.codex_bridge import (
    CODEX_TURN_TIMEOUT_SECONDS,
    CodexBridge,
    THREAD_SANDBOX_MODE,
    build_patch_hunks,
    build_codex_prompt,
    diff_workspace_snapshots,
    messages_from_codex_thread,
    normalize_codex_thread,
    snapshot_workspace,
    staged_patch_files,
)
from talos.checkpoints import (
    create_before_save_checkpoint,
    latest_saved_checkpoint,
    mark_checkpoint_saved,
    rollback_last_checkpoint,
)
from talos.run_history import record_patch, record_patch_transition, record_verify, run_history
from talos.server import state_payload

class TalosArduinoTests(unittest.TestCase):
    def test_codex_prompt_contains_selected_arduino_context(self) -> None:
        prompt = build_codex_prompt(
            "Fix the compile error.",
            {
                "path": r"C:\Sketch\Blink",
                "main_sketch": "Blink.ino",
                "fqbn": "arduino:avr:uno",
                "map": {
                    "source_tab_count": 2,
                    "source_tabs": [{"path": "Blink.ino"}, {"path": "motor.cpp"}],
                    "diagnostics": {
                        "status": "passed",
                        "libraries": [{"name": "Wire", "version": "1.0.0"}],
                        "platforms": [{"name": "arduino:avr", "version": "1.8.6"}],
                    },
                },
            },
            {"path": "Blink.ino", "content": "void setup() {}\n"},
            "ERROR Blink.ino:2:1 - expected declaration",
        )

        self.assertIn("Fix the compile error.", prompt)
        self.assertIn(r"C:\Sketch\Blink", prompt)
        self.assertIn("arduino:avr:uno", prompt)
        self.assertIn("void setup()", prompt)
        self.assertIn("ERROR Blink.ino:2:1", prompt)
        self.assertIn("Talos staging copy", prompt)
        self.assertIn("Source tabs (2): Blink.ino, motor.cpp", prompt)
        self.assertIn("Verified libraries: Wire 1.0.0", prompt)

    def test_arduino_event_filter_and_debounce_only_signal_arduino_windows(self) -> None:
        signals: list[str] = []
        watcher = ArduinoEventWatcher(signals.append, debounce_seconds=60)

        watcher._signal("Untitled - Notepad")
        watcher._signal("Blink.ino | Arduino IDE 2.3.4")
        watcher._signal("Blink.ino | Arduino IDE 2.3.4")

        self.assertFalse(is_arduino_window_title("Untitled - Notepad"))
        self.assertTrue(is_arduino_window_title("Blink.ino | Arduino IDE 2.3.4"))
        self.assertEqual(signals, ["window"])

    def test_codex_prompt_includes_per_sketch_environment_profile(self) -> None:
        prompt = build_codex_prompt(
            "Review this sketch.",
            {
                "path": r"C:\Sketch\Blink",
                "map": {
                    "environment_profile": {
                        "serial_port": "COM7",
                        "baud_rate": 115200,
                        "build_flags": ["-DDEBUG"],
                        "libraries": ["Wire", "ArduinoJson"],
                    },
                },
            },
        )

        self.assertIn("Serial profile: COM7 @ 115200 baud", prompt)
        self.assertIn("Build flags: -DDEBUG", prompt)
        self.assertIn("Profile libraries: Wire, ArduinoJson", prompt)

    def test_codex_prompt_can_be_read_only(self) -> None:
        prompt = build_codex_prompt("Review only.", allow_edits=False)

        self.assertIn("This turn is read-only.", prompt)

    def test_codex_protocol_uses_legacy_thread_sandbox_enum(self) -> None:
        self.assertEqual(THREAD_SANDBOX_MODE, "workspace-write")
        self.assertEqual(CODEX_TURN_TIMEOUT_SECONDS, 300)

    def test_codex_workspace_snapshot_reports_added_updated_and_deleted_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "update.cpp").write_text("old\n", encoding="utf-8")
            (root / "delete.h").write_text("remove\n", encoding="utf-8")
            before = snapshot_workspace(root)
            (root / "update.cpp").write_text("new\n", encoding="utf-8")
            (root / "delete.h").unlink()
            (root / "add.ino").write_text("void setup() {}\n", encoding="utf-8")

            changes = diff_workspace_snapshots(before, snapshot_workspace(root))

            self.assertEqual(
                [(change["path"], change["kind"]) for change in changes],
                [("add.ino", "add"), ("delete.h", "delete"), ("update.cpp", "update")],
            )

    def test_codex_patch_tracking_supports_an_initially_empty_workspace(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            staging = Path(tmp) / "staging"
            root.mkdir()
            staging.mkdir()
            bridge = CodexBridge()
            bridge._turn_workspace = str(root)
            bridge._turn_staging_workspace = str(staging)
            bridge._turn_track_changes = True
            bridge._turn_snapshot = snapshot_workspace(staging)
            (staging / "created.ino").write_text("void setup() {}\n", encoding="utf-8")

            bridge._finalize_turn_patch()
            status = bridge.status(start=False)

            self.assertEqual(status["patch_revision"], 1)
            self.assertEqual(status["patch_event_revision"], 1)
            self.assertEqual(status["patches"][0]["files"][0]["path"], "created.ino")
            self.assertEqual(status["patches"][0]["files"][0]["kind"], "add")
            self.assertEqual(status["patches"][0]["files"][0]["review_status"], "staged")
            self.assertEqual(status["patches"][0]["review_status"], "staged")
            self.assertFalse((root / "created.ino").exists())

    def test_codex_thread_changes_do_not_emit_patch_events(self) -> None:
        bridge = CodexBridge()
        bridge.new_thread()

        status = bridge.status(start=False)
        self.assertEqual(status["patch_event_revision"], 0)

    def test_codex_review_patch_moves_one_file_to_editor_without_writing_workspace(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "Sketch.ino"
            target.write_text("old\n", encoding="utf-8")
            bridge = CodexBridge()
            bridge._patches.append(
                {
                    "id": "patch-1",
                    "workspace": str(root),
                    "review_status": "staged",
                    "files": [{"path": "Sketch.ino", "kind": "update", "content": "new\n", "review_status": "staged"}],
                }
            )

            reviewed = bridge.review_patch("patch-1", str(root), "Sketch.ino")
            self.assertTrue(reviewed["ok"])
            self.assertEqual(bridge._patches[0]["files"][0]["review_status"], "reviewing")

            result = bridge.apply_patch("patch-1", str(root), "Sketch.ino")

            self.assertTrue(result["ok"])
            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(result["file"]["content"], "new\n")
            self.assertEqual(bridge._patches[0]["files"][0]["review_status"], "applied-to-editor")
            self.assertEqual(bridge._patches[0]["review_status"], "applied-to-editor")

            saved = bridge.mark_patch_saved(str(root), "Sketch.ino")

            self.assertTrue(saved["ok"])
            self.assertTrue(saved["saved"])
            self.assertEqual(bridge._patches[0]["files"][0]["review_status"], "saved")
            self.assertEqual(bridge._patches[0]["review_status"], "saved")

    def test_codex_hunk_review_applies_only_the_selected_hunk(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = "first\nkeep\nsecond\n"
            after = "FIRST\nkeep\nSECOND\n"
            hunks = build_patch_hunks(before, after)
            self.assertEqual(len(hunks), 2)
            bridge = CodexBridge()
            bridge._patches.append(
                {
                    "id": "patch-hunks",
                    "workspace": str(root),
                    "review_status": "staged",
                    "files": [{
                        "path": "Sketch.ino",
                        "kind": "update",
                        "base_content": before,
                        "content": after,
                        "review_status": "staged",
                        "hunks": hunks,
                    }],
                }
            )

            result = bridge.apply_hunk("patch-hunks", str(root), "Sketch.ino", hunks[0]["id"])

            self.assertTrue(result["ok"])
            statuses = [hunk["review_status"] for hunk in bridge._patches[0]["files"][0]["hunks"]]
            self.assertEqual(statuses, ["applied-to-editor", "staged"])
            self.assertEqual(bridge._patches[0]["files"][0]["review_status"], "reviewing")

            applied = bridge.apply_all("patch-hunks", str(root))

            self.assertTrue(applied["ok"])
            self.assertEqual(applied["changed"], 1)
            file = bridge._patches[0]["files"][0]
            self.assertEqual(file["review_status"], "applied-to-editor")
            self.assertEqual(file["editor_content"], after)

    def test_external_workspace_change_marks_staged_codex_file_as_conflict(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            staging = Path(tmp) / "staging"
            root.mkdir()
            staging.mkdir()
            (root / "Sketch.ino").write_text("before\n", encoding="utf-8")
            (staging / "Sketch.ino").write_text("codex change\n", encoding="utf-8")
            files = staged_patch_files(root, staging, [{"path": "Sketch.ino", "kind": "update"}])
            bridge = CodexBridge()
            bridge._patches.append({
                "id": "patch-conflict",
                "workspace": str(root),
                "review_status": "staged",
                "files": files,
            })
            (root / "Sketch.ino").write_text("external edit\n", encoding="utf-8")

            status = bridge.status(start=False)

            self.assertEqual(status["patches"][0]["files"][0]["review_status"], "conflict")
            self.assertEqual(status["patches"][0]["review_status"], "conflict")
            self.assertEqual(status["patches"][0]["files"][0]["conflict_current_content"], "external edit\n")

            resolved = bridge.keep_external_conflict("patch-conflict", str(root), "Sketch.ino")

            self.assertTrue(resolved["ok"])
            self.assertEqual(resolved["file"]["review_status"], "rejected")
            self.assertEqual(resolved["file"]["conflict_resolution"], "kept-external")
            self.assertEqual((root / "Sketch.ino").read_text(encoding="utf-8"), "external edit\n")

    def test_staged_patch_sandbox_overrides_use_pending_codex_content_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Sketch"
            root.mkdir()
            bridge = CodexBridge()
            bridge._patches.append({
                "id": "patch-verify",
                "workspace": str(root),
                "review_status": "reviewing",
                "files": [
                    {"path": "Sketch.ino", "kind": "update", "content": "proposed\n", "review_status": "reviewing"},
                    {"path": "ignored.cpp", "kind": "update", "content": "ignored\n", "review_status": "rejected"},
                ],
            })

            result = bridge.staged_sandbox_overrides("patch-verify", str(root))

            self.assertTrue(result["ok"])
            self.assertEqual(result["overrides"], {"Sketch.ino": "proposed\n"})

    def test_frontend_contains_codex_workbench_panel(self) -> None:
        html = (Path(__file__).parents[1] / "ui" / "web_frontend" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="codexPanel"', html)
        self.assertIn('id="codexComposer"', html)
        self.assertIn('id="toggleCodexBtn"', html)
        self.assertIn('id="editInTalosBtn"', html)
        self.assertIn('id="editorModeBadge"', html)
        self.assertIn('id="boardInfoBtn"', html)
        self.assertIn('id="boardInfoPanel"', html)
        self.assertIn('id="environmentProfile"', html)
        self.assertIn('id="saveEnvironmentProfileBtn"', html)
        self.assertIn('id="editorLineNumbers"', html)
        self.assertIn("data-codex-prompt", html)
        self.assertIn('id="codexAllowEdits"', html)
        self.assertIn('id="cancelCodexBtn"', html)
        self.assertNotIn('id="virtualPatchToggleBtn"', html)
        self.assertIn('id="codexDiffPreview"', html)
        self.assertIn('id="codexContextPreview"', html)
        self.assertIn('id="codexContextPreviewText"', html)
        self.assertNotIn('id="codexHistoryBtn"', html)
        self.assertIn('id="codexBackBtn"', html)
        self.assertIn('id="codexHistoryCount"', html)
        self.assertIn('id="runHistoryTab"', html)
        self.assertIn('id="explorerSplitter"', html)
        self.assertIn('id="codexSplitter"', html)

        script = (Path(__file__).parents[1] / "ui" / "web_frontend" / "app.js").read_text(encoding="utf-8")
        self.assertIn("patch_event_revision", script)
        self.assertIn("Codex patch is ready for review.", script)
        self.assertIn("formatDuration", script)
        self.assertIn("timings.compile", script)
        self.assertIn("ACTIVE_FILE_POLL_MS", script)
        self.assertIn("checkActiveFileOnDisk", script)
        self.assertIn("pending_patch", script)
        self.assertIn("previewPendingCodexPatch", script)
        self.assertIn("applyUnifiedDiff", script)
        self.assertIn("Codex change review", script)
        self.assertIn("transientWorkspaceLoss", script)
        self.assertIn("activeFileByWorkspace", script)
        self.assertIn("applyCodexPatch", script)
        self.assertIn("rejectCodexPatch", script)
        self.assertIn("reviewCodexHunk", script)
        self.assertIn("contentWithAppliedHunks", script)
        self.assertIn("/api/codex_apply_hunk", script)
        self.assertIn("resolveCodexTurn", script)
        self.assertIn("/api/codex_apply_all", script)
        self.assertIn("Codex change conflict detected", script)
        self.assertIn("Save blocked: this file changed outside Talos", script)
        self.assertIn("setCodexConflictMode", script)
        self.assertIn("rollbackWorkspaceFile", script)
        self.assertIn("/api/arduino_rollback", script)
        self.assertIn("saveAndVerifyWorkspace", script)
        self.assertIn("verifySource", script)
        self.assertIn("patch-timeline", script)
        self.assertIn("verifyCodexPatch", script)
        self.assertIn("/api/codex_verify_patch", script)
        self.assertIn("keepExternalConflict", script)
        self.assertIn("buildCodexContextPreview", script)
        self.assertIn("renderCodexContextPreview", script)
        self.assertIn("/api/codex_keep_external", script)
        self.assertIn('id="codexConflictView"', html)
        self.assertIn('id="keepExternalConflictBtn"', html)
        self.assertIn('id="rollbackFileBtn"', html)
        self.assertIn('id="saveAndVerifyBtn"', html)
        self.assertIn('id="editorFileName" class="sr-only"', html)
        self.assertNotIn('id="editorMoreBtn"', html)
        self.assertIn('id="verifyCodexPatchBtn"', html)
        self.assertIn('id="applyCodexTurnBtn"', html)
        self.assertIn("localEditMode", script)
        self.assertIn("setLocalEditMode", script)
        self.assertIn("updateEditorAccess", script)
        self.assertIn("boardInfoText", script)
        self.assertIn("Arduino IDE owns the saved sketch", script)
        self.assertIn("codexDiffPreview", html)
        self.assertNotIn("virtualPatchEnabled", script)
        self.assertNotIn("toggleVirtualPatchMode", script)
        self.assertNotIn("virtualPatchStatus", script)
        self.assertIn("Codex change applied to Talos editor", script)
        self.assertIn('"Apply To Editor"', script)
        self.assertIn('addEventListener("click", () => applyCodexPatch())', script)
        self.assertIn("selectEditorLine", script)
        self.assertIn("lineFromGutterEvent", script)
        self.assertIn("setInterval(checkActiveFileOnDisk", script)
        self.assertIn("View all (", script)
        self.assertIn("relativeTimeLabel", script)
        self.assertIn("showCodexTasks(true)", script)
        self.assertNotIn("toggleCodexHistory", script)
        self.assertIn("bindExplorerSplitter", script)
        self.assertIn("bindCodexSplitter", script)
        self.assertIn("saveEnvironmentProfile", script)
        self.assertIn("/api/arduino_profile", script)
        self.assertIn("watchArduinoEvents", script)
        self.assertIn("/api/arduino_events", script)
        self.assertIn("renderActiveFileRow", script)
        self.assertIn("CODEX_WIDTH_KEY", script)
        self.assertNotIn("applyWindowMetrics", script)
        self.assertNotIn("--native-window-width", script)

        styles = (Path(__file__).parents[1] / "ui" / "web_frontend" / "styles.css").read_text(encoding="utf-8")
        self.assertNotIn("width: 100vw;", styles)
        self.assertIn("max-width: none;", styles)
        self.assertIn("justify-self: stretch;", styles)
        self.assertIn("inset: 0;", styles)
        self.assertIn("border-left: 1px solid var(--line);", styles)
        self.assertIn("minmax(280px, var(--codex-pane-width))", styles)
        self.assertIn("@media (max-width: 1240px)", styles)
        self.assertIn("minmax(260px, var(--codex-pane-width))", styles)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", styles)
        self.assertIn("grid-template-areas:", styles)
        self.assertIn("grid-column: 1 / -1;", styles)
        self.assertIn("body.native-window .app-chrome", styles)
        self.assertIn(".workspace-file-list tbody tr.active td", styles)
        self.assertIn(".sr-only", styles)

        desktop = (Path(__file__).parents[1] / "desktop_app.py").read_text(encoding="utf-8")
        self.assertIn("frameless=False", desktop)
        self.assertIn("display: none;", styles)
        self.assertIn("[hidden]", styles)
        self.assertIn("display: none !important;", styles)
        self.assertIn("cursor: pointer;", styles)
        self.assertIn("--codex-pane-width", styles)

        check_script = (Path(__file__).parents[1] / "scripts" / "check.ps1").read_text(encoding="utf-8")
        self.assertIn("build_native.ps1", check_script)
        self.assertIn("_HAS_NATIVE_WINDOW_ROWS", check_script)
        self.assertIn("_HAS_NATIVE_PROCESS_ROWS", check_script)
        self.assertIn("benchmark_native.py", check_script)
        self.assertIn("unittest tests.test_desktop_app", check_script)

        benchmark_script = (Path(__file__).parents[1] / "scripts" / "benchmark_native.py").read_text(encoding="utf-8")
        self.assertIn("list_window_rows", benchmark_script)
        self.assertIn("list_arduino_process_rows_native", benchmark_script)
        self.assertIn("fallback_status", benchmark_script)
        self.assertIn("list_arduino_tool_processes_wmic", benchmark_script)

        smoke_test = (Path(__file__).parents[1] / "docs" / "ARDUINO_SMOKE_TEST.md").read_text(encoding="utf-8")
        self.assertIn("Verify Sandbox", smoke_test)
        self.assertIn("Codex", smoke_test)
        self.assertIn("Pass Criteria", smoke_test)
        self.assertIn("Fail Conditions", smoke_test)
        self.assertIn("MVP Smoke-Test Matrix", smoke_test)
        self.assertIn("Hunk decision", smoke_test)
        self.assertIn("External-change conflict", smoke_test)
        self.assertIn("Save and verify", smoke_test)

    def test_pipeline_defines_exit_condition_for_every_stage(self) -> None:
        pipeline = (Path(__file__).parents[1] / "docs" / "TALOS_PIPELINE.md").read_text(encoding="utf-8")
        stages = re.split(r"(?=^## Stage \d+ - )", pipeline, flags=re.MULTILINE)
        stage_sections = [section for section in stages if section.startswith("## Stage ")]

        self.assertEqual(len(stage_sections), 11)
        for section in stage_sections:
            self.assertIn("Exit condition:", section, section.splitlines()[0])

    def test_codex_thread_summary_prefers_name_and_supports_unix_time(self) -> None:
        summary = normalize_codex_thread(
            {
                "id": "thread-1",
                "name": "Arduino review",
                "preview": "Long injected prompt",
                "cwd": r"C:\Sketch",
                "updatedAt": 1781541452,
            },
            "thread-1",
        )

        self.assertEqual(summary["title"], "Arduino review")
        self.assertEqual(summary["updated_at"], 1781541452)
        self.assertTrue(summary["active"])

    def test_codex_thread_messages_restore_user_and_assistant_text(self) -> None:
        messages = messages_from_codex_thread(
            {
                "turns": [
                    {
                        "startedAt": 1781541452,
                        "items": [
                            {
                                "id": "user-1",
                                "type": "userMessage",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Review this sketch.\n\nTalos Arduino context:\n- Workspace: C:\\Sketch",
                                    }
                                ],
                            },
                            {
                                "id": "assistant-1",
                                "type": "agentMessage",
                                "text": "The sketch compiles.",
                            },
                        ],
                    }
                ]
            }
        )

        self.assertEqual(messages[0]["text"], "Review this sketch.")
        self.assertEqual(messages[1]["text"], "The sketch compiles.")

    def test_run_history_keeps_verify_and_patch_events(self) -> None:
        with TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "run_history.json"
            with patch("talos.run_history.RUN_HISTORY_PATH", history_path):
                record_verify(
                    {
                        "ok": True,
                        "status": "passed",
                        "summary": {"path": r"C:\Sketch", "main_sketch": "Sketch.ino"},
                        "output": "Sketch uses 100 bytes.",
                    },
                    "codex_patch",
                )
                record_patch(
                    {
                        "id": "patch-1",
                        "workspace": r"C:\Sketch",
                        "files": [{"path": "Sketch.ino", "kind": "update"}],
                    }
                )
                record_patch_transition(
                    {
                        "id": "patch-1",
                        "workspace": r"C:\Sketch",
                        "review_status": "saved",
                        "files": [{"path": "Sketch.ino", "kind": "update", "review_status": "saved", "hunks": [{}, {}]}],
                    },
                    "saved",
                    "Sketch.ino",
                )
                events = run_history()

            self.assertEqual([event["type"] for event in events], ["patch", "verify"])
            self.assertEqual(events[1]["source"], "codex_patch")
            self.assertEqual(events[0]["files"][0]["hunks"], 2)
            self.assertEqual(events[0]["timeline"][-1]["action"], "saved")

    def test_codex_status_starts_runtime_without_blocking_for_handshake(self) -> None:
        bridge = CodexBridge()

        with patch.object(bridge, "start_async") as start_async:
            status = bridge.status()

        start_async.assert_called_once_with()
        self.assertFalse(status["connected"])
        self.assertFalse(status["ok"])

    def test_codex_start_respects_reconnect_cooldown(self) -> None:
        bridge = CodexBridge()
        bridge._next_retry_at = time.monotonic() + 60

        with patch("talos.codex_bridge.threading.Thread") as thread:
            bridge.start_async()
            bridge.start_async(force=True)

        self.assertEqual(thread.call_count, 1)

    def test_codex_reconnect_does_not_replay_interrupted_turn(self) -> None:
        bridge = CodexBridge()
        bridge._turn_running = True
        bridge._turn_id = "turn-1"
        bridge._turn_workspace = r"C:\Sketch"
        bridge._turn_protocol_changes = {"Sketch.ino": {"path": "Sketch.ino"}}

        with patch.object(bridge, "start_async") as start_async:
            result = bridge.reconnect()

        self.assertTrue(result["ok"])
        self.assertFalse(bridge._turn_running)
        self.assertEqual(bridge._turn_id, "")
        self.assertEqual(bridge._turn_protocol_changes, {})
        self.assertIn("not replayed", bridge._turn_error)
        start_async.assert_called_once_with(force=True)

    def test_board_mapping_uses_window_plugin_host_process_tree(self) -> None:
        windows = [
            {"pid": 101, "title": "first | Arduino IDE 2.3.4"},
            {"pid": 201, "title": "second | Arduino IDE 2.3.4"},
        ]
        processes = [
            {"name": "Arduino IDE.exe", "pid": 101, "created_at": 1000, "command_line": "--type=renderer"},
            {"name": "Arduino IDE.exe", "pid": 102, "created_at": 2000, "command_line": r"backend\plugin-host"},
            {"name": "arduino-language-server.exe", "pid": 103, "parent_pid": 102, "fqbn": "vendor:arch:first", "board_name": "First"},
            {"name": "Arduino IDE.exe", "pid": 201, "created_at": 20000, "command_line": "--type=renderer"},
            {"name": "Arduino IDE.exe", "pid": 202, "created_at": 21000, "command_line": r"backend\plugin-host"},
            {"name": "arduino-language-server.exe", "pid": 203, "parent_pid": 202, "fqbn": "vendor:arch:second", "board_name": "Second"},
        ]

        mapping = boards_by_window_title(windows, processes)

        self.assertEqual(mapping["first | Arduino IDE 2.3.4"]["board_name"], "First")
        self.assertEqual(mapping["second | Arduino IDE 2.3.4"]["board_name"], "Second")

    def test_board_mapping_rejects_shared_electron_window_pid(self) -> None:
        windows = [
            {"pid": 101, "title": "first | Arduino IDE 2.3.4"},
            {"pid": 101, "title": "second | Arduino IDE 2.3.4"},
        ]
        processes = [
            {"name": "Arduino IDE.exe", "pid": 101, "created_at": 1000, "command_line": ""},
            {"name": "Arduino IDE.exe", "pid": 102, "created_at": 2000, "command_line": r"backend\plugin-host"},
            {"name": "arduino-language-server.exe", "pid": 103, "parent_pid": 102, "fqbn": "vendor:arch:first", "board_name": "First"},
        ]

        self.assertEqual(boards_by_window_title(windows, processes), {})

    def test_arduino_workspace_summary_finds_main_sketch_and_tabs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Blink"
            root.mkdir()
            (root / "Blink.ino").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
            (root / "helpers.cpp").write_text("int value() { return 1; }\n", encoding="utf-8")
            (root / "notes.tmp").write_text("ignored\n", encoding="utf-8")

            summary = workspace_summary({"arduino_workspace_path": str(root), "arduino_fqbn": "arduino:avr:uno"})

            self.assertTrue(summary["valid"])
            self.assertEqual(summary["main_sketch"], "Blink.ino")
            self.assertEqual([item["path"] for item in summary["files"]], ["Blink.ino", "helpers.cpp"])

    def test_arduino_workspace_summary_includes_header_and_cpp_tabs_case_insensitively(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Controller"
            root.mkdir()
            (root / "Controller.ino").write_text("void setup() {}\n", encoding="utf-8")
            (root / "motor.CPP").write_text("void motor_init() {}\n", encoding="utf-8")
            (root / "motor.H").write_text("void motor_init();\n", encoding="utf-8")

            summary = workspace_summary({"arduino_workspace_path": str(root), "arduino_fqbn": ""})

            self.assertEqual(
                [item["path"] for item in summary["files"]],
                ["Controller.ino", "motor.CPP", "motor.H"],
            )

    def test_native_bridge_extracts_multiple_open_sketch_titles(self) -> None:
        self.assertEqual(extract_ino_names("1.ino - Arduino IDE"), ["1.ino"])
        self.assertEqual(extract_ino_names("2.ino | Arduino IDE"), ["2.ino"])
        self.assertEqual(extract_ino_names("test | Arduino IDE 2.3.4"), ["test.ino"])
        self.assertEqual(
            extract_ino_names("LQR_pendulum - lqr_controller.cpp | Arduino IDE 2.3.4"),
            ["LQR_pendulum.ino"],
        )
        self.assertEqual(
            extract_ino_names("LQR_pendulum - config.h | Arduino IDE 2.3.4"),
            ["LQR_pendulum.ino"],
        )
        self.assertIsInstance(native_available(), bool)

    def test_native_bridge_extracts_board_from_language_server_command(self) -> None:
        command = (
            'arduino-language-server.exe -cli-daemon-addr localhost:51373 '
            '-fqbn esp32:esp32:esp32:UploadSpeed=921600,CPUFreq=240 '
            '-board-name "ESP32 Dev Module"'
        )

        self.assertEqual(extract_fqbn(command), "esp32:esp32:esp32:UploadSpeed=921600,CPUFreq=240")
        self.assertEqual(extract_board_name(command), "ESP32 Dev Module")

    def test_native_bridge_parses_native_window_rows(self) -> None:
        rows = parse_window_rows_payload("123\tBlink | Arduino IDE 2.3.4\nbad\tIgnored\n456\tTalos")

        self.assertEqual(rows[0], {"pid": 123, "title": "Blink | Arduino IDE 2.3.4"})
        self.assertEqual(rows[1], {"pid": 0, "title": "Ignored"})
        self.assertEqual(rows[2], {"pid": 456, "title": "Talos"})

    def test_native_bridge_parses_native_process_rows(self) -> None:
        rows = parse_process_rows_payload(
            "Arduino IDE.exe\t101\t1\t1781541452000\narduino-language-server.exe\t102\t101\tbad"
        )

        self.assertEqual(rows[0]["name"], "Arduino IDE.exe")
        self.assertEqual(rows[0]["pid"], 101)
        self.assertEqual(rows[0]["parent_pid"], 1)
        self.assertEqual(rows[0]["created_at"], 1781541452000)
        self.assertEqual(rows[1]["created_at"], 0)
        self.assertEqual(rows[1]["fqbn"], "")

    def test_native_tool_processes_merge_native_snapshot_with_cached_command_lines(self) -> None:
        native_bridge._CACHE.clear()
        native_rows = [
            {
                "name": "Arduino IDE.exe",
                "pid": 101,
                "parent_pid": 1,
                "created_at": 10,
                "title": "",
                "command_line": "",
                "ino_paths": [],
                "fqbn": "",
                "board_name": "",
            },
            {
                "name": "arduino-language-server.exe",
                "pid": 102,
                "parent_pid": 101,
                "created_at": 11,
                "title": "",
                "command_line": "",
                "ino_paths": [],
                "fqbn": "",
                "board_name": "",
            },
        ]
        command_rows = [
            {
                "name": "arduino-language-server.exe",
                "pid": 102,
                "parent_pid": 101,
                "created_at": 11,
                "title": "",
                "command_line": "-fqbn esp32:esp32:esp32 -board-name ESP32",
                "ino_paths": [],
                "fqbn": "esp32:esp32:esp32",
                "board_name": "ESP32",
            }
        ]

        with (
            patch("talos.native_bridge.list_arduino_process_rows_native", return_value=native_rows),
            patch("talos.native_bridge.list_arduino_tool_processes_commandline_uncached", return_value=command_rows) as command_scan,
        ):
            first = native_bridge.list_arduino_tool_processes_uncached()
            second = native_bridge.list_arduino_tool_processes_uncached()

        self.assertEqual(command_scan.call_count, 1)
        self.assertEqual(first[1]["fqbn"], "esp32:esp32:esp32")
        self.assertEqual(second[1]["board_name"], "ESP32")

    def test_arduino_discovery_maps_open_sketches_to_folders(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Arduino"
            one = root / "1"
            two = root / "2"
            one.mkdir(parents=True)
            two.mkdir()
            (one / "1.ino").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
            (two / "2.ino").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")

            projects = discover_arduino_projects(
                {"arduino_search_roots": str(root)},
                titles=["1.ino - Arduino IDE", "2.ino - Arduino IDE"],
            )

            self.assertEqual([project["sketch"] for project in projects], ["1.ino", "2.ino"])
            self.assertEqual([Path(project["path"]).name for project in projects], ["1", "2"])
            self.assertTrue(all(project["valid"] for project in projects))
            self.assertTrue(all(project["source_count"] == 1 for project in projects))

    def test_arduino_discovery_maps_ide_2_titles_without_ino_extension(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Arduino"
            sketch = root / "test"
            sketch.mkdir(parents=True)
            (sketch / "test.ino").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")

            projects = discover_arduino_projects(
                {"arduino_search_roots": str(root)},
                titles=["test | Arduino IDE 2.3.4"],
            )

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["sketch"], "test.ino")
            self.assertEqual(Path(projects[0]["path"]).name, "test")
            self.assertTrue(projects[0]["valid"])

    def test_arduino_discovery_uses_window_title_when_process_has_no_ino_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Arduino"
            sketch = root / "test"
            sketch.mkdir(parents=True)
            (sketch / "test.ino").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")

            projects = discover_arduino_projects(
                {"arduino_search_roots": str(root)},
                titles=["test | Arduino IDE 2.3.4"],
                ino_paths=[],
            )

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["sketch"], "test.ino")
            self.assertEqual(projects[0]["source"], "window_title")

    def test_arduino_discovery_reuses_supplied_window_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Arduino"
            sketch = root / "test"
            sketch.mkdir(parents=True)
            (sketch / "test.ino").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")

            with patch("talos.arduino.open_window_titles", side_effect=AssertionError("window titles should be reused")):
                projects = discover_arduino_projects(
                    {"arduino_search_roots": str(root)},
                    window_rows=[{"pid": 123, "title": "test | Arduino IDE 2.3.4"}],
                    tool_processes=[],
                    open_workspaces=[],
                    workspace_boards={},
                )

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["sketch"], "test.ino")

    def test_state_payload_reuses_one_detection_snapshot(self) -> None:
        config = {"theme": "light", "arduino_workspace_path": "", "arduino_fqbn": ""}
        with (
            patch("talos.server.load_config", return_value=config),
            patch("talos.server.list_arduino_ide_processes", return_value=[]) as ide_scan,
            patch("talos.server.list_arduino_tool_processes", return_value=[]) as tool_scan,
            patch("talos.server.list_window_rows", return_value=[{"pid": 0, "title": "test | Arduino IDE 2.3.4"}]) as window_scan,
            patch("talos.server.list_arduino_open_workspaces", return_value=[]),
            patch("talos.server.list_arduino_workspace_boards", return_value={}),
        ):
            payload = state_payload()

        self.assertEqual(ide_scan.call_count, 1)
        self.assertEqual(tool_scan.call_count, 1)
        self.assertEqual(window_scan.call_count, 1)
        self.assertEqual(payload["app"]["publisher"], "T-Engine")
        self.assertEqual(payload["app"]["version"], "0.1.0")
        self.assertEqual(payload["app"]["channel"], "Beta")
        self.assertTrue(payload["arduino_ide"]["running"])

    def test_arduino_discovery_does_not_include_configured_folder_without_open_ide_signal(self) -> None:
        with TemporaryDirectory() as tmp:
            sketch = Path(tmp) / "test"
            sketch.mkdir()
            (sketch / "test.ino").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")

            projects = discover_arduino_projects({"arduino_workspace_path": str(sketch)}, titles=[])

            self.assertEqual(projects, [])

    def test_arduino_discovery_ignores_persisted_workspace_after_ide_closes(self) -> None:
        with TemporaryDirectory() as tmp:
            sketch = Path(tmp) / "closed_sketch"
            sketch.mkdir()
            (sketch / "closed_sketch.ino").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")

            with (
                patch("talos.arduino.list_arduino_ide_processes", return_value=[]),
                patch("talos.arduino.list_arduino_tool_processes", return_value=[]),
                patch("talos.arduino.open_window_titles", return_value=["Talos", "desktop_app.py - Visual Studio Code"]),
                patch("talos.arduino.list_arduino_open_workspaces", return_value=[{"path": str(sketch), "time": 1}]),
                patch(
                    "talos.arduino.list_arduino_workspace_boards",
                    return_value={
                        str(sketch.resolve()).lower(): {
                            "fqbn": "esp32:esp32:esp32",
                            "name": "ESP32 Dev Module",
                        }
                    },
                ),
            ):
                projects = discover_arduino_projects({})

            self.assertEqual(projects, [])

    def test_arduino_discovery_maps_open_process_ino_path_to_folder(self) -> None:
        with TemporaryDirectory() as tmp:
            sketch = Path(tmp) / "test"
            sketch.mkdir()
            ino = sketch / "test.ino"
            ino.write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")

            projects = discover_arduino_projects({}, titles=[], ino_paths=[str(ino)])

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["sketch"], "test.ino")
            self.assertEqual(Path(projects[0]["path"]).name, "test")
            self.assertTrue(projects[0]["valid"])

    def test_arduino_discovery_lists_multiple_open_ino_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            first = Path(tmp) / "one"
            second = Path(tmp) / "two"
            first.mkdir()
            second.mkdir()
            one = first / "one.ino"
            two = second / "two.ino"
            one.write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
            two.write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")

            projects = discover_arduino_projects({}, titles=[], ino_paths=[str(one), str(two)])

            self.assertEqual([project["sketch"] for project in projects], ["one.ino", "two.ino"])

    def test_arduino_discovery_combines_process_paths_and_window_titles(self) -> None:
        with TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop"
            parent = desktop / "test"
            title_sketch = parent
            process_sketch = parent / "mpu6050"
            configured_sketch = parent / "velo_test"
            title_sketch.mkdir(parents=True)
            process_sketch.mkdir()
            configured_sketch.mkdir()
            (title_sketch / "test.ino").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
            process_ino = process_sketch / "mpu6050.ino"
            process_ino.write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
            (configured_sketch / "velo_test.ino").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")

            projects = discover_arduino_projects(
                {"arduino_workspace_path": str(configured_sketch)},
                titles=["test | Arduino IDE 2.3.4"],
                ino_paths=[str(process_ino)],
            )

            self.assertEqual([project["sketch"] for project in projects], ["mpu6050.ino", "test.ino"])
            self.assertTrue(all(project["valid"] for project in projects))

    def test_arduino_discovery_ignores_stale_process_path_for_unsaved_current_sketch(self) -> None:
        with TemporaryDirectory() as tmp:
            old = Path(tmp) / "old"
            old.mkdir()
            old_ino = old / "old.ino"
            old_ino.write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")

            projects = discover_arduino_projects(
                {},
                titles=["sketch_jun11a | Arduino IDE 2.3.4"],
                ino_paths=[str(old_ino)],
            )

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["sketch"], "sketch_jun11a.ino")
            self.assertEqual(projects[0]["path"], "")
            self.assertFalse(projects[0]["valid"])
            self.assertEqual(projects[0]["source"], "window_title")
            self.assertTrue(projects[0]["unsaved"])
            self.assertEqual(projects[0]["status"], "unsaved")

    def test_arduino_discovery_attaches_detected_board_to_open_project(self) -> None:
        with TemporaryDirectory() as tmp:
            sketch = Path(tmp) / "test"
            sketch.mkdir()
            ino = sketch / "test.ino"
            ino.write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")

            projects = discover_arduino_projects(
                {},
                titles=[],
                ino_paths=[str(ino)],
                tool_processes=[
                    {
                        "fqbn": "esp32:esp32:esp32:UploadSpeed=921600",
                        "board_name": "ESP32 Dev Module",
                    }
                ],
            )

            self.assertEqual(projects[0]["fqbn"], "esp32:esp32:esp32:UploadSpeed=921600")
            self.assertEqual(projects[0]["board_name"], "ESP32 Dev Module")

    def test_arduino_discovery_matches_board_by_sketch_name_hint(self) -> None:
        with TemporaryDirectory() as tmp:
            first = Path(tmp) / "mpu6050"
            second = Path(tmp) / "mpu6050_esp32c3"
            first.mkdir()
            second.mkdir()
            first_ino = first / "mpu6050.ino"
            second_ino = second / "mpu6050_esp32c3.ino"
            first_ino.write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
            second_ino.write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")

            projects = discover_arduino_projects(
                {},
                titles=[],
                ino_paths=[str(first_ino), str(second_ino)],
                tool_processes=[
                    {
                        "fqbn": "esp32:esp32:esp32:UploadSpeed=921600",
                        "board_name": "ESP32 Dev Module",
                    },
                    {
                        "fqbn": "esp32:esp32:esp32c3:UploadSpeed=921600",
                        "board_name": "ESP32C3 Dev Module",
                    },
                ],
            )

            self.assertEqual(projects[0]["board_name"], "ESP32 Dev Module")
            self.assertEqual(projects[1]["board_name"], "ESP32C3 Dev Module")

    def test_arduino_discovery_prefers_exact_workspace_board_state(self) -> None:
        with TemporaryDirectory() as tmp:
            first = Path(tmp) / "controller_a"
            second = Path(tmp) / "controller_b"
            first.mkdir()
            second.mkdir()
            first_ino = first / "controller_a.ino"
            second_ino = second / "controller_b.ino"
            first_ino.write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
            second_ino.write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")

            projects = discover_arduino_projects(
                {},
                titles=[],
                ino_paths=[str(first_ino), str(second_ino)],
                tool_processes=[
                    {
                        "fqbn": "esp32:esp32:esp32:UploadSpeed=921600",
                        "board_name": "ESP32 Dev Module",
                    },
                    {
                        "fqbn": "esp32:esp32:esp32s3:USBMode=hwcdc",
                        "board_name": "ESP32S3 Dev Module",
                    },
                ],
                workspace_boards={
                    str(first.resolve()).lower(): {
                        "fqbn": "esp32:esp32:esp32s3",
                        "name": "ESP32S3 Dev Module",
                    },
                    str(second.resolve()).lower(): {
                        "fqbn": "esp32:esp32:esp32",
                        "name": "ESP32 Dev Module",
                    },
                },
            )

            self.assertEqual(projects[0]["board_name"], "ESP32S3 Dev Module")
            self.assertEqual(projects[0]["board_source"], "workspace_state")
            self.assertEqual(projects[1]["board_name"], "ESP32 Dev Module")

    def test_arduino_context_includes_sketch_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Sensor"
            root.mkdir()
            (root / "Sensor.ino").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")

            context = workspace_context({"arduino_workspace_path": str(root), "arduino_fqbn": ""})

            self.assertIn("Arduino workspace context", context)
            self.assertIn("--- Sensor.ino ---", context)

    def test_workspace_map_includes_board_tabs_and_latest_diagnostics(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Sensor"
            root.mkdir()
            (root / "Sensor.ino").write_text("void setup() {}\n", encoding="utf-8")
            (root / "motor.cpp").write_text("void motor() {}\n", encoding="utf-8")

            result = workspace_map(
                {"arduino_workspace_path": str(root), "arduino_fqbn": "arduino:avr:uno"},
                {"status": "passed", "time": "2026-06-24", "result": {
                    "issues": [{"message": "warning"}],
                    "libraries": [{"name": "Wire", "version": "1.0.0"}],
                    "platforms": [{"name": "arduino:avr", "version": "1.8.6"}],
                }},
            )

            self.assertTrue(result["valid"])
            self.assertEqual(result["main_sketch"], "Sensor.ino")
            self.assertEqual(result["board"]["fqbn"], "arduino:avr:uno")
            self.assertEqual(result["source_tab_count"], 2)
            self.assertEqual(result["diagnostics"]["status"], "passed")
            self.assertEqual(result["diagnostics"]["libraries"][0]["name"], "Wire")

    def test_environment_profile_is_isolated_per_resolved_sketch_folder(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "First"
            second = root / "Second"
            first.mkdir()
            second.mkdir()
            config = {"arduino_profiles": {}, "arduino_workspace_path": str(first), "arduino_fqbn": "arduino:avr:uno"}

            saved = save_environment_profile(config, str(first), {
                "fqbn": "esp32:esp32:esp32",
                "serial_port": "COM5",
                "baud_rate": "921600",
                "build_flags": ["-DDEBUG"],
                "libraries": "Wire\nArduinoJson",
            })

            self.assertTrue(saved["ok"])
            self.assertEqual(environment_profile(config, str(first))["serial_port"], "COM5")
            self.assertEqual(environment_profile(config, str(second))["libraries"], [])
            self.assertEqual(workspace_summary(config)["fqbn"], "esp32:esp32:esp32")

            mapped = workspace_map(config)
            self.assertEqual(mapped["environment_profile"]["baud_rate"], 921600)
            self.assertEqual(mapped["environment_profile"]["libraries"], ["Wire", "ArduinoJson"])

    def test_legacy_user_state_is_backed_up_and_migrated_without_losing_records(self) -> None:
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            config_path = config_dir / "config.json"
            legacy_config = {
                "theme": "dark",
                "arduino_workspace_path": r"C:\Sketches\Blink",
                "arduino_profiles": {"c:\\sketches\\blink": {"fqbn": "arduino:avr:uno"}},
            }
            config_path.write_text(json.dumps(legacy_config), encoding="utf-8")

            with patch.object(core, "CONFIG_PATH", config_path):
                migrated = core.load_config()

            self.assertEqual(migrated["schema_version"], core.CONFIG_SCHEMA_VERSION)
            self.assertEqual(migrated["theme"], "dark")
            self.assertIn("c:\\sketches\\blink", migrated["arduino_profiles"])
            self.assertTrue(list((config_dir / "backups").glob("config.migration.*.json")))

            checkpoint_path = config_dir / "checkpoints.json"
            checkpoint_path.write_text(json.dumps({"checkpoints": [{"id": "legacy-checkpoint"}]}), encoding="utf-8")
            with patch.object(checkpoint_store, "CHECKPOINT_PATH", checkpoint_path):
                checkpoints = checkpoint_store._load()
            self.assertEqual(checkpoints[0]["id"], "legacy-checkpoint")
            self.assertEqual(json.loads(checkpoint_path.read_text(encoding="utf-8"))["schema_version"], 1)

            history_path = config_dir / "run_history.json"
            history_path.write_text(json.dumps({"events": [{"id": "legacy-history"}]}), encoding="utf-8")
            with patch.object(run_history_store, "RUN_HISTORY_PATH", history_path):
                events = run_history_store._load_events()
            self.assertEqual(events[0]["id"], "legacy-history")
            self.assertEqual(json.loads(history_path.read_text(encoding="utf-8"))["schema_version"], 1)
            self.assertTrue(list((config_dir / "backups").glob("*.json")))

    def test_future_schema_is_not_downgraded_or_overwritten(self) -> None:
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            future = {"schema_version": core.CONFIG_SCHEMA_VERSION + 1, "theme": "dark", "future_setting": True}
            config_path.write_text(json.dumps(future), encoding="utf-8")

            with patch.object(core, "CONFIG_PATH", config_path):
                loaded = core.load_config()

            self.assertEqual(loaded["schema_version"], core.CONFIG_SCHEMA_VERSION + 1)
            self.assertTrue(loaded["future_setting"])
            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8")), future)

    def test_arduino_verify_requires_fqbn_before_compile(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Blink"
            root.mkdir()
            (root / "Blink.ino").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")

            result = run_arduino_compile({"arduino_workspace_path": str(root), "arduino_fqbn": ""})

            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "missing_fqbn")
            self.assertIn("prepare", result["timings"])
            self.assertIn("total", result["timings"])

    def test_compile_cache_is_keyed_by_workspace_content_and_can_be_cleared(self) -> None:
        clear_arduino_compile_cache()
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "Blink"
            workspace.mkdir()
            sketch = workspace / "Blink.ino"
            sketch.write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
            summary = {"fqbn": "arduino:avr:uno"}
            profile = {"build_flags": [], "build_properties": []}

            initial_key = compile_cache_key(workspace, summary, profile, "arduino-cli", None)
            from talos.arduino import store_compile_result
            store_compile_result(initial_key, {"ok": True, "status": "passed", "output": "ok"})
            cached = cached_compile_result(initial_key)

            self.assertTrue(cached["cache"]["hit"])
            self.assertEqual(cached["output"], "ok")

            sketch.write_text("void setup() { Serial.begin(9600); }\nvoid loop() {}\n", encoding="utf-8")
            changed_key = compile_cache_key(workspace, summary, profile, "arduino-cli", None)
            self.assertNotEqual(initial_key, changed_key)
            self.assertEqual(clear_arduino_compile_cache(), 1)
            self.assertIsNone(cached_compile_result(initial_key))

    def test_arduino_compile_output_parser_cleans_ansi_and_extracts_summary(self) -> None:
        output = (
            "Sketch uses 322548 bytes (24%) of program storage space. Maximum is 1310720 bytes.\n"
            "Global variables use 13796 bytes (4%) of dynamic memory, leaving 313884 bytes for local variables. Maximum is 327680 bytes.\n\n"
            "\x1b[92mUsed library\x1b[0m \x1b[92mVersion\x1b[0m \x1b[90mPath\x1b[0m\n"
            "\x1b[93mWire\x1b[0m         3.3.6   \x1b[90mC:\\Arduino\\Wire\x1b[0m\n\n"
            "\x1b[92mUsed platform\x1b[0m \x1b[92mVersion\x1b[0m \x1b[90mPath\x1b[0m\n"
            "\x1b[93mesp32:esp32\x1b[0m   3.3.6   \x1b[90mC:\\Arduino\\esp32\x1b[0m\n"
        )

        parsed = parse_compile_output(output)

        self.assertNotIn("\x1b", parsed["output"])
        self.assertEqual(parsed["memory"]["program"]["percent"], 24)
        self.assertEqual(parsed["memory"]["dynamic"]["used"], 13796)
        self.assertEqual(parsed["libraries"][0]["name"], "Wire")
        self.assertEqual(parsed["platforms"][0]["name"], "esp32:esp32")

    def test_arduino_compile_output_parser_extracts_file_line_issues(self) -> None:
        output = (
            r"C:\Sketch\Blink.ino:14:7: error: 'missingValue' was not declared in this scope"
            "\n"
            r"C:\Sketch\helpers.cpp:8: warning: unused variable 'sample'"
        )

        issues = parse_compile_output(output)["issues"]

        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0]["file"], r"C:\Sketch\Blink.ino")
        self.assertEqual(issues[0]["line"], 14)
        self.assertEqual(issues[0]["column"], 7)
        self.assertEqual(issues[0]["level"], "error")
        self.assertEqual(issues[1]["line"], 8)
        self.assertEqual(issues[1]["column"], 0)
        self.assertEqual(issues[1]["level"], "warning")
        self.assertEqual(
            format_compile_issue_context(issues),
            "Arduino compile issues:\n"
            "ERROR Blink.ino:14:7 - 'missingValue' was not declared in this scope\n"
            "WARNING helpers.cpp:8 - unused variable 'sample'",
        )

    def test_arduino_sandbox_copy_ignores_build_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Blink"
            root.mkdir()
            (root / "Blink.ino").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
            (root / "build").mkdir()
            (root / "build" / "old.o").write_text("ignore\n", encoding="utf-8")

            sandbox = copy_workspace_to_sandbox(root)

            self.assertEqual(sandbox.name, "Blink")
            self.assertTrue((sandbox / "Blink.ino").exists())
            self.assertFalse((sandbox / "build").exists())

    def test_arduino_sandbox_copy_ignores_platformio_cache(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Blink"
            root.mkdir()
            (root / "Blink.ino").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
            (root / ".pio").mkdir()
            (root / ".pio" / "cache.o").write_text("ignore\n", encoding="utf-8")

            sandbox = copy_workspace_to_sandbox(root)

            self.assertFalse((sandbox / ".pio").exists())

    def test_arduino_workspace_file_write_read_and_delete_are_scoped(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Blink"
            root.mkdir()
            config = {"arduino_workspace_path": str(root), "arduino_fqbn": ""}

            write_result = write_workspace_file(config, "Blink.ino", "void setup() {}\nvoid loop() {}\n")
            read_result = read_workspace_file(config, "Blink.ino")
            delete_result = delete_workspace_file(config, "Blink.ino")

            self.assertTrue(write_result["ok"])
            self.assertTrue(read_result["ok"])
            self.assertIn("void setup", read_result["content"])
            self.assertIsInstance(write_result["mtime_ns"], int)
            self.assertIsInstance(read_result["mtime_ns"], int)
            self.assertTrue(delete_result["ok"])
            self.assertFalse((root / "Blink.ino").exists())

    def test_arduino_workspace_file_write_is_atomic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Blink"
            root.mkdir()
            target = root / "Blink.ino"
            target.write_text("before\n", encoding="utf-8")
            config = {"arduino_workspace_path": str(root), "arduino_fqbn": ""}

            result = write_workspace_file(config, "Blink.ino", "after\n")

            self.assertTrue(result["ok"])
            self.assertEqual(result["write"], "atomic")
            self.assertEqual(target.read_text(encoding="utf-8"), "after\n")
            self.assertEqual(list(root.glob(".talos-write-*")), [])

    def test_arduino_workspace_file_rejects_escape_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Blink"
            root.mkdir()
            config = {"arduino_workspace_path": str(root), "arduino_fqbn": ""}

            result = write_workspace_file(config, "../outside.ino", "void setup() {}\n")

            self.assertFalse(result["ok"])
            self.assertFalse((Path(tmp) / "outside.ino").exists())

    def test_checkpoint_rolls_back_only_when_talos_saved_version_is_current(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Blink"
            root.mkdir()
            target = root / "Blink.ino"
            target.write_text("before\n", encoding="utf-8")
            config = {"arduino_workspace_path": str(root), "arduino_fqbn": ""}
            checkpoint_path = Path(tmp) / "checkpoints.json"

            with patch("talos.checkpoints.CHECKPOINT_PATH", checkpoint_path):
                created = create_before_save_checkpoint(config, "Blink.ino")
                self.assertTrue(created["ok"])
                write_workspace_file(config, "Blink.ino", "saved\n")
                marked = mark_checkpoint_saved(created["checkpoint"]["id"], "saved\n")
                self.assertTrue(marked["ok"])
                self.assertIsNotNone(latest_saved_checkpoint(config, "Blink.ino")["checkpoint"])

                rolled_back = rollback_last_checkpoint(config, "Blink.ino")
                self.assertTrue(rolled_back["ok"])
                self.assertEqual(target.read_text(encoding="utf-8"), "before\n")

                created = create_before_save_checkpoint(config, "Blink.ino")
                write_workspace_file(config, "Blink.ino", "saved again\n")
                mark_checkpoint_saved(created["checkpoint"]["id"], "saved again\n")
                target.write_text("external change\n", encoding="utf-8")
                blocked = rollback_last_checkpoint(config, "Blink.ino")
                self.assertFalse(blocked["ok"])
                self.assertIn("changed after Talos saved", blocked["error"])

if __name__ == "__main__":
    unittest.main()
