import json
import unittest
from pathlib import Path

import helpers
from etay import config, paths, tracker
from etay.storage import read_jsonl

T0 = 1_800_000_000.0
SESSION = "session-abc"


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self._env = helpers.isolated_env()
        self.env = self._env.__enter__()
        self.data: Path = self.env["data"]
        self.cfg = config.load()

    def tearDown(self):
        self._env.__exit__(None, None, None)

    # helpers -----------------------------------------------------------------

    def send(self, event, at, **payload):
        payload.setdefault("session_id", SESSION)
        payload.setdefault("cwd", "/work/project")
        tracker.Tracker(self.data, self.cfg, T0 + at).handle(event, payload)

    def state(self):
        return tracker.read_session(self.data, SESSION)

    def history(self):
        return read_jsonl(paths.history_path(self.data))

    def run_turn(self, start, length, pid, prompt="Fix the failing test in the parser"):
        self.send("prompt", start, prompt_id=pid, prompt=prompt)
        self.send("tool", start + 1, prompt_id=pid, tool_name="Bash", duration_ms=500)
        self.send("stop", start + length, prompt_id=pid)

    # tests -------------------------------------------------------------------

    def test_simple_turn_is_recorded(self):
        self.run_turn(0, 90, "p1")
        (rec,) = self.history()
        self.assertEqual(rec["outcome"], "completed")
        self.assertEqual(rec["duration_s"], 90)
        self.assertEqual(rec["active_s"], 90)
        self.assertEqual(rec["tools"], 1)
        self.assertEqual(rec["tool_s"], 0.5)
        self.assertEqual(rec["prompts"], 1)
        self.assertEqual(self.state()["turn"]["status"], "done")
        self.assertEqual(self.state()["last"]["active_s"], 90)

    def test_estimate_attached_at_prompt_time(self):
        self.send("prompt", 0, prompt_id="p1", prompt="hello")
        est = self.state()["turn"]["estimate"]
        for key in ("p10", "p50", "p90", "sample", "confidence"):
            self.assertIn(key, est)

    def test_queued_message_joins_running_turn(self):
        self.send("prompt", 0, prompt_id="p1", prompt="Build the export feature")
        self.send("tool", 10, prompt_id="p1")
        self.send("prompt", 12, prompt_id="p1", prompt="also add a CSV option")
        turn = self.state()["turn"]
        self.assertEqual(turn["status"], "running")
        self.assertEqual(len(turn["joined"]), 1)
        self.send("stop", 100, prompt_id="p1")
        (rec,) = self.history()
        self.assertEqual(rec["prompts"], 2)
        self.assertEqual(rec["outcome"], "completed")

    def test_new_prompt_id_means_previous_was_interrupted(self):
        self.send("prompt", 0, prompt_id="p1", prompt="Long job")
        self.send("tool", 5, prompt_id="p1")
        self.send("prompt", 30, prompt_id="p2", prompt="Never mind, do this instead")
        recs = self.history()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["outcome"], "interrupted")
        self.assertEqual(recs[0]["duration_s"], 5)  # ends at its last activity, not at the next prompt
        self.assertEqual(self.state()["turn"]["prompt_id"], "p2")

    def test_abandoned_and_new_turn_get_distinct_ids(self):
        self.send("prompt", 0, prompt_id="p1", prompt="x")
        self.send("prompt", 10 * 3600, prompt_id="p1", prompt="y")
        self.send("stop", 10 * 3600 + 30, prompt_id="p1")
        latest = tracker.load_history(self.data, 100)
        self.assertEqual(sorted(r["outcome"] for r in latest), ["abandoned", "completed"])

    def test_stale_turn_is_abandoned(self):
        self.send("prompt", 0, prompt_id="p1", prompt="x")
        self.send("prompt", 10 * 3600, prompt_id="p1", prompt="y")
        recs = self.history()
        self.assertEqual(recs[0]["outcome"], "abandoned")
        self.assertEqual(recs[0]["ended_at"], T0)  # ended at last activity, not hours later
        self.assertEqual(self.state()["turn"]["status"], "running")

    def test_waiting_for_user_is_excluded(self):
        self.send("prompt", 0, prompt_id="p1", prompt="Deploy it")
        self.send("wait", 20, prompt_id="p1")
        self.send("wait", 25, prompt_id="p1")  # a second wait must not restart the clock
        self.send("tool", 80, prompt_id="p1", tool_name="AskUserQuestion")
        self.send("stop", 100, prompt_id="p1")
        (rec,) = self.history()
        self.assertEqual(rec["waiting_s"], 60)
        self.assertEqual(rec["active_s"], 40)

    def test_tasks_are_counted(self):
        self.send("prompt", 0, prompt_id="p1", prompt="Plan and do it")
        for i in ("1", "2", "3"):
            self.send("task-created", 1, prompt_id="p1", task_id=i)
        self.send("task-created", 1, prompt_id="p1", task_id="1")  # duplicate
        self.send("task-completed", 5, prompt_id="p1", task_id="1")
        self.send("task-completed", 6, prompt_id="p1", task_id="99")  # from an earlier turn
        turn = self.state()["turn"]
        self.assertEqual(turn["tasks_created"], ["1", "2", "3"])
        self.send("stop", 10, prompt_id="p1")
        (rec,) = self.history()
        self.assertEqual(rec["tasks_created"], 3)
        self.assertEqual(rec["tasks_completed"], 1)

    def test_stop_failure(self):
        self.send("prompt", 0, prompt_id="p1", prompt="x")
        self.send("stop-failure", 20, prompt_id="p1", error="rate_limit")
        self.assertEqual(self.history()[0]["outcome"], "failed")

    def test_second_stop_extends_turn_when_another_hook_blocked_stop(self):
        self.run_turn(0, 30, "p1")
        self.send("stop", 31, prompt_id="p1")  # duplicate inside the grace window: ignored
        self.assertEqual(tracker.load_history(self.data, 100)[0]["duration_s"], 30)
        self.send("tool", 60, prompt_id="p1")
        self.send("stop", 90, prompt_id="p1", stop_hook_active=True)
        latest = tracker.load_history(self.data, 100)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["duration_s"], 90)
        self.assertEqual(latest[0]["outcome"], "completed")
        self.assertTrue(latest[0]["reopened"])

    def test_background_tool_events_after_stop_are_ignored(self):
        self.run_turn(0, 100, "p1")
        self.send("tool", 110, prompt_id="p1", agent_id="bg-agent")
        self.send("tool", 120)  # no prompt id at all
        self.assertEqual(self.state()["turn"]["status"], "done")
        self.send("prompt", 1000, prompt_id="p2", prompt="next")
        outcomes = [r["outcome"] for r in tracker.load_history(self.data, 100)]
        self.assertEqual(outcomes, ["completed"])

    def test_subagent_tools_do_not_end_a_wait(self):
        self.send("prompt", 0, prompt_id="p1", prompt="x")
        self.send("wait", 10, prompt_id="p1")
        self.send("tool", 20, prompt_id="p1", agent_id="sub-1")
        self.assertEqual(self.state()["turn"]["waiting_since"], T0 + 10)
        self.send("tool", 50, prompt_id="p1")
        self.send("stop", 60, prompt_id="p1")
        self.assertEqual(self.history()[0]["waiting_s"], 40)

    def test_odd_transcript_path_does_not_lose_the_stop(self):
        for i, odd in enumerate(([1, 2], 7, {"a": 1}, None)):
            pid = f"p{i}"
            self.send("prompt", i * 100, prompt_id=pid, prompt="x")
            self.send("stop", i * 100 + 10, prompt_id=pid, transcript_path=odd)
        self.assertEqual([r["outcome"] for r in self.history()], ["completed"] * 4)

    def test_late_async_event_does_not_recreate_ended_session(self):
        self.send("prompt", 0, prompt_id="p1", prompt="x")
        self.send("session-end", 10, reason="other")
        self.send("model", 11, to_model="claude-opus-5-5")
        self.send("tool", 12, prompt_id="p1")
        self.assertIsNone(self.state())

    def test_events_for_other_prompts_are_ignored(self):
        self.send("prompt", 0, prompt_id="p1", prompt="x")
        self.send("tool", 5, prompt_id="zzz")
        self.send("stop", 6, prompt_id="zzz")
        self.assertEqual(self.state()["turn"]["tools"], 0)
        self.assertEqual(self.state()["turn"]["status"], "running")

    def test_events_without_a_turn_are_harmless(self):
        self.send("tool", 0, prompt_id="p1")
        self.send("stop", 1, prompt_id="p1")
        self.send("task-completed", 1, prompt_id="p1", task_id="1")
        self.assertEqual(self.history(), [])

    def test_compaction_does_not_end_the_turn(self):
        self.send("prompt", 0, prompt_id="p1", prompt="x")
        self.send("session-start", 50, source="compact")
        self.assertEqual(self.state()["turn"]["status"], "running")

    def test_resume_closes_a_dangling_turn(self):
        self.send("prompt", 0, prompt_id="p1", prompt="x")
        self.send("tool", 40, prompt_id="p1")
        self.send("session-start", 5000, source="resume")
        (rec,) = self.history()
        self.assertEqual(rec["outcome"], "abandoned")
        self.assertEqual(rec["duration_s"], 40)

    def test_session_end_cleans_up(self):
        self.send("prompt", 0, prompt_id="p1", prompt="x")
        self.send("session-end", 10, reason="clear")
        self.assertIsNone(self.state())
        self.assertEqual(self.history()[0]["outcome"], "abandoned")

    def test_model_tracking(self):
        self.send("session-start", 0, source="startup", model="claude-opus-5-5")
        self.send("model", 1, from_model="claude-opus-5-5", to_model="claude-sonnet-5-5")
        self.run_turn(2, 10, "p1")
        self.assertEqual(self.history()[0]["model"], "claude-sonnet-5-5")

    def test_effort_comes_from_payload(self):
        self.send("prompt", 0, prompt_id="p1", prompt="x")
        self.send("tool", 1, prompt_id="p1", effort={"level": "xhigh"})
        self.send("stop", 2, prompt_id="p1")
        self.assertEqual(self.history()[0]["effort"], "xhigh")

    def test_learning_feeds_the_next_estimate(self):
        for i in range(40):
            self.run_turn(i * 1000, 400, f"p{i}")
        self.send("prompt", 100000, prompt_id="next", prompt="Fix the failing test in the parser")
        est = self.state()["turn"]["estimate"]
        self.assertEqual(est["basis"], "history")
        self.assertTrue(330 <= est["p50"] <= 470, est)

    def test_prompt_text_never_reaches_disk(self):
        secret = "SECRET-PROMPT-TEXT-1234"
        self.send("prompt", 0, prompt_id="p1", prompt=f"please handle {secret} carefully")
        self.send("stop", 5, prompt_id="p1", last_assistant_message=f"done with {secret}")
        for path in self.data.rglob("*"):
            if path.is_file():
                self.assertNotIn(secret, path.read_text(encoding="utf-8", errors="replace"), path)

    def test_missing_session_id_is_ignored(self):
        tracker.Tracker(self.data, self.cfg, T0).handle("prompt", {"prompt": "x"})
        self.assertFalse(paths.sessions_dir(self.data).exists() and any(paths.sessions_dir(self.data).iterdir()))

    def test_without_prompt_ids_turns_still_work(self):
        self.send("prompt", 0, prompt="first")
        self.send("stop", 10)
        self.send("prompt", 20, prompt="second")
        self.send("stop", 50)
        recs = self.history()
        self.assertEqual([r["outcome"] for r in recs], ["completed", "completed"])
        self.assertEqual([r["duration_s"] for r in recs], [10, 30])

    def test_housekeeping_trims_history(self):
        hist = paths.history_path(self.data)
        lines = [json.dumps({"turn_id": str(i), "outcome": "completed"}) for i in range(50)]
        hist.write_text("\n".join(lines) + "\n" + ("x" * 200000) + "\n", encoding="utf-8")
        tracker.housekeeping(self.data, T0, max_records=10)
        kept = read_jsonl(hist)
        self.assertEqual(len(kept), 10)
        self.assertEqual(kept[-1]["turn_id"], "49")


if __name__ == "__main__":
    unittest.main()
