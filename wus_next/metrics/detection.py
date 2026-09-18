"""Detection error metrics with independent H0/H1/H2 denominators."""

from __future__ import annotations

from .statistical import detection_interval


def detection_metrics(
    trials: list[dict],
    *,
    ci_method: str = "wilson",
    confidence: float = 0.95,
) -> dict:
    """Compute MDR/FAR/FDR from DetectionTrial records.

    Denominators are independent per condition. SKIPPED outcomes are excluded
    from all three denominators and reported separately. Training/threshold
    trials must already be filtered out (validation rejects them in eval sets).
    """

    counts = {
        "H0": {"n": 0, "false_alarm": 0, "skipped": 0},
        "H1": {"n": 0, "miss": 0, "skipped": 0},
        "H2": {"n": 0, "false_detect": 0, "skipped": 0},
    }
    for trial in trials:
        cond = trial.get("condition")
        outcome = trial.get("outcome")
        if cond not in counts:
            continue
        if outcome == "SKIPPED":
            counts[cond]["skipped"] += 1
            continue
        if outcome not in {"WAKE", "NO_WAKE"}:
            continue
        counts[cond]["n"] += 1
        if cond == "H0" and outcome == "WAKE":
            counts[cond]["false_alarm"] += 1
        elif cond == "H1" and outcome == "NO_WAKE":
            counts[cond]["miss"] += 1
        elif cond == "H2" and outcome == "WAKE":
            counts[cond]["false_detect"] += 1

    far = detection_interval(counts["H0"]["false_alarm"], counts["H0"]["n"], ci_method, confidence)
    mdr = detection_interval(counts["H1"]["miss"], counts["H1"]["n"], ci_method, confidence)
    fdr = detection_interval(counts["H2"]["false_detect"], counts["H2"]["n"], ci_method, confidence)

    return {
        "n_H0": counts["H0"]["n"],
        "n_H1": counts["H1"]["n"],
        "n_H2": counts["H2"]["n"],
        "skipped_H0": counts["H0"]["skipped"],
        "skipped_H1": counts["H1"]["skipped"],
        "skipped_H2": counts["H2"]["skipped"],
        "skipped_total": counts["H0"]["skipped"] + counts["H1"]["skipped"] + counts["H2"]["skipped"],
        "far": far,
        "mdr": mdr,
        "fdr": fdr,
        "ci_method": ci_method,
        "confidence": confidence,
        "denominator_note": "H0/H1/H2 use independent denominators; SKIPPED excluded; target+other simultaneous is H1 only",
    }
