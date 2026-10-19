import os
import unittest

import helpers
from etay import config, paths, progress, render, tracker

NOW = 1_800_000_000.0


def report(line, age=60.0):
    parsed = progress.parse(line)
    parsed["updated_at"] = NOW - age
    return parsed


class ParseTests(unittest.TestCase):
    def test_count(self):
        self.assertEqual(progress.parse("3/7 Building"), {"fraction": 3 / 7, "count": "3/7", "label": "Building"})
        self.assertEqual(progress.parse(" 3 / 7 ")["count"], "3/7")
        self.assertEqual(progress.parse("9/7 Over")["fraction"], 1.0)

    def test_percent(self):
        self.assertEqual(progress.parse("42% Uploading"), {"fraction": 0.42, "count": "42%", "label": "Uploading"})
        self.assertEqual(progress.parse("12.5%")["count"], "12.5%")
        self.assertEqual(progress.parse("100% done")["fraction"], 1.0)

    def test_label_only(self):
        self.assertEqual(
            progress.parse("Indexing the repo"), {"fraction": None, "count": "", "label": "Indexing the repo"}
        )

    def test_junk_falls_back_to_a_label_or_nothing(self):
        for line in ("3/0 Nothing", "150% Too much", "-3/7 Odd", "a/b", "1/2/3"):
            self.assertIsNone(progress.parse(line)["fraction"], line)
            self.assertEqual(progress.parse(line)["label"], line)
        for line in ("", "   ", "\x00\x01\x02", "\n3/7 Second line"):
            self.assertIsNone(progress.parse(line), repr(line))

    def test_first_line_only(self):
        self.assertEqual(progress.parse("1/2 First\n2/2 Second")["label"], "First")
        self.assertEqual(progress.parse("1/2 First\r\n")["label"], "First")

    def test_control_characters_and_escapes_are_stripped(self):
        line = "\x1b[31m3/7\x1b[0m Build\x07ing \x1b]0;pwned\x07now\x1b[2J\ttab\x9b"
        parsed = progress.parse(line)
        self.assertEqual(parsed["count"], "3/7")
        self.assertEqual(parsed["label"], "Building now tab")
        self.assertNotRegex(parsed["label"], r"[\x00-\x1f\x7f-\x9f]")

    def test_length_is_capped(self):
        parsed = progress.parse("x" * 5000)
        self.assertEqual(len(parsed["label"]), progress.MAX_CHARS)


class FileTests(unittest.TestCase):
    def setUp(self):
        self._env = helpers.isolated_env()
        self.data = self._env.__enter__()["data"]

    def tearDown(self):
        self._env.__exit__(None, None, None)

    def write(self, text, session="s/1", mtime=NOW - 30):
        target = progress.file_for(self.data, session)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text if isinstance(text, bytes) else text.encode())
        os.utime(target, (mtime, mtime))
        return target

    def test_read_uses_mtime_and_safe_name(self):
        target = self.write("2/4 Copying")
        self.assertEqual(target.name, "s-1")
        got = progress.read(self.data, "s/1")
        self.assertEqual(got["count"], "2/4")
        self.assertEqual(got["updated_at"], NOW - 30)

    def test_read_missing_or_binary(self):
        self.assertIsNone(progress.read(self.data, "nobody"))
        self.write(b"\xff\xfe\x00 \x80")
        self.assertIsNone(progress.read(self.data, "s/1"))

    def test_session_end_removes_the_file(self):
        target = self.write("1/2 Going", session="s")
        tracker.Tracker(self.data, dict(config.DEFAULTS), NOW).handle("session-end", {"session_id": "s"})
        self.assertFalse(target.exists())

    def test_housekeeping_removes_old_files_only(self):
        old = self.write("1/2 Old", session="old", mtime=NOW - 30 * 86400)
        fresh = self.write("1/2 Fresh", session="fresh")
        tracker.housekeeping(self.data, NOW)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_export_line_quotes_awkward_paths(self):
        line = progress.export_line(self.data / "it's here", "s")
        self.assertTrue(line.startswith("export ET_AY_PROGRESS='"))
        self.assertIn("it'\\''s here", line)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(config.DEFAULTS)
        self.cfg["color"] = False

    def text(self, state, rep, now=NOW, **cfg):
        merged = dict(self.cfg, **cfg)
        return render.format_view(render.compute(state, now, merged), merged, render.progress_view(rep, now, merged))

    def test_bar_width(self):
        self.assertEqual(render.bar(0.6, 10, False), "▕██████░░░░▏")
        self.assertEqual(render.bar(0.6, 10, True), "[######----]")
        self.assertEqual(render.bar(0.0, 4, True), "[----]")
        self.assertEqual(render.bar(1.0, 4, True), "[####]")
        self.assertEqual(len(render.bar(3 / 7, 20, False)), 22)

    def test_progress_alone(self):
        self.assertEqual(self.text(None, report("3/7 Building", age=120)), "ETA bg ▕████░░░░░░▏ 3/7 Building · 2m ago")
        self.assertEqual(
            self.text(None, report("42% Uploading", age=3)), "ETA bg ▕████░░░░░░▏ 42% Uploading · just now"
        )

    def test_label_only_shows_label_and_age(self):
        self.assertEqual(self.text(None, report("Indexing", age=300)), "ETA bg Indexing · 5m ago")

    def test_custom_width(self):
        self.assertIn("▕███░░▏ 3/5", self.text(None, report("3/5 Go"), progress_width=5))

    def test_ascii(self):
        out = self.text(None, report("3/7 Building", age=120), ascii=True)
        self.assertEqual(out, "ETA bg [####------] 3/7 Building | 2m ago")
        out.encode("ascii")

    def test_stale(self):
        out = self.text(None, report("3/7 Building", age=34 * 60))
        self.assertTrue(out.endswith("3/7 Building · no update for 34m"), out)
        self.assertIn("· 14m ago", self.text(None, report("3/7 Building", age=14 * 60)))
        self.assertIn("no update for 5m", self.text(None, report("Hmm", age=300), progress_stale_minutes=5))

    def test_done_then_hidden_after_idle_seconds(self):
        self.assertEqual(self.text(None, report("3/3 Building", age=60)), "ETA bg ▕██████████▏ 3/3 Building · 1m ago")
        self.assertIn("100% Upload", self.text(None, report("100% Upload", age=60)))
        # Finished jobs are never shown as stale, and go once idle_seconds has passed.
        self.assertNotIn("no update", self.text(None, report("3/3 Building", age=20 * 60)))
        self.assertEqual(self.text(None, report("3/3 Building", age=901)), "")
        self.assertIn("3/3", self.text(None, report("3/3 Building", age=10**6), idle_seconds=0))

    def test_colours(self):
        def coloured(line, age):
            cfg = dict(config.DEFAULTS)
            return render.format_view(None, cfg, render.progress_view(report(line, age), NOW, cfg))

        self.assertIn("\033[33mno update for 34m", coloured("3/7 Go", 34 * 60))
        self.assertTrue(coloured("3/3 Go", 60).split(" ", 1)[1].startswith("\033[32mbg "))
        self.assertNotIn("\033[33m", coloured("3/7 Go", 60))

    def test_follows_running_segments(self):
        from test_render import running_state

        out = self.text(running_state(), report("3/7 Building", age=120))
        self.assertTrue(out.startswith("ETA ~3m left (1-5m)"), out)
        self.assertTrue(out.endswith("1m 00s in · bg ▕████░░░░░░▏ 3/7 Building · 2m ago"), out)

    def test_follows_idle_summary(self):
        state = {"turn": None, "last": {"outcome": "completed", "ended_at": NOW - 60, "active_s": 245, "p50": 200}}
        self.assertEqual(
            self.text(state, report("Indexing", age=60)), "ETA last 4m 05s (est 3m) · bg Indexing · 1m ago"
        )

    def test_disabled(self):
        self.assertEqual(self.text(None, report("3/7 Building"), progress=False), "")


class HookTests(unittest.TestCase):
    def setUp(self):
        self._env = helpers.isolated_env()
        self.env = self._env.__enter__()
        self.env_file = self.env["home"] / "sessionstart-hook-1.sh"

    def tearDown(self):
        self._env.__exit__(None, None, None)

    def start(self, source="startup", session="abc/1"):
        return helpers.run_cli(
            ["hook", "session-start"],
            stdin={"session_id": session, "source": source},
            now=NOW,
            extra_env={"CLAUDE_ENV_FILE": str(self.env_file)},
        )

    def test_env_line_written_once_and_silently(self):
        for source in ("startup", "compact", "resume"):
            result = self.start(source)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")
        expected = f"export ET_AY_PROGRESS='{self.env['data'] / 'progress' / 'abc-1'}'"
        self.assertEqual(self.env_file.read_text().splitlines(), [expected])
        self.assertTrue((self.env["data"] / "progress").is_dir())
        self.assertFalse((self.env["data"] / "errors.log").exists())

    def test_existing_env_file_content_is_kept(self):
        self.env_file.write_text("export OTHER=1")
        self.start()
        lines = self.env_file.read_text().splitlines()
        self.assertEqual(lines[0], "export OTHER=1")
        self.assertTrue(lines[1].startswith("export ET_AY_PROGRESS="))

    def test_line_sources_into_the_shell(self):
        self.start()
        target = self.env["data"] / "progress" / "abc-1"
        result = helpers.subprocess.run(
            ["sh", "-c", f'. "{self.env_file}" && printf "%s\\n" "1/2 Hi" > "$ET_AY_PROGRESS"'], timeout=10
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(target.read_text(), "1/2 Hi\n")

    def test_no_env_file_or_disabled(self):
        result = helpers.hook("session-start", {"session_id": "s", "source": "startup"}, NOW)
        self.assertEqual(result.stdout, b"")
        helpers.run_cli(["config", "set", "progress", "false"])
        self.start()
        self.assertFalse(self.env_file.exists())

    def test_statusline_shows_progress(self):
        self.start()
        target = self.env["data"] / "progress" / "abc-1"
        target.write_text("2/4 Copying\n")
        os.utime(target, (NOW - 120, NOW - 120))
        helpers.run_cli(["config", "set", "color", "false"])
        result = helpers.run_cli(["statusline"], stdin={"session_id": "abc/1"}, now=NOW)
        self.assertEqual(result.stdout.decode().strip(), "ETA bg ▕█████░░░░░▏ 2/4 Copying · 2m ago")
        self.assertTrue(paths.data_dir() == self.env["data"])


if __name__ == "__main__":
    unittest.main()
