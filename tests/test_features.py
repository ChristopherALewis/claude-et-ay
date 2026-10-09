import unittest

import helpers  # noqa: F401  (sets up the import path)
from etay import features


class FeatureExtractionTests(unittest.TestCase):
    def test_question(self):
        f = features.extract("Why does the parser drop trailing commas?")
        self.assertEqual(f["kind"], "question")
        self.assertIsNone(f["command"])

    def test_task(self):
        self.assertEqual(features.extract("Refactor the billing module")["kind"], "task")

    def test_slash_command(self):
        f = features.extract("/review-pr 1234 --strict")
        self.assertEqual(f["kind"], "command")
        self.assertEqual(f["command"], "review-pr")

    def test_plugin_namespaced_command(self):
        self.assertEqual(features.extract("/et-ay:stats")["command"], "et-ay:stats")

    def test_sentence_starting_with_slash_is_not_stored(self):
        f = features.extract("/ProjectAtlas is broken again")
        self.assertTrue(f["command"].startswith("#"))
        self.assertNotIn("atlas", f["command"].lower())
        self.assertEqual(f["command"], features.extract("/ProjectAtlas other words")["command"])

    def test_notification(self):
        f = features.extract("<task-notification>\nAgent finished\n</task-notification>")
        self.assertEqual(f["kind"], "notification")

    def test_counts(self):
        prompt = "Look at @src/app.py and @README.md\n```\ncode\n```\nsee https://example.com"
        f = features.extract(prompt)
        self.assertEqual(f["mentions"], 2)
        self.assertEqual(f["code_blocks"], 1)
        self.assertEqual(f["urls"], 1)
        self.assertEqual(f["lines"], 5)

    def test_pasted_and_broad(self):
        f = features.extract('<pasted_content id="1">x</pasted_content id="1"> update every file in the repo')
        self.assertTrue(f["pasted"])
        self.assertTrue(f["broad"])

    def test_empty(self):
        f = features.extract("")
        self.assertEqual(f["chars"], 0)
        self.assertEqual(f["lines"], 0)

    def test_no_text_is_retained(self):
        secret = "my password is hunter2-correct-horse"
        f = features.extract(secret)
        for value in f.values():
            self.assertNotIn("hunter2", str(value))


if __name__ == "__main__":
    unittest.main()
