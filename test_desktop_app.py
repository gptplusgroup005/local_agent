import unittest

from desktop_app import DEFAULT_CONFIG, LocalTaskEngine, process_prompt


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


if __name__ == "__main__":
    unittest.main()
