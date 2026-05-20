import unittest

from desktop_app import (
    DEFAULT_CONFIG,
    DETAIL_PANE_MIN_HEIGHT,
    LocalTaskEngine,
    QUEUE_PANE_MIN_HEIGHT,
    QUEUE_SPLIT_INITIAL_RATIO,
    QUEUE_SPLITTER_HEIGHT,
    process_prompt,
    queue_split_initial_sash_y,
)


class LocalTaskEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = DEFAULT_CONFIG | {"model_enabled": False, "language": "en"}
        self.engine = LocalTaskEngine(self.config)

    def test_math_expression_is_handled_locally(self) -> None:
        self.assertEqual(self.engine.handle("calculate sqrt(16) + 2"), "sqrt(16) + 2 = 6")

    def test_plain_sentence_with_numbers_is_not_treated_as_math(self) -> None:
        self.assertIsNone(self.engine.handle("first 10 numbers of pi"))
        self.assertIsNone(self.engine.handle("list prime numbers from 0-100"))

    def test_invalid_math_does_not_fail_task_when_model_is_disabled(self) -> None:
        result = process_prompt("first 10 numbers of pi", self.config)
        self.assertIn("Prototype mode", result)
        self.assertIn("first 10 numbers of pi", result)

    def test_trailing_question_marker_is_accepted_for_math(self) -> None:
        self.assertEqual(self.engine.handle("1+1=?"), "1+1 = 2")

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


if __name__ == "__main__":
    unittest.main()
