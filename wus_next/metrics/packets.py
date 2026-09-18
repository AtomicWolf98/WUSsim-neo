"""Arrival-cohort packet metrics: PDB, completion, delays, censoring."""

from __future__ import annotations

from typing import Sequence

from .statistical import mean, percentile
from .validation import classify_outcome

NS_PER_MS = 1_000_000


def _in_half_open(t: int | None, start: int, end: int) -> bool:
    if t is None:
        return False
    return start <= t < end


def build_arrival_cohort(
    outcomes: list[dict],
    *,
    start_ns: int,
    end_ns: int,
    warmup_end_ns: int | None = None,
) -> dict:
    """Split PacketOutcomes into cohort / warmup-backlog / out-of-window."""

    cohort_start = start_ns if warmup_end_ns is None else int(warmup_end_ns)
    if cohort_start < start_ns or cohort_start > end_ns:
        raise ValueError("warmup_end_ns must lie inside [start_ns, end_ns]")

    cohort: list[dict] = []
    warmup_backlog: list[dict] = []
    pre_window_completed: list[dict] = []
    for outcome in outcomes:
        arrival = outcome["arrival_ns"]
        status = classify_outcome(outcome)
        if cohort_start <= arrival < end_ns:
            cohort.append(outcome)
        elif start_ns <= arrival < cohort_start:
            # Arrived during warmup; backlog if still open at cohort boundary.
            completion = outcome.get("completion_ns")
            drop = outcome.get("drop_ns")
            if completion is None and drop is None:
                warmup_backlog.append(outcome)
            elif completion is not None and completion >= cohort_start:
                warmup_backlog.append(outcome)
            else:
                pre_window_completed.append(outcome)
        # arrivals outside [start_ns, end_ns) are ignored by the fixed window
    return {
        "cohort": cohort,
        "warmup_backlog": warmup_backlog,
        "pre_window_settled": pre_window_completed,
        "cohort_start_ns": cohort_start,
        "cohort_end_ns": end_ns,
    }


def packet_metrics(
    outcomes: list[dict],
    *,
    start_ns: int,
    end_ns: int,
    pdb_ns: int | None = None,
    warmup_end_ns: int | None = None,
    drain_end_ns: int | None = None,
    count_drain_in_completion: bool = True,
) -> dict:
    """Compute cohort counts, PDB, completion_rate and delay fields.

    PDB denominator includes only cohort packets with deadline_ns <= end_ns.
    Right-censored (pending, deadline > end_ns) are excluded from PDB but
    remain in arrived/pending/completion_rate bookkeeping.
    Past-deadline pending and drops count as PDB failures when eligible.
    Delays are defined only for delivered packets.
    """

    if end_ns <= start_ns:
        raise ValueError("end_ns must be > start_ns")
    horizon = end_ns if drain_end_ns is None else int(drain_end_ns)
    if horizon < end_ns:
        raise ValueError("drain_end_ns must be >= end_ns")

    split = build_arrival_cohort(outcomes, start_ns=start_ns, end_ns=end_ns, warmup_end_ns=warmup_end_ns)
    cohort = split["cohort"]

    delivered: list[dict] = []
    pending: list[dict] = []
    dropped: list[dict] = []
    for outcome in cohort:
        status = classify_outcome(outcome)
        completion = outcome.get("completion_ns")
        if status == "delivered":
            # delivery observed at completion time; if drain configured and
            # completion is after end_ns, still a delivery for completion_rate
            # when count_drain_in_completion is True.
            if completion is not None and completion < horizon:
                delivered.append(outcome)
            elif completion is not None and completion >= horizon and not count_drain_in_completion:
                pending.append(outcome)
            else:
                delivered.append(outcome)
        elif status == "dropped":
            dropped.append(outcome)
        else:
            pending.append(outcome)

    drain_delivered = [
        o
        for o in delivered
        if o.get("completion_ns") is not None and end_ns <= o["completion_ns"] < horizon
    ]

    right_censored = [
        o
        for o in pending
        if o.get("completion_ns") is None and o.get("drop_ns") is None and o.get("deadline_ns", 0) > end_ns
    ]

    arrived = len(cohort)
    n_delivered = len(delivered)
    n_pending = len(pending)
    n_dropped = len(dropped)

    delays_ms = [
        (o["completion_ns"] - o["arrival_ns"]) / NS_PER_MS
        for o in delivered
        if o.get("completion_ns") is not None
    ]

    # PDB
    if pdb_ns is None:
        pdb_eligible: list[dict] = []
        pdb_satisfied = 0
        pdb_ratio = None
        pdb_status = "NOT_CONFIGURED"
        pdb_reason = "pdb_ns is null; PDB fields are null"
    else:
        pdb_eligible = [o for o in cohort if o.get("deadline_ns", 0) <= end_ns]
        pdb_satisfied = 0
        for o in pdb_eligible:
            completion = o.get("completion_ns")
            if completion is None:
                continue
            if (completion - o["arrival_ns"]) <= pdb_ns:
                pdb_satisfied += 1
        if not pdb_eligible:
            pdb_ratio = None
            pdb_status = "NO_EVENTS"
            pdb_reason = "no deadline-eligible packets in cohort"
        else:
            pdb_ratio = pdb_satisfied / len(pdb_eligible)
            pdb_status = "OK"
            pdb_reason = None

    if arrived == 0:
        completion_rate = None
        completion_status = "NO_EVENTS"
        completion_reason = "zero arrivals in cohort; delivery rate is null, not 1.0"
    else:
        completion_rate = n_delivered / arrived
        completion_status = "OK"
        completion_reason = None

    delay_mean = mean(delays_ms) if delays_ms else None
    delay_p90 = percentile(delays_ms, 90) if delays_ms else None
    delay_p99 = percentile(delays_ms, 99) if delays_ms else None

    return {
        "arrived_count": arrived,
        "delivered_count": n_delivered,
        "pending_count": n_pending,
        "dropped_count": n_dropped,
        "right_censored_count": len(right_censored),
        "warmup_backlog_count": len(split["warmup_backlog"]),
        "drain_delivered_count": len(drain_delivered),
        "completion_rate": completion_rate,
        "completion_rate_status": completion_status,
        "completion_rate_reason": completion_reason,
        "pdb_ns": pdb_ns,
        "pdb_eligible_count": len(pdb_eligible) if pdb_ns is not None else None,
        "pdb_satisfied_count": pdb_satisfied if pdb_ns is not None else None,
        "pdb_ratio": pdb_ratio,
        "pdb_status": pdb_status,
        "pdb_reason": pdb_reason,
        "delay_mean_ms_delivered": delay_mean,
        "delay_p90_ms_run": delay_p90,
        "delay_p99_ms_run": delay_p99,
        "delay_sample_count": len(delays_ms),
        "delay_status": "OK" if delays_ms else "NO_EVENTS",
        "delay_reason": None if delays_ms else "no delivered packets; delay is null",
        "delivered_packet_ids": [o["packet_id"] for o in delivered],
        "pending_packet_ids": [o["packet_id"] for o in pending],
        "dropped_packet_ids": [o["packet_id"] for o in dropped],
        "right_censored_packet_ids": [o["packet_id"] for o in right_censored],
        "delivered_delays_ms": delays_ms,
    }


def aggregate_multi_run(summaries: Sequence[dict]) -> dict:
    """Pool counts and separate pooled vs mean-run percentiles.

    Input is a sequence of single-run packet_metrics dicts (each may carry
    delivered_delays_ms).  Never silently overwrites one aggregation with
    another: both fields are always present.
    """

    if not summaries:
        return {
            "runs": 0,
            "arrived_count": 0,
            "delivered_count": 0,
            "pending_count": 0,
            "dropped_count": 0,
            "right_censored_count": 0,
            "pdb_eligible_count": 0,
            "pdb_satisfied_count": 0,
            "pdb_ratio_pooled": None,
            "pdb_ratio_seed_mean": None,
            "completion_rate_pooled": None,
            "completion_rate_seed_mean": None,
            "delay_mean_ms_pooled": None,
            "delay_p90_ms_pooled": None,
            "delay_p99_ms_pooled": None,
            "delay_p90_ms_mean_run": None,
            "delay_p99_ms_mean_run": None,
            "delay_percentile_empty_runs": 0,
            "status": "NO_EVENTS",
            "reason": "no runs supplied",
        }

    pooled_delays: list[float] = []
    run_p90: list[float] = []
    run_p99: list[float] = []
    empty_delay_runs = 0
    completion_rates: list[float] = []
    pdb_ratios: list[float] = []

    arrived = delivered = pending = dropped = right_censored = 0
    pdb_elig = pdb_sat = 0
    pdb_configured = False

    for s in summaries:
        arrived += s["arrived_count"]
        delivered += s["delivered_count"]
        pending += s["pending_count"]
        dropped += s["dropped_count"]
        right_censored += s["right_censored_count"]
        if s.get("pdb_ns") is not None:
            pdb_configured = True
            pdb_elig += s.get("pdb_eligible_count") or 0
            pdb_sat += s.get("pdb_satisfied_count") or 0
            if s.get("pdb_ratio") is not None:
                pdb_ratios.append(s["pdb_ratio"])
        if s.get("completion_rate") is not None:
            completion_rates.append(s["completion_rate"])
        delays = s.get("delivered_delays_ms") or []
        if delays:
            pooled_delays.extend(delays)
            if s.get("delay_p90_ms_run") is not None:
                run_p90.append(s["delay_p90_ms_run"])
            if s.get("delay_p99_ms_run") is not None:
                run_p99.append(s["delay_p99_ms_run"])
        else:
            empty_delay_runs += 1

    pdb_ratio_pooled = (pdb_sat / pdb_elig) if pdb_configured and pdb_elig > 0 else None
    completion_pooled = (delivered / arrived) if arrived > 0 else None

    return {
        "runs": len(summaries),
        "arrived_count": arrived,
        "delivered_count": delivered,
        "pending_count": pending,
        "dropped_count": dropped,
        "right_censored_count": right_censored,
        "pdb_eligible_count": pdb_elig if pdb_configured else None,
        "pdb_satisfied_count": pdb_sat if pdb_configured else None,
        "pdb_ratio_pooled": pdb_ratio_pooled,
        "pdb_ratio_seed_mean": mean(pdb_ratios) if pdb_ratios else None,
        "completion_rate_pooled": completion_pooled,
        "completion_rate_seed_mean": mean(completion_rates) if completion_rates else None,
        "delay_mean_ms_pooled": mean(pooled_delays) if pooled_delays else None,
        "delay_p90_ms_pooled": percentile(pooled_delays, 90) if pooled_delays else None,
        "delay_p99_ms_pooled": percentile(pooled_delays, 99) if pooled_delays else None,
        "delay_p90_ms_mean_run": mean(run_p90) if run_p90 else None,
        "delay_p99_ms_mean_run": mean(run_p99) if run_p99 else None,
        "delay_percentile_empty_runs": empty_delay_runs,
        "status": "OK" if arrived > 0 else "NO_EVENTS",
        "reason": None if arrived > 0 else "zero arrivals; rates are null, not 1.0",
    }
