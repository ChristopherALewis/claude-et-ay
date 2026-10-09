import os
import unittest

import helpers  # noqa: F401
from etay import config, render

NOW = 1_800_000_000.0


def running_state(**overrides):
    turn = {
        "status": "running",
        "started_at": NOW - 60,
        "last_activity": NOW - 5,
        "estimate": {
            "sample": [[120, 1], [180, 1], [240, 1], [300, 1], [360, 1]],
            "confidence": "medium",
        },
        "joined": [],
        "tasks_created": [],
        "tasks_completed": [],
        "first_task_at": None,
        "waiting_since": None,
        "waiting_s": 0.0,
    }
    turn.update(overrides)
    return {"turn": turn, "last": None}


class FormattingTests(unittest.TestCase):
    def test_approx(self):
        self.assertEqual(render.approx(3), "<10s")
        self.assertEqual(render.approx(42), "40s")
        self.assertEqual(render.approx(70), "1m")
        self.assertEqual(render.approx(330), "6m")
        self.assertEqual(render.approx(3600), "1h")
        self.assertEqual(render.approx(5400), "1h 30m")

    def test_exact(self):
        self.assertEqual(render.exact(9.7), "9s")
        self.assertEqual(render.exact(125), "2m 05s")
        self.assertEqual(render.exact(3725), "1h 02m")

    def test_span(self):
        self.assertEqual(render.span(120, 360), "2-6m")
        self.assertEqual(render.span(40, 360), "40s-6m")
        self.assertEqual(render.span(20, 45), "20-45s")
        self.assertEqual(render.span(300, 310), "5m")


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(config.DEFAULTS)
        self.cfg["color"] = False

    def text(self, state, now=NOW, **cfg):
        merged = dict(self.cfg, **cfg)
        return render.format_view(render.compute(state, now, merged), merged)

    def test_running(self):
        out = self.text(running_state())
        self.assertTrue(out.startswith("ETA ~3m left (1-5m)"), out)
        self.assertIn("1m 00s in", out)
        self.assertIn("done ≈", out)

    def test_learning_marks_guess(self):
        state = running_state(estimate={"sample": [[100, 1], [200, 1]], "confidence": "learning"})
        self.assertIn("~2m? left", self.text(state))

    def test_waiting(self):
        out = self.text(running_state(waiting_since=NOW - 30, waiting_s=10))
        self.assertIn("paused, waiting for you", out)
        self.assertNotIn("done", out)

    def test_overdue(self):
        out = self.text(running_state(started_at=NOW - 3000))
        self.assertIn("running long", out)

    def test_tasks_and_queue(self):
        state = running_state(
            tasks_created=["1", "2", "3"],
            tasks_completed=["1"],
            first_task_at=NOW - 50,
            joined=[{"at": NOW - 10, "p10": 20, "p50": 60, "p90": 120}],
        )
        out = self.text(state)
        self.assertIn("1/3 tasks", out)
        self.assertIn("+1 queued", out)

    def test_segments_are_configurable(self):
        out = self.text(running_state(), segments=["elapsed"], label="")
        self.assertEqual(out, "1m 00s in")

    def test_ascii_mode(self):
        out = self.text(running_state(), ascii=True)
        self.assertNotIn("·", out)
        self.assertNotIn("≈", out)
        out.encode("ascii")

    def test_colour_and_no_color(self):
        coloured = render.format_view(render.compute(running_state(), NOW, config.DEFAULTS), config.DEFAULTS)
        self.assertIn("\033[", coloured)
        os.environ["NO_COLOR"] = "1"
        try:
            plain = render.format_view(render.compute(running_state(), NOW, config.DEFAULTS), config.DEFAULTS)
        finally:
            del os.environ["NO_COLOR"]
        self.assertNotIn("\033[", plain)

    def test_idle_summary_and_expiry(self):
        state = {
            "turn": {"status": "done"},
            "last": {"outcome": "completed", "ended_at": NOW - 60, "active_s": 245, "p50": 200},
        }
        self.assertEqual(self.text(state), "ETA last 4m 05s (est 3m)")
        self.assertEqual(self.text(state, now=NOW + 10000), "")
        self.assertEqual(self.text(state, idle="none"), "")

    def test_idle_after_interruption(self):
        state = {"turn": None, "last": {"outcome": "interrupted", "ended_at": NOW, "active_s": 5}}
        self.assertEqual(self.text(state), "ETA last turn interrupted")

    def test_nothing_to_show(self):
        self.assertEqual(self.text(None), "")
        self.assertEqual(self.text({"turn": None, "last": None}), "")


if __name__ == "__main__":
    unittest.main()
