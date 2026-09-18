"""Field-by-field metric definitions: unit, denominator, aggregation, censoring.

This module is the authoritative catalogue consumed by documentation tests and
by W09 report builders.  It does not compute values.
"""

from __future__ import annotations

from typing import Any


NS_PER_MS = 1_000_000

# Status codes used across metrics when a value is unavailable.
STATUS_NULL_REASONS = {
    "NO_EVENTS": "no observed events in the evaluation set",
    "NO_TRIALS": "n=0; no confidence interval",
    "NOT_DEFINED": "metric definition is not frozen for this profile",
    "INSUFFICIENT_EVENTS": "sample too small for the requested claim",
    "SINGLE_SEED": "fewer than 2 paired seeds; interval not computed",
    "UPT_DEFINITION_UNFROZEN": "session/UPT definition not frozen by W00; field forced to null",
}


def metric_definitions() -> dict[str, dict[str, Any]]:
    """Return the frozen W04 metric catalogue as a JSON-compatible dict."""

    return {
        "energy_avg_power_unit": {
            "unit": "relative_power_unit",
            "numerator": "sum of radio-interval energy clipped to [start_ns,end_ns) plus energy increments clipped to the same window",
            "denominator": "observation_window_ns = end_ns - start_ns",
            "aggregation": "single_run: energy/window; multi_seed: report per-run then mean or ratio-of-sums only with explicit estimator name",
            "censoring": "warmup backlog work still inside the window is counted; drain-phase energy is excluded from this field",
            "null_when": "window missing or non-positive",
        },
        "energy_window_ns": {
            "unit": "ns",
            "numerator": "end_ns - start_ns",
            "denominator": None,
            "aggregation": "identity",
            "censoring": "half-open [start_ns,end_ns)",
            "null_when": "window missing",
        },
        "energy_unit_ms_window": {
            "unit": "energy_unit_ms",
            "numerator": "clipped interval energy + clipped increment energy",
            "denominator": None,
            "aggregation": "sum",
            "censoring": "only material overlapping the fixed observation window",
            "null_when": "window missing",
        },
        "arrived_count": {
            "unit": "packets",
            "numerator": "PacketOutcome with arrival_ns in [cohort_start_ns, cohort_end_ns)",
            "denominator": None,
            "aggregation": "count",
            "censoring": "arrival cohort is independent of completion time",
            "null_when": "never; 0 is a valid count",
        },
        "delivered_count": {
            "unit": "packets",
            "numerator": "cohort packets with completion_ns not null",
            "denominator": None,
            "aggregation": "count",
            "censoring": "partial bit success without application completion is not delivered",
            "null_when": "never",
        },
        "pending_count": {
            "unit": "packets",
            "numerator": "cohort packets with completion_ns and drop_ns both null",
            "denominator": None,
            "aggregation": "count",
            "censoring": "includes both deadline-mature failures still pending and right-censored immature packets",
            "null_when": "never",
        },
        "dropped_count": {
            "unit": "packets",
            "numerator": "cohort packets with drop_ns not null",
            "denominator": None,
            "aggregation": "count",
            "censoring": "drop remaining_bits are not counted as delivered payload",
            "null_when": "never",
        },
        "right_censored_count": {
            "unit": "packets",
            "numerator": "cohort pending packets with deadline_ns > end_ns",
            "denominator": None,
            "aggregation": "count",
            "censoring": "excluded from PDB denominator; still in completion_rate numerator opportunity set",
            "null_when": "never",
        },
        "warmup_backlog_count": {
            "unit": "packets",
            "numerator": "packets with arrival_ns < warmup_end_ns that are not completed before warmup_end_ns",
            "denominator": None,
            "aggregation": "count",
            "censoring": "reported separately; not merged into arrival-cohort delay stats",
            "null_when": "warmup_end_ns not configured",
        },
        "drain_delivered_count": {
            "unit": "packets",
            "numerator": "packets from the arrival cohort whose completion_ns is in [end_ns, drain_end_ns)",
            "denominator": None,
            "aggregation": "count",
            "censoring": "drain completions update delivery observations only; they never enter fixed-window power",
            "null_when": "drain_end_ns not configured",
        },
        "completion_rate": {
            "unit": "probability",
            "numerator": "delivered_count (completion observed by the analysis horizon, including drain completions when configured)",
            "denominator": "arrived_count",
            "aggregation": "pooled counts ratio; seed-mean reported separately as completion_rate_seed_mean",
            "censoring": "describes fixed-horizon delivery, not final reliability; zero arrivals => null + NO_EVENTS",
            "null_when": "arrived_count == 0",
        },
        "pdb_eligible_count": {
            "unit": "packets",
            "numerator": "cohort packets with deadline_ns <= end_ns",
            "denominator": None,
            "aggregation": "count",
            "censoring": "right-censored immature packets are excluded from the PDB denominator",
            "null_when": "never",
        },
        "pdb_satisfied_count": {
            "unit": "packets",
            "numerator": "pdb-eligible packets with completion_ns not null and (completion_ns - arrival_ns) <= pdb_ns",
            "denominator": None,
            "aggregation": "count",
            "censoring": "pending past deadline and drops count as failures",
            "null_when": "pdb_ns not configured (then eligible set is undefined and ratio is null)",
        },
        "pdb_ratio": {
            "unit": "probability",
            "numerator": "pdb_satisfied_count",
            "denominator": "pdb_eligible_count",
            "aggregation": "primary = pooled counts ratio; secondary = mean of per-run pdb_ratio",
            "censoring": "deadline-mature pending counts as miss; drop counts as miss",
            "null_when": "pdb_ns not configured or pdb_eligible_count == 0",
        },
        "delay_mean_ms_delivered": {
            "unit": "ms",
            "numerator": "mean(completion_ns - arrival_ns) over delivered cohort packets",
            "denominator": "delivered_count",
            "aggregation": "single-run mean; multi-seed mean-of-run-means reported as delay_mean_ms_seed_mean",
            "censoring": "pending and dropped packets do not contribute a delay sample; never impute 0",
            "null_when": "delivered_count == 0",
        },
        "delay_p90_ms_pooled": {
            "unit": "ms",
            "numerator": "90th percentile of all delivered packet delays pooled across seeds in the comparison set",
            "denominator": "pooled delivered_count",
            "aggregation": "pooled packet quantile",
            "censoring": "delivered only",
            "null_when": "no delivered packets",
        },
        "delay_p99_ms_pooled": {
            "unit": "ms",
            "numerator": "99th percentile of pooled delivered delays",
            "denominator": "pooled delivered_count",
            "aggregation": "pooled packet quantile",
            "censoring": "delivered only",
            "null_when": "no delivered packets",
        },
        "delay_p90_ms_mean_run": {
            "unit": "ms",
            "numerator": "mean over runs of each run's delivered-delay P90",
            "denominator": "runs with at least one delivered packet",
            "aggregation": "mean run percentile (NOT pooled)",
            "censoring": "runs with zero deliveries are omitted from the mean and counted in delay_percentile_empty_runs",
            "null_when": "no run has a delivered packet",
        },
        "delay_p99_ms_mean_run": {
            "unit": "ms",
            "numerator": "mean over runs of each run's delivered-delay P99",
            "denominator": "runs with at least one delivered packet",
            "aggregation": "mean run percentile (NOT pooled)",
            "censoring": "same as delay_p90_ms_mean_run",
            "null_when": "no run has a delivered packet",
        },
        "window_goodput_mbps": {
            "unit": "Mbit/s",
            "numerator": "sum(size_bits) over packets whose completion_ns is inside the observation window (optionally only cohort packets)",
            "denominator": "observation_window_ns converted to seconds",
            "aggregation": "single-run then seed mean or ratio-of-sums with named estimator",
            "censoring": "not UPT; name must not be silently replaced",
            "null_when": "window missing or zero-length",
        },
        "upt_mbps": {
            "unit": "Mbit/s",
            "numerator": "session payload bits under the frozen session definition",
            "denominator": "session service duration under the frozen session definition",
            "aggregation": "only when W00 freezes session boundaries",
            "censoring": "n/a",
            "null_when": "session definition not frozen; always null with reason UPT_DEFINITION_UNFROZEN in W04",
        },
        "mdr": {
            "unit": "probability",
            "numerator": "H1 trials with outcome NO_WAKE (misses)",
            "denominator": "H1 trials excluding SKIPPED",
            "aggregation": "point + fixed-sample CI; separate from FAR/FDR",
            "censoring": "SKIPPED excluded from denominator",
            "null_when": "n_H1 == 0 => null + NO_TRIALS",
        },
        "far": {
            "unit": "probability",
            "numerator": "H0 trials with outcome WAKE (false alarms)",
            "denominator": "H0 trials excluding SKIPPED",
            "aggregation": "point + fixed-sample CI",
            "censoring": "SKIPPED excluded",
            "null_when": "n_H0 == 0 => null + NO_TRIALS",
        },
        "fdr": {
            "unit": "probability",
            "numerator": "H2 trials with outcome WAKE (non-target false detections)",
            "denominator": "H2 trials excluding SKIPPED",
            "aggregation": "point + fixed-sample CI",
            "censoring": "SKIPPED excluded; target+other simultaneous still counts as H1 only",
            "null_when": "n_H2 == 0 => null + NO_TRIALS",
        },
        "power_saving_gain": {
            "unit": "probability",
            "numerator": "1 - sum(case_avg_power)/sum(baseline_avg_power) over paired seeds",
            "denominator": "sum(baseline_avg_power)",
            "aggregation": "ratio of mean powers under equal seed weights; NOT mean of per-seed ratios",
            "censoring": "requires identical observation windows and matched traffic_sha256",
            "null_when": "baseline power sum is 0 or pairing checks fail",
        },
    }


def catalog_as_list() -> list[dict[str, Any]]:
    """Ordered list of {field, ...definition} for handoff tables."""

    table = metric_definitions()
    return [{"field": name, **spec} for name, spec in table.items()]
