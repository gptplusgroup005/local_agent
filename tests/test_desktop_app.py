import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from talos import native_bridge
from talos.arduino import (
    boards_by_window_title,
    copy_workspace_to_sandbox,
    delete_workspace_file,
    discover_arduino_projects,
    extract_ino_names,
    format_compile_issue_context,
    parse_compile_output,
    read_workspace_file,
    run_arduino_compile,
    workspace_context,
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
    build_codex_prompt,
    diff_workspace_snapshots,
    messages_from_codex_thread,
    normalize_codex_thread,
    snapshot_workspace,
)
from talos.run_history import record_patch, record_verify, run_history
from talos.server import state_payload

class TalosArduinoTests(unittest.TestCase):
    def test_codex_prompt_contains_selected_arduino_context(self) -> None:
        prompt = build_codex_prompt(
            "Fix the compile error.",
            {
                "path": r"C:\Sketch\Blink",
                "main_sketch": "Blink.ino",
                "fqbn": "arduino:avr:uno",
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
            self.assertEqual(status["patches"][0]["files"][0]["review_status"], "pending")
            self.assertEqual(status["patches"][0]["review_status"], "pending")
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
                    "review_status": "pending",
                    "files": [{"path": "Sketch.ino", "kind": "update", "content": "new\n"}],
                }
            )

            result = bridge.apply_patch("patch-1", str(root), "Sketch.ino")

            self.assertTrue(result["ok"])
            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(result["file"]["content"], "new\n")
            self.assertEqual(bridge._patches[0]["files"][0]["review_status"], "editor")
            self.assertEqual(bridge._patches[0]["review_status"], "editor")

    def test_frontend_contains_codex_workbench_panel(self) -> None:
        html = (Path(__file__).parents[1] / "ui" / "web_frontend" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="codexPanel"', html)
        self.assertIn('id="codexComposer"', html)
        self.assertIn('id="toggleCodexBtn"', html)
        self.assertIn('id="editorLineNumbers"', html)
        self.assertIn("data-codex-prompt", html)
        self.assertIn('id="codexAllowEdits"', html)
        self.assertIn('id="cancelCodexBtn"', html)
        self.assertIn('id="virtualPatchToggleBtn"', html)
        self.assertIn('id="codexDiffPreview"', html)
        self.assertNotIn('id="codexHistoryBtn"', html)
        self.assertIn('id="codexBackBtn"', html)
        self.assertIn('id="codexHistoryCount"', html)
        self.assertIn('id="runHistoryTab"', html)
        self.assertIn('id="explorerSplitter"', html)
        self.assertNotIn('id="codexSplitter"', html)

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
        self.assertIn("Streaming Codex patch", script)
        self.assertIn("transientWorkspaceLoss", script)
        self.assertIn("activeFileByWorkspace", script)
        self.assertIn("applyCodexPatch", script)
        self.assertIn("rejectCodexPatch", script)
        self.assertIn("codexDiffPreview", html)
        self.assertIn("virtualPatchEnabled", script)
        self.assertIn("toggleVirtualPatchMode", script)
        self.assertIn("virtualPatchStatus", script)
        self.assertIn("Virtual patch applied to Talos editor", script)
        self.assertIn("virtual_patch_enabled: false", script)
        self.assertIn('streaming ? "Apply Stream"', script)
        self.assertIn('addEventListener("click", () => applyCodexPatch())', script)
        self.assertIn("selectEditorLine", script)
        self.assertIn("lineFromGutterEvent", script)
        self.assertIn("setInterval(checkActiveFileOnDisk", script)
        self.assertIn("View all (", script)
        self.assertIn("relativeTimeLabel", script)
        self.assertIn("showCodexTasks(true)", script)
        self.assertNotIn("toggleCodexHistory", script)
        self.assertIn("bindExplorerSplitter", script)
        self.assertNotIn("CODEX_WIDTH_KEY", script)
        self.assertNotIn("applyWindowMetrics", script)
        self.assertNotIn("--native-window-width", script)

        styles = (Path(__file__).parents[1] / "ui" / "web_frontend" / "styles.css").read_text(encoding="utf-8")
        self.assertNotIn("width: 100vw;", styles)
        self.assertIn("max-width: none;", styles)
        self.assertIn("justify-self: stretch;", styles)
        self.assertIn("inset: 0;", styles)
        self.assertIn("border-left: 1px solid var(--line);", styles)
        self.assertIn("minmax(320px, 1fr)", styles)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", styles)
        self.assertIn("grid-template-areas:", styles)
        self.assertIn("grid-column: 1 / -1;", styles)
        self.assertIn("display: none;", styles)
        self.assertIn("[hidden]", styles)
        self.assertIn("display: none !important;", styles)
        self.assertIn("cursor: pointer;", styles)
        self.assertNotIn("--codex-panel-width", styles)

        check_script = (Path(__file__).parents[1] / "scripts" / "check.ps1").read_text(encoding="utf-8")
        self.assertIn("build_native.ps1", check_script)
        self.assertIn("_HAS_NATIVE_WINDOW_ROWS", check_script)
        self.assertIn("_HAS_NATIVE_PROCESS_ROWS", check_script)
        self.assertIn("unittest tests.test_desktop_app", check_script)

        smoke_test = (Path(__file__).parents[1] / "docs" / "ARDUINO_SMOKE_TEST.md").read_text(encoding="utf-8")
        self.assertIn("Verify Sandbox", smoke_test)
        self.assertIn("Codex", smoke_test)
        self.assertIn("Pass Criteria", smoke_test)
        self.assertIn("Fail Conditions", smoke_test)

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
                events = run_history()

            self.assertEqual([event["type"] for event in events], ["patch", "verify"])
            self.assertEqual(events[1]["source"], "codex_patch")

    def test_codex_status_starts_runtime_without_blocking_for_handshake(self) -> None:
        bridge = CodexBridge()

        with patch.object(bridge, "start_async") as start_async:
            status = bridge.status()

        start_async.assert_called_once_with()
        self.assertFalse(status["connected"])
        self.assertFalse(status["ok"])

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

    def test_arduino_workspace_file_rejects_escape_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Blink"
            root.mkdir()
            config = {"arduino_workspace_path": str(root), "arduino_fqbn": ""}

            result = write_workspace_file(config, "../outside.ino", "void setup() {}\n")

            self.assertFalse(result["ok"])
            self.assertFalse((Path(tmp) / "outside.ino").exists())

if __name__ == "__main__":
    unittest.main()
