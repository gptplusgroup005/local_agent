import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from desktop_app import (
    DEFAULT_CONFIG,
    DETAIL_PANE_MIN_HEIGHT,
    LocalComputerActionEngine,
    QUEUE_PANE_MIN_HEIGHT,
    QUEUE_SPLIT_INITIAL_RATIO,
    QUEUE_SPLITTER_HEIGHT,
    TaskStore,
    process_prompt,
    queue_split_initial_sash_y,
)
from talos_arduino import (
    copy_workspace_to_sandbox,
    delete_workspace_file,
    discover_arduino_projects,
    extract_ino_names,
    read_workspace_file,
    run_arduino_compile,
    workspace_context,
    workspace_summary,
    write_workspace_file,
)
from talos_core import detect_language, language_label

class LocalComputerActionEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = DEFAULT_CONFIG | {"model_enabled": False, "language": "en"}
        self.engine = LocalComputerActionEngine(self.config)

    def test_math_expression_is_not_handled_locally(self) -> None:
        self.assertIsNone(self.engine.handle("calculate sqrt(16) + 2"))

    def test_plain_sentence_with_numbers_is_not_handled_locally(self) -> None:
        self.assertIsNone(self.engine.handle("first 10 numbers of pi"))
        self.assertIsNone(self.engine.handle("list prime numbers from 0-100"))

    def test_math_falls_back_to_model_path_when_model_is_disabled(self) -> None:
        result = process_prompt("calculate sqrt(16) + 2", self.config)
        self.assertIn("Prototype mode", result)
        self.assertIn("calculate sqrt(16) + 2", result)

    def test_general_question_falls_back_to_model_path_when_model_is_disabled(self) -> None:
        result = process_prompt("first 10 numbers of pi", self.config)
        self.assertIn("Prototype mode", result)
        self.assertIn("first 10 numbers of pi", result)

    def test_realtime_queries_are_not_handled_locally(self) -> None:
        self.assertIsNone(self.engine.handle("weather in Hanoi"))
        self.assertIsNone(self.engine.handle("latest NBA score for Lakers vs Warriors"))

    def test_language_strings_survive_core_split(self) -> None:
        self.assertEqual(language_label({"language": "vi"}), "Tiếng Việt")
        self.assertEqual(detect_language("hãy sửa lỗi này"), "vi")

    def test_queue_splitter_initial_position_respects_panel_bounds(self) -> None:
        total_height = 500
        available = total_height - QUEUE_SPLITTER_HEIGHT

        self.assertGreaterEqual(queue_split_initial_sash_y(total_height), QUEUE_PANE_MIN_HEIGHT)
        self.assertEqual(
            queue_split_initial_sash_y(1000),
            round((1000 - QUEUE_SPLITTER_HEIGHT) * QUEUE_SPLIT_INITIAL_RATIO),
        )
        self.assertLessEqual(queue_split_initial_sash_y(total_height), available - DETAIL_PANE_MIN_HEIGHT)

    def test_queue_splitter_stays_valid_when_space_is_tight(self) -> None:
        self.assertEqual(queue_split_initial_sash_y(20), 2)

    def test_task_store_does_not_write_for_missing_update(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            store = TaskStore(path)
            before = path.stat().st_mtime_ns

            store.update(999, status="done")

            self.assertEqual(path.stat().st_mtime_ns, before)
            self.assertEqual(store.read(), [])

    def test_task_store_cache_refreshes_after_external_write(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            store = TaskStore(path)
            self.assertEqual(store.read(), [])

            path.write_text(
                '[{"id": 7, "prompt": "hi", "status": "queued"}]',
                encoding="utf-8",
            )

            self.assertEqual(store.read()[0]["id"], 7)

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

    def test_arduino_window_titles_detect_multiple_open_sketches(self) -> None:
        self.assertEqual(extract_ino_names("1.ino - Arduino IDE"), ["1.ino"])
        self.assertEqual(extract_ino_names("2.ino | Arduino IDE"), ["2.ino"])

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

    def test_arduino_sandbox_copy_ignores_build_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Blink"
            root.mkdir()
            (root / "Blink.ino").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
            (root / "build").mkdir()
            (root / "build" / "old.o").write_text("ignore\n", encoding="utf-8")

            sandbox = copy_workspace_to_sandbox(root)

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
