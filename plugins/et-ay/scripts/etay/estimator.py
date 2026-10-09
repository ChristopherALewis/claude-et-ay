# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christopher Lewis
"""The estimator.

The approach is deliberately simple and explainable. When a prompt arrives,
every past completed turn is given a weight according to how similar it looks
(prompt length, kind of request, slash command, project, model, effort) and how
recent it is. The weighted durations form an empirical distribution, and the
estimate is its 10th, 50th and 90th percentiles. With little history, a weak
prior based on prompt length fills the gap and fades out as data accumulates.

While the turn runs, the status line asks a different question: given that this
turn has already taken ``elapsed`` seconds, how much longer is it likely to take?
That is answered by conditioning the same weighted sample on durations longer
than ``elapsed``, which is why a turn that has run past most of its peers shows a
growing estimate rather than a negative one.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

Sample = List[Tuple[float, float]]  # (duration seconds, weight)

# Nine standard-normal quantiles at evenly spaced probabilities, used to turn the
# log-normal prior into pseudo-observations.
_Z9 = (-1.5932, -0.9674, -0.5894, -0.2822, 0.0, 0.2822, 0.5894, 0.9674, 1.5932)

MAX_SAMPLE_POINTS = 160
MIN_DURATION = 1.0


# ---------------------------------------------------------------------------
# Weighted statistics


def weighted_quantile(sample: Sequence[Tuple[float, float]], q: float) -> Optional[float]:
    """Quantile of a weighted sample using midpoint interpolation."""
    points = sorted((v, w) for v, w in sample if w > 0 and v == v)
    if not points:
        return None
    total = sum(w for _, w in points)
    if len(points) == 1:
        return points[0][0]
    target = q * total
    cumulative = 0.0
    previous_mid = None
    previous_value = None
    for value, weight in points:
        mid = cumulative + weight / 2.0
        if mid >= target:
            if previous_mid is None:
                return value
            span = mid - previous_mid
            frac = 0.0 if span <= 0 else (target - previous_mid) / span
            return previous_value + frac * (value - previous_value)
        previous_mid, previous_value = mid, value
        cumulative += weight
    return points[-1][0]


def effective_n(weights: Iterable[float]) -> float:
    """Kish's effective sample size: how many equally weighted points this is worth."""
    ws = [w for w in weights if w > 0]
    if not ws:
        return 0.0
    square_of_sum = sum(ws) ** 2
    sum_of_squares = sum(w * w for w in ws)
    return square_of_sum / sum_of_squares if sum_of_squares else 0.0


# ---------------------------------------------------------------------------
# Prior


def prior_median(features: Dict[str, Any]) -> float:
    """A rough cold-start guess, in seconds, from the shape of the prompt."""
    chars = max(0, int(features.get("chars") or 0))
    kind = features.get("kind") or "chat"
    if kind == "notification":
        base, scale, power = 25.0, 600.0, 0.4
    elif kind == "question":
        base, scale, power = 35.0, 400.0, 0.6
    elif kind == "command":
        base, scale, power = 120.0, 400.0, 0.5
    elif kind == "task":
        base, scale, power = 120.0, 300.0, 0.7
    else:
        base, scale, power = 40.0, 300.0, 0.6
    median = base * (1.0 + chars / scale) ** power
    if features.get("broad"):
        median *= 1.6
    median *= 1.0 + 0.15 * min(int(features.get("mentions") or 0), 6)
    return float(min(max(median, 10.0), 3600.0))


def prior_sample(features: Dict[str, Any], total_weight: float, sigma: float = 1.0) -> Sample:
    if total_weight <= 0:
        return []
    median = prior_median(features)
    each = total_weight / len(_Z9)
    return [(median * math.exp(sigma * z), each) for z in _Z9]


# ---------------------------------------------------------------------------
# Similarity


def _length_kernel(a: int, b: int, bandwidth: float = 0.9) -> float:
    distance = (math.log1p(max(a, 0)) - math.log1p(max(b, 0))) / bandwidth
    return math.exp(-0.5 * distance * distance)


def similarity(current: Dict[str, Any], past: Dict[str, Any]) -> float:
    """How much a past turn should count when estimating the current one."""
    cf = current.get("f") or {}
    pf = past.get("f") or {}
    weight = _length_kernel(int(cf.get("chars") or 0), int(pf.get("chars") or 0))

    if cf.get("command"):
        weight *= 4.0 if cf.get("command") == pf.get("command") else 0.25
    elif pf.get("command"):
        weight *= 0.4

    if cf.get("kind") and pf.get("kind"):
        weight *= 1.5 if cf["kind"] == pf["kind"] else 0.6

    if bool(cf.get("broad")) != bool(pf.get("broad")):
        weight *= 0.7
    if bool(cf.get("pasted")) != bool(pf.get("pasted")):
        weight *= 0.8

    for key, same, different in (("project", 1.8, 1.0), ("model", 1.3, 0.75), ("effort", 1.3, 0.75)):
        a, b = current.get(key), past.get(key)
        if a and b:
            weight *= same if a == b else different
    return weight


def recency(age_seconds: float, half_life_days: float) -> float:
    if age_seconds <= 0:
        return 1.0
    return 0.5 ** (age_seconds / (half_life_days * 86400.0))


def usable_for_training(record: Dict[str, Any]) -> bool:
    if record.get("outcome") != "completed":
        return False
    if int(record.get("prompts") or 1) != 1:
        return False
    duration = record.get("active_s", record.get("duration_s"))
    return isinstance(duration, (int, float)) and duration >= MIN_DURATION


# ---------------------------------------------------------------------------
# Estimate at the start of a turn


def confidence_label(n_eff: float) -> str:
    if n_eff < 3:
        return "learning"
    if n_eff < 10:
        return "low"
    if n_eff < 30:
        return "medium"
    return "high"


def estimate(
    current: Dict[str, Any],
    history: Sequence[Dict[str, Any]],
    now: float,
    half_life_days: float = 45.0,
    prior_strength: float = 3.0,
) -> Dict[str, Any]:
    """Build the weighted sample and summary quantiles for a new turn.

    ``current`` carries ``f`` (prompt features) plus optional ``project``,
    ``model`` and ``effort``. ``history`` is a list of turn records as written by
    the tracker.
    """
    weighted: Sample = []
    for record in history:
        if not usable_for_training(record):
            continue
        duration = float(record.get("active_s", record.get("duration_s")))
        age = now - float(record.get("ended_at") or now)
        weight = similarity(current, record) * recency(age, half_life_days)
        if weight > 1e-6:
            weighted.append((duration, weight))

    n_eff = effective_n(w for _, w in weighted)
    # Scale history weights so they sum to the effective sample size; the prior's
    # weight is then directly comparable to "this many typical past turns".
    total = sum(w for _, w in weighted)
    if total > 0:
        weighted = [(d, w * n_eff / total) for d, w in weighted]

    prior_weight = prior_strength * max(0.1, 1.0 - n_eff / 30.0)
    sample = weighted + prior_sample(current.get("f") or {}, prior_weight)

    sample.sort(key=lambda point: point[1], reverse=True)
    sample = sample[:MAX_SAMPLE_POINTS]
    sample = [(round(d, 1), round(w, 4)) for d, w in sample]

    p10 = weighted_quantile(sample, 0.10) or 0.0
    p50 = weighted_quantile(sample, 0.50) or 0.0
    p90 = weighted_quantile(sample, 0.90) or 0.0
    return {
        "p10": round(p10, 1),
        "p50": round(p50, 1),
        "p90": round(p90, 1),
        "n_eff": round(n_eff, 2),
        "confidence": confidence_label(n_eff),
        "basis": "history" if n_eff >= 3 else "prior",
        "sample": [list(point) for point in sample],
    }


# ---------------------------------------------------------------------------
# Remaining time while a turn is running


def remaining(
    sample: Sequence[Sequence[float]],
    elapsed: float,
) -> Dict[str, Any]:
    """Quantiles of the time still to go, given the turn has run ``elapsed`` seconds."""
    tail = [(float(d) - elapsed, float(w)) for d, w in sample if float(d) > elapsed and float(w) > 0]
    tail_n = effective_n(w for _, w in tail)
    if tail and tail_n >= 1.5:
        return {
            "p10": weighted_quantile(tail, 0.10) or 0.0,
            "p50": weighted_quantile(tail, 0.50) or 0.0,
            "p90": weighted_quantile(tail, 0.90) or 0.0,
            "overdue": False,
        }
    # The turn has outlasted nearly everything comparable. Long-tailed durations
    # mean the expected time left grows with time already spent, so scale with it.
    base = max(elapsed, 1.0)
    return {
        "p10": max(5.0, 0.1 * base),
        "p50": max(15.0, 0.35 * base),
        "p90": max(45.0, 1.0 * base),
        "overdue": True,
    }


def blend_tasks(
    hist: Dict[str, Any],
    elapsed_since_first_task: float,
    created: int,
    completed: int,
) -> Dict[str, Any]:
    """Blend in progress through Claude's task list, when there is one.

    If Claude has planned five tasks and finished two in four minutes, the
    finishing rate is a strong signal. It is weighted by the fraction done, so a
    plan with one of eight tasks complete barely moves the estimate.
    """
    if created < 2 or completed < 1 or elapsed_since_first_task <= 0:
        return hist
    result = dict(hist)
    if completed >= created:
        # Everything planned is ticked off: Claude is usually wrapping up.
        for key in ("p10", "p50", "p90"):
            result[key] = min(hist[key], {"p10": 5.0, "p50": 20.0, "p90": 90.0}[key])
        result["overdue"] = False
        return result
    rate_remaining = elapsed_since_first_task / completed * (created - completed)
    weight = 0.65 * completed / created
    old_mid = max(hist["p50"], 1.0)
    new_mid = (1 - weight) * hist["p50"] + weight * rate_remaining
    factor = new_mid / old_mid
    result["p50"] = new_mid
    result["p10"] = hist["p10"] * factor
    result["p90"] = hist["p90"] * factor
    result["task_weight"] = round(weight, 3)
    return result
