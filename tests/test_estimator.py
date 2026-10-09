import math
import unittest

import helpers  # noqa: F401
from etay import estimator

NOW = 1_800_000_000.0


def record(duration, chars=60, kind="task", command=None, project="p", age_days=1.0, outcome="completed", prompts=1):
    return {
        "turn_id": f"t{duration}-{chars}-{age_days}",
        "outcome": outcome,
        "prompts": prompts,
        "active_s": duration,
        "duration_s": duration,
        "ended_at": NOW - age_days * 86400,
        "project": project,
        "f": {"chars": chars, "kind": kind, "command": command},
    }


def context(chars=60, kind="task", command=None, project="p"):
    return {"f": {"chars": chars, "kind": kind, "command": command}, "project": project}


class WeightedStatsTests(unittest.TestCase):
    def test_quantile_unweighted_median(self):
        self.assertAlmostEqual(estimator.weighted_quantile([(1, 1), (2, 1), (3, 1)], 0.5), 2.0)

    def test_quantile_respects_weights(self):
        heavy_high = estimator.weighted_quantile([(10, 1), (100, 9)], 0.5)
        self.assertGreater(heavy_high, 50)

    def test_quantile_edges(self):
        self.assertIsNone(estimator.weighted_quantile([], 0.5))
        self.assertEqual(estimator.weighted_quantile([(5, 1)], 0.9), 5)
        self.assertEqual(estimator.weighted_quantile([(5, 1), (9, 1)], 0.0), 5)
        self.assertEqual(estimator.weighted_quantile([(5, 1), (9, 1)], 1.0), 9)

    def test_effective_n(self):
        self.assertAlmostEqual(estimator.effective_n([1, 1, 1, 1]), 4.0)
        self.assertAlmostEqual(estimator.effective_n([1, 0, 0]), 1.0)
        self.assertLess(estimator.effective_n([10, 1, 1]), 3.0)
        self.assertEqual(estimator.effective_n([]), 0.0)


class PriorTests(unittest.TestCase):
    def test_longer_prompts_get_longer_priors(self):
        short = estimator.prior_median({"chars": 40, "kind": "task"})
        long = estimator.prior_median({"chars": 4000, "kind": "task"})
        self.assertGreater(long, short)

    def test_questions_are_quicker_than_tasks(self):
        self.assertLess(
            estimator.prior_median({"chars": 200, "kind": "question"}),
            estimator.prior_median({"chars": 200, "kind": "task"}),
        )

    def test_prior_is_bounded(self):
        self.assertLessEqual(
            estimator.prior_median({"chars": 10**7, "kind": "task", "broad": True, "mentions": 50}), 3600
        )
        self.assertGreaterEqual(estimator.prior_median({"chars": 0}), 10)


class EstimateTests(unittest.TestCase):
    def test_cold_start_uses_prior(self):
        est = estimator.estimate(context(), [], now=NOW)
        self.assertEqual(est["basis"], "prior")
        self.assertEqual(est["confidence"], "learning")
        self.assertGreater(est["p90"], est["p50"])
        self.assertGreater(est["p50"], est["p10"])

    def test_history_dominates_once_plentiful(self):
        history = [record(300 + i) for i in range(60)]
        est = estimator.estimate(context(), history, now=NOW)
        self.assertEqual(est["basis"], "history")
        self.assertIn(est["confidence"], ("medium", "high"))
        self.assertTrue(250 <= est["p50"] <= 360, est)

    def test_same_command_outweighs_others(self):
        history = [record(30, command="quick") for _ in range(20)] + [record(900, command="deep") for _ in range(20)]
        deep = estimator.estimate(context(command="deep", kind="command"), history, now=NOW)
        quick = estimator.estimate(context(command="quick", kind="command"), history, now=NOW)
        self.assertGreater(deep["p50"], 500)
        self.assertLess(quick["p50"], 120)

    def test_prompt_length_matters(self):
        history = [record(40, chars=30) for _ in range(20)] + [record(800, chars=3000) for _ in range(20)]
        short = estimator.estimate(context(chars=35), history, now=NOW)
        long = estimator.estimate(context(chars=2500), history, now=NOW)
        self.assertLess(short["p50"], long["p50"])

    def test_recent_history_counts_more(self):
        history = [record(60, age_days=1) for _ in range(15)] + [record(600, age_days=400) for _ in range(15)]
        est = estimator.estimate(context(), history, now=NOW, half_life_days=30)
        self.assertLess(est["p50"], 200)

    def test_excludes_unusable_records(self):
        history = [record(5000, outcome="interrupted") for _ in range(30)]
        history += [record(5000, prompts=3) for _ in range(30)]
        history += [record(0.2) for _ in range(30)]
        est = estimator.estimate(context(), history, now=NOW)
        self.assertEqual(est["basis"], "prior")

    def test_sample_is_capped(self):
        history = [record(100 + i, chars=60 + i) for i in range(500)]
        est = estimator.estimate(context(), history, now=NOW)
        self.assertLessEqual(len(est["sample"]), estimator.MAX_SAMPLE_POINTS)


class RemainingTests(unittest.TestCase):
    sample = [[60, 1], [120, 1], [180, 1], [240, 1], [300, 1]]

    def test_at_start(self):
        left = estimator.remaining(self.sample, 0)
        self.assertFalse(left["overdue"])
        self.assertAlmostEqual(left["p50"], 180, delta=1)

    def test_conditional_on_elapsed(self):
        left = estimator.remaining(self.sample, 150)
        # Remaining durations are 30, 90 and 150 seconds.
        self.assertAlmostEqual(left["p50"], 90, delta=1)
        self.assertFalse(left["overdue"])

    def test_overdue_grows_with_elapsed(self):
        a = estimator.remaining(self.sample, 400)
        b = estimator.remaining(self.sample, 1200)
        self.assertTrue(a["overdue"])
        self.assertGreater(b["p50"], a["p50"])

    def test_empty_sample(self):
        left = estimator.remaining([], 30)
        self.assertTrue(left["overdue"])
        self.assertTrue(math.isfinite(left["p50"]))


class TaskBlendTests(unittest.TestCase):
    hist = {"p10": 60.0, "p50": 300.0, "p90": 900.0, "overdue": False}

    def test_no_tasks_no_change(self):
        self.assertEqual(estimator.blend_tasks(self.hist, 100, 0, 0), self.hist)
        self.assertEqual(estimator.blend_tasks(self.hist, 100, 5, 0), self.hist)

    def test_fast_progress_pulls_estimate_down(self):
        blended = estimator.blend_tasks(self.hist, 60, 4, 3)  # 20s per task, one left
        self.assertLess(blended["p50"], self.hist["p50"])
        self.assertLess(blended["p90"], self.hist["p90"])

    def test_slow_progress_pushes_estimate_up(self):
        blended = estimator.blend_tasks(self.hist, 600, 4, 2)  # 300s per task, two left
        self.assertGreater(blended["p50"], self.hist["p50"])

    def test_all_done_means_wrapping_up(self):
        blended = estimator.blend_tasks(self.hist, 600, 3, 3)
        self.assertLessEqual(blended["p50"], 20)


if __name__ == "__main__":
    unittest.main()
