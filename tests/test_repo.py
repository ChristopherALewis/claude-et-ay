"""Checks on the repository itself: manifests, versions, licences and house style."""

import json
import unittest

import helpers

REPO = helpers.REPO
PLUGIN = helpers.PLUGIN
TEXT_SUFFIXES = {".py", ".md", ".json", ".sh", ".yml", ".yaml", ".toml", ".txt", ".cfg", ""}
EM_DASH = chr(0x2014)
SKIP_DIRS = {".git", "__pycache__", ".ruff_cache", ".pytest_cache"}


def text_files():
    for path in REPO.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts) or not path.is_file():
            continue
        if path.suffix in TEXT_SUFFIXES:
            yield path


class ManifestTests(unittest.TestCase):
    def load(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_json_files_parse(self):
        for path in REPO.rglob("*.json"):
            if ".git" not in path.parts:
                self.load(path)

    def test_versions_agree(self):
        from etay import __version__

        plugin = self.load(PLUGIN / ".claude-plugin" / "plugin.json")
        version_file = (REPO / "VERSION").read_text().strip()
        self.assertEqual(plugin["version"], __version__)
        self.assertEqual(version_file, __version__)
        self.assertIn(f"## [{__version__}]", (REPO / "CHANGELOG.md").read_text())

    def test_marketplace_points_at_plugin(self):
        market = self.load(REPO / ".claude-plugin" / "marketplace.json")
        plugin = self.load(PLUGIN / ".claude-plugin" / "plugin.json")
        (entry,) = market["plugins"]
        self.assertEqual(entry["name"], plugin["name"])
        self.assertEqual((REPO / entry["source"]).resolve(), PLUGIN.resolve())
        self.assertFalse(plugin["name"].startswith("claude"), "names starting with claude- are reserved")

    def test_hooks_reference_real_events_and_script(self):
        from etay.cli import HOOK_EVENTS

        hooks = self.load(PLUGIN / "hooks" / "hooks.json")["hooks"]
        for groups in hooks.values():
            for group in groups:
                for hook in group["hooks"]:
                    command = hook["command"]
                    self.assertIn('"${CLAUDE_PLUGIN_ROOT}/scripts/run.sh"', command)
                    self.assertIn(command.split()[-1], HOOK_EVENTS)

    def test_prompt_and_stop_hooks_are_synchronous(self):
        # Ordering matters for these: an async Stop could land after the next prompt.
        hooks = self.load(PLUGIN / "hooks" / "hooks.json")["hooks"]
        for event in ("UserPromptSubmit", "Stop", "SessionStart"):
            for group in hooks[event]:
                for hook in group["hooks"]:
                    self.assertFalse(hook.get("async"), event)

    def test_skills_have_frontmatter(self):
        skills = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
        self.assertGreaterEqual(len(skills), 5)
        for skill in skills:
            text = skill.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"), skill)
            front = text.split("---", 2)[1]
            self.assertRegex(front, r"\ndescription: .{20,}", skill)
            self.assertIn("disable-model-invocation: true", front, skill)


class LicenceTests(unittest.TestCase):
    def test_licence_present_and_identical(self):
        root = (REPO / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", root)
        self.assertIn("Christopher Lewis", root)
        self.assertEqual((PLUGIN / "LICENSE").read_text(encoding="utf-8"), root)

    def test_manifests_declare_mit(self):
        plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(plugin["license"], "MIT")

    def test_python_files_carry_no_third_party_imports(self):
        import ast
        import sys

        if not hasattr(sys, "stdlib_module_names"):
            self.skipTest("sys.stdlib_module_names needs Python 3.10 or later")
        stdlib = set(sys.stdlib_module_names) | {"msvcrt", "fcntl"}
        for path in (PLUGIN / "scripts").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [(node.module or "").split(".")[0]]
                else:
                    continue
                for name in names:
                    self.assertTrue(name in stdlib or name == "etay", f"{path.name} imports {name}")


class StyleTests(unittest.TestCase):
    def test_no_em_dashes(self):
        offenders = [str(p.relative_to(REPO)) for p in text_files() if EM_DASH in p.read_text(encoding="utf-8")]
        self.assertEqual(offenders, [])

    def test_shell_scripts_are_executable(self):
        import os

        self.assertTrue(os.access(helpers.RUN, os.X_OK))


if __name__ == "__main__":
    unittest.main()
