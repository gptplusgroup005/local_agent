import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

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
from talos.core import language_label
from talos.native_bridge import extract_board_name, extract_fqbn, native_available
from talos.codex_bridge import (
    CODEX_TURN_TIMEOUT_SECONDS,
    CodexBridge,
    THREAD_SANDBOX_MODE,
    build_codex_prompt,
    diff_workspace_snapshots,
    snapshot_workspace,
)

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
        self.assertIn("You may edit relevant files directly", prompt)

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
            root = Path(tmp)
            bridge = CodexBridge()
            bridge._turn_workspace = str(root)
            bridge._turn_track_changes = True
            bridge._turn_snapshot = snapshot_workspace(root)
            (root / "created.ino").write_text("void setup() {}\n", encoding="utf-8")

            bridge._finalize_turn_patch()
            status = bridge.status(start=False)

            self.assertEqual(status["patch_revision"], 1)
            self.assertEqual(status["patches"][0]["files"][0]["path"], "created.ino")
            self.assertEqual(status["patches"][0]["files"][0]["kind"], "add")

    def test_frontend_contains_codex_workbench_panel(self) -> None:
        html = (Path(__file__).parents[1] / "ui" / "web_frontend" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="codexPanel"', html)
        self.assertIn('id="codexComposer"', html)
        self.assertIn('id="toggleCodexBtn"', html)
        self.assertIn('id="editorLineNumbers"', html)
        self.assertIn("data-codex-prompt", html)
        self.assertIn('id="codexAllowEdits"', html)
        self.assertIn('id="cancelCodexBtn"', html)

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

    def test_language_label_defaults_to_vietnamese(self) -> None:
        self.assertEqual(language_label({"language": "vi"}), "Ti\u1ebfng Vi\u1ec7t")

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
