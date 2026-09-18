"""W04 metrics: statistics, KPIs and independent run verification.

Public surface follows the frozen W00 contract:

* ``summarize_run(profile, intervals, increments, packets, tx, trials)``
* ``compare_paired(runs, baseline_id, bootstrap_seed)``

``packets`` in ``summarize_run`` is the PacketOutcome list.
"""

from __future__ import annotations

from .compare import compare_paired
from .definitions import catalog_as_list, metric_definitions
from .detection import detection_metrics
from .energy import integrate_window_energy, split_warmup_drain_windows
from .packets import aggregate_multi_run, build_arrival_cohort, packet_metrics
from .statistical import (
    clopper_pearson,
    detection_interval,
    mean,
    one_sided_error_upper,
    paired_bootstrap_gain,
    percentile,
    proportion_point,
    ratio_of_sums,
    wilson,
)
from .summarize import summarize_multi_run, summarize_run
from .throughput import UPT_NULL_REASON, upt_placeholder, window_goodput
from .validation import (
    MetricsValidationError,
    assert_valid_run_records,
    classify_outcome,
    validate_bit_partition,
    validate_detection_trials,
    validate_interval_nonoverlap,
    validate_outcome_conservation,
    validate_packet_ids_unique,
    validate_run_records,
)
from .verify import verify_run

__all__ = [
    "MetricsValidationError",
    "UPT_NULL_REASON",
    "aggregate_multi_run",
    "assert_valid_run_records",
    "build_arrival_cohort",
    "catalog_as_list",
    "classify_outcome",
    "clopper_pearson",
    "compare_paired",
    "detection_interval",
    "detection_metrics",
    "integrate_window_energy",
    "mean",
    "metric_definitions",
    "one_sided_error_upper",
    "packet_metrics",
    "paired_bootstrap_gain",
    "percentile",
    "proportion_point",
    "ratio_of_sums",
    "split_warmup_drain_windows",
    "summarize_multi_run",
    "summarize_run",
    "upt_placeholder",
    "validate_bit_partition",
    "validate_detection_trials",
    "validate_interval_nonoverlap",
    "validate_outcome_conservation",
    "validate_packet_ids_unique",
    "validate_run_records",
    "verify_run",
    "wilson",
    "window_goodput",
]
