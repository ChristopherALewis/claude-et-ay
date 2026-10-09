import csv
import io
import json
import unittest

import helpers
from etay import paths

T0 = 1_800_000_000.0


class HookProcessTests(unittest.TestCase):
    """Hooks run as real subprocesses through run.sh, as Claude Code runs them."""

    def setUp(self):
        self._env = helpers.isolated_env()
        self.env = self._env.__enter__()

    def tearDown(self):
        self._env.__exit__(None, None, None)

    def test_prompt_hook_is_silent(self):
        # UserPromptSubmit stdout is injected into Claude's context, so it must stay empty.
        result = helpers.hook("prompt", {"session_id": "s", "prompt_id": "p", "prompt": "hi"}, T0)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_bad_input_never_fails(self):
        for stdin in ("", "not json", "[1,2,3]", '{"session_id": 5}'):
            result = helpers.run_cli(["hook", "stop"], stdin=stdin, now=T0)
            self.assertEqual(result.returncode, 0, stdin)
            self.assertEqual(result.stdout, b"")

    def test_internal_errors_go_to_the_log(self):
        sessions = paths.sessions_dir(self.env["data"])
        sessions.mkdir(parents=True)
        # A directory where the state file should be forces a write error.
        (sessions / "s.json").mkdir()
        result = helpers.hook("prompt", {"session_id": "s", "prompt_id": "p", "prompt": "hi"}, T0)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        log = (self.env["data"] / "errors.log").read_text()
        self.assertIn("[prompt]", log)

    def test_session_start_writes_pointers_and_heartbeat(self):
        helpers.hook("session-start", {"session_id": "s", "source": "startup"}, T0)
        self.assertEqual((paths.anchor_dir() / "plugin_root").read_text().strip(), str(helpers.PLUGIN))
        self.assertEqual((paths.anchor_dir() / "data_dir").read_text().strip(), str(self.env["data"]))
        beats = json.loads((self.env["data"] / "heartbeat.json").read_text())
        self.assertEqual(beats["session-start"], T0)

    def test_debug_log_is_redacted(self):
        helpers.run_cli(["config", "set", "debug", "true"])
        helpers.hook("prompt", {"session_id": "s", "prompt_id": "p", "prompt": "TOP SECRET plan"}, T0)
        helpers.hook("tool", {"session_id": "s", "prompt_id": "p", "tool_input": {"command": "cat secret"}}, T0 + 1)
        helpers.hook(
            "tool",
            {"session_id": "s", "prompt_id": "p", "error": "SECRET_TOOL_OUTPUT", "brand_new_field": "LEAK"},
            T0 + 2,
        )
        helpers.hook(
            "task-created",
            {
                "session_id": "s",
                "prompt_id": "p",
                "task_id": "1",
                "task_subject": "SECRET_SUBJECT",
                "task_description": "SECRET_DESC",
            },
            T0 + 3,
        )
        text = (self.env["data"] / "debug.jsonl").read_text()
        for secret in ("TOP SECRET", "cat secret", "SECRET_TOOL_OUTPUT", "LEAK", "SECRET_SUBJECT", "SECRET_DESC"):
            self.assertNotIn(secret, text)
        self.assertIn('"chars": 15', text)

    def test_full_cycle_and_stats(self):
        for i in range(5):
            start = T0 + i * 1000
            helpers.hook("prompt", {"session_id": "s", "prompt_id": f"p{i}", "prompt": "Write a test"}, start)
            helpers.hook("stop", {"session_id": "s", "prompt_id": f"p{i}"}, start + 100 + i)
        out = helpers.run_cli(["stats"]).stdout.decode()
        self.assertIn("5 turns recorded", out)
        self.assertIn("Accuracy over 5 scored turns", out)

    def test_statusline_with_nothing_configured(self):
        result = helpers.run_cli(["statusline"], stdin={"session_id": "unknown"}, now=T0)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def test_statusline_handles_garbage(self):
        for stdin in ("", "{", "[]"):
            self.assertEqual(helpers.run_cli(["statusline"], stdin=stdin).returncode, 0)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._env = helpers.isolated_env()
        self.env = self._env.__enter__()

    def tearDown(self):
        self._env.__exit__(None, None, None)

    def test_config_round_trip(self):
        self.assertEqual(helpers.run_cli(["config", "set", "label", "⏱"]).returncode, 0)
        self.assertEqual(helpers.run_cli(["config", "set", "segments", '["left","elapsed"]']).returncode, 0)
        shown = helpers.run_cli(["config", "show"]).stdout.decode()
        self.assertIn('* label            "⏱"', shown)
        self.assertEqual(helpers.run_cli(["config", "unset", "label"]).returncode, 0)
        self.assertIn('   label            "ETA"', helpers.run_cli(["config", "show"]).stdout.decode())

    def test_config_validation(self):
        for args in (
            ["config", "set", "nonsense", "1"],
            ["config", "set", "color", '"yes"'],
            ["config", "set", "segments", '["left","bogus"]'],
            ["config", "set", "position", '"sideways"'],
            ["config", "set", "half_life_days", "0"],
            ["config", "set", "history_window", "true"],
            ["config", "set", "stale_minutes", "0"],
            ["config", "set", "prior_strength", "-1"],
            ["config", "set", "idle_seconds", "-5"],
        ):
            result = helpers.run_cli(args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn("Error", result.stdout.decode())

    def test_damaged_config_falls_back_to_defaults(self):
        paths.config_path().parent.mkdir(parents=True, exist_ok=True)
        paths.config_path().write_text('{"label": 42, "color": false, "oops"')
        shown = helpers.run_cli(["config", "show"]).stdout.decode()
        self.assertIn('label            "ETA"', shown)

    def test_reset_needs_confirmation(self):
        helpers.hook("prompt", {"session_id": "s", "prompt_id": "p", "prompt": "x"}, T0)
        helpers.hook("stop", {"session_id": "s", "prompt_id": "p"}, T0 + 5)
        self.assertEqual(helpers.run_cli(["reset"]).returncode, 1)
        self.assertTrue(paths.history_path(self.env["data"]).exists())
        self.assertEqual(helpers.run_cli(["reset", "--yes"]).returncode, 0)
        self.assertFalse(paths.history_path(self.env["data"]).exists())

    def test_export_csv(self):
        helpers.hook("prompt", {"session_id": "s", "prompt_id": "p", "prompt": "/review 12"}, T0)
        helpers.hook("stop", {"session_id": "s", "prompt_id": "p"}, T0 + 42)
        rows = list(csv.DictReader(io.StringIO(helpers.run_cli(["export"]).stdout.decode())))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["command"], "review")
        self.assertEqual(float(rows[0]["active_s"]), 42.0)

    def test_doctor_runs(self):
        out = helpers.run_cli(["doctor"]).stdout.decode()
        self.assertIn("health check", out)
        self.assertIn("No status line configured", out)

    def test_data_dir_placeholder_is_ignored(self):
        # A skill whose ${CLAUDE_PLUGIN_DATA} was not substituted must not create a literal "${...}" folder.
        result = helpers.run_cli(["--data-dir", "${CLAUDE_PLUGIN_DATA}", "stats"])
        self.assertEqual(result.returncode, 0)
        self.assertFalse(any("${" in p.name for p in self.env["home"].rglob("*")))

    def test_version(self):
        from etay import __version__

        self.assertEqual(helpers.run_cli(["version"]).stdout.decode().strip(), __version__)


if __name__ == "__main__":
    unittest.main()
