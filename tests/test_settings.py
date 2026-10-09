import json
import os
import subprocess
import unittest

import helpers
from etay import paths, settings

T0 = 1_800_000_000.0


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self._env = helpers.isolated_env()
        self.env = self._env.__enter__()
        self.settings_file = self.env["config"] / "settings.json"

    def tearDown(self):
        self._env.__exit__(None, None, None)

    def write_settings(self, data):
        self.settings_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def read_settings(self):
        return json.loads(self.settings_file.read_text(encoding="utf-8"))

    def backups(self):
        return sorted(p.name for p in self.env["config"].glob("settings.json.et-ay-backup-*"))

    def setup_plugin(self, **kwargs):
        return settings.setup(helpers.PLUGIN, self.env["data"], **kwargs)

    def test_fresh_install_without_settings_file(self):
        self.setup_plugin()
        line = self.read_settings()["statusLine"]
        self.assertEqual(line["type"], "command")
        self.assertIn("et-ay/statusline.sh", line["command"])
        self.assertEqual(line["refreshInterval"], 1)
        self.assertTrue(os.access(paths.shim_path(), os.X_OK))
        self.assertEqual((paths.anchor_dir() / "plugin_root").read_text().strip(), str(helpers.PLUGIN))

    def test_existing_settings_are_preserved_and_backed_up(self):
        self.write_settings({"model": "opus", "permissions": {"allow": ["Bash(ls)"]}})
        self.setup_plugin()
        data = self.read_settings()
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["permissions"], {"allow": ["Bash(ls)"]})
        self.assertEqual(list(data.keys())[:2], ["model", "permissions"])
        self.assertEqual(len(self.backups()), 1)

    def test_existing_status_line_is_wrapped_and_restored(self):
        original = {"type": "command", "command": "~/.claude/mine.sh", "padding": 2, "refreshInterval": 5}
        self.write_settings({"statusLine": original})
        notes = self.setup_plugin()
        self.assertTrue(any("kept" in n for n in notes), notes)
        line = self.read_settings()["statusLine"]
        self.assertEqual(line["padding"], 2)
        self.assertEqual(line["refreshInterval"], 1)
        self.assertEqual((paths.anchor_dir() / "wrapped_command").read_text().strip(), "~/.claude/mine.sh")

        settings.remove()
        self.assertEqual(self.read_settings()["statusLine"], original)

    def test_setup_is_idempotent(self):
        self.write_settings({"statusLine": {"type": "command", "command": "echo hi"}})
        self.setup_plugin()
        first = self.read_settings()
        notes = self.setup_plugin()
        self.assertEqual(self.read_settings(), first)
        self.assertTrue(any("nothing to change" in n for n in notes), notes)
        self.assertEqual(len(self.backups()), 1)
        # The original is still what gets restored, not our own shim.
        settings.remove()
        self.assertEqual(self.read_settings()["statusLine"]["command"], "echo hi")

    def test_faster_existing_refresh_is_kept(self):
        self.write_settings({"statusLine": {"type": "command", "command": "echo hi", "refreshInterval": 1}})
        self.setup_plugin(refresh=5)
        self.assertEqual(self.read_settings()["statusLine"]["refreshInterval"], 1)

    def test_refresh_can_be_raised_after_install(self):
        self.setup_plugin()
        self.setup_plugin(refresh=5)
        self.assertEqual(self.read_settings()["statusLine"]["refreshInterval"], 5)

    def test_symlinked_settings_stay_linked_and_keep_mode(self):
        real = self.env["home"] / "dotfiles" / "claude-settings.json"
        real.parent.mkdir()
        real.write_text('{"model": "opus"}', encoding="utf-8")
        real.chmod(0o644)
        self.settings_file.symlink_to(real)
        self.setup_plugin()
        self.assertTrue(self.settings_file.is_symlink())
        self.assertIn("statusLine", json.loads(real.read_text()))
        self.assertEqual(real.stat().st_mode & 0o777, 0o644)

    def test_no_wrap(self):
        self.write_settings({"statusLine": {"type": "command", "command": "echo hi"}})
        self.setup_plugin(wrap=False)
        self.assertFalse((paths.anchor_dir() / "wrapped_command").exists())
        settings.remove()
        self.assertEqual(self.read_settings()["statusLine"]["command"], "echo hi")

    def test_remove_without_previous_line_deletes_key(self):
        self.write_settings({"theme": "dark"})
        self.setup_plugin()
        settings.remove()
        self.assertEqual(self.read_settings(), {"theme": "dark"})

    def test_remove_leaves_someone_elses_line_alone(self):
        self.setup_plugin()
        self.write_settings({"statusLine": {"type": "command", "command": "other.sh"}})
        notes = settings.remove()
        self.assertEqual(self.read_settings()["statusLine"]["command"], "other.sh")
        self.assertTrue(any("left as it is" in n for n in notes))

    def test_remove_purge(self):
        self.setup_plugin()
        settings.remove(purge=True)
        self.assertFalse(paths.anchor_dir().exists())

    def test_refuses_unparseable_settings(self):
        self.settings_file.write_text('{\n  // a comment\n  "model": "opus"\n}\n', encoding="utf-8")
        with self.assertRaises(settings.SetupError):
            self.setup_plugin()
        self.assertIn("// a comment", self.settings_file.read_text())
        self.assertEqual(self.backups(), [])

    def test_position_option(self):
        from etay import config

        self.setup_plugin(position="line")
        self.assertEqual(config.load()["position"], "line")


class ShimTests(unittest.TestCase):
    """Run the generated shim exactly as Claude Code would."""

    def setUp(self):
        self._env = helpers.isolated_env()
        self.env = self._env.__enter__()
        settings_file = self.env["config"] / "settings.json"
        settings_file.write_text(json.dumps({"statusLine": {"type": "command", "command": "echo MINE"}}))
        settings.setup(helpers.PLUGIN, self.env["data"])
        # The status line process does not get plugin variables from Claude Code.
        for name in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT"):
            os.environ.pop(name, None)

    def tearDown(self):
        self._env.__exit__(None, None, None)

    def run_shim(self, payload, now=T0):
        env = os.environ.copy()
        env["ET_AY_NOW"] = repr(now)
        env["NO_COLOR"] = "1"
        return subprocess.run(
            ["sh", "-c", json.loads(json.dumps(self.command()))],
            input=json.dumps(payload).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=30,
        )

    def command(self):
        return json.loads((self.env["config"] / "settings.json").read_text())["statusLine"]["command"]

    def test_shim_shows_wrapped_line_when_idle(self):
        result = self.run_shim({"session_id": "nobody"})
        self.assertEqual(result.stdout.decode().strip(), "MINE")

    def test_shim_combines_with_running_turn(self):
        env_data = dict(os.environ, CLAUDE_PLUGIN_DATA=str(self.env["data"]), ET_AY_NOW=repr(T0))
        subprocess.run(
            ["sh", str(helpers.RUN), "hook", "prompt"],
            input=json.dumps({"session_id": "s", "prompt_id": "p", "prompt": "Add a feature"}).encode(),
            env=env_data,
            check=True,
            timeout=30,
        )
        out = self.run_shim({"session_id": "s"}, now=T0 + 30).stdout.decode().strip()
        self.assertTrue(out.startswith("MINE · ETA ~"), out)
        self.assertIn("30s in", out)

    def test_shim_falls_back_when_python_is_missing(self):
        # A PATH with the basic tools the shim needs but no Python at all.
        import shutil

        bin_dir = self.env["home"] / "nopython-bin"
        bin_dir.mkdir()
        for tool in ("sh", "cat", "dirname", "uname"):
            found = shutil.which(tool)
            if found:
                (bin_dir / tool).symlink_to(found)
        env = dict(os.environ, PATH=str(bin_dir))
        result = subprocess.run(
            [str(bin_dir / "sh"), str(paths.shim_path())],
            input=b'{"session_id": "s"}',
            stdout=subprocess.PIPE,
            env=env,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.decode().strip(), "MINE")

    def test_shim_falls_back_when_plugin_is_gone(self):
        (paths.anchor_dir() / "plugin_root").write_text("/nonexistent/plugin\n")
        result = self.run_shim({"session_id": "s"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.decode().strip(), "MINE")


if __name__ == "__main__":
    unittest.main()
