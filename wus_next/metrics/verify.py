"""Independent verify_run recomputation from raw records.

This module deliberately does not call W01 ledger APIs or re-import
summarize_run internals for the authoritative numbers.  It recomputes
window energy, packet bookkeeping, bit partitions and detection rates
directly from the public record lists and reports the smallest
counterexample identifiers on mismatch.
"""

from __future__ import annotations

from typing import Any

from .detection import detection_metrics
from .energy import integrate_window_energy
from .packets import packet_metrics
from .throughput import window_goodput
from .validation import classify_outcome, validate_bit_partition, validate_run_records


def _close(a: float | None, b: float | None, tol: float = 1e-9) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def verify_run(
    run: dict,
    *,
    summary: dict | None = None,
    window: dict | None = None,
    rtol: float = 1e-9,
    atol: float = 1e-9,
) -> dict:
    """Recompute metrics from raw run records and optionally diff a summary.

    ``run`` must be a contract RunOutput-like dict with intervals, increments,
    outcomes, tx, trials, and either an embedded summary or an explicit
    ``window`` argument.  When the embedded summary is absent and window is
    provided, only the recomputation report is returned (status RECOMPUTED).
    """

    if not isinstance(run, dict):
        raise TypeError("run must be a dict")

    intervals = run.get("intervals") or []
    increments = run.get("increments") or []
    outcomes = run.get("outcomes") or run.get("packets_outcomes") or []
    # Contract RunOutput uses "outcomes" for PacketOutcome and "packets" for Packet.
    if not outcomes and isinstance(run.get("outcomes"), list):
        outcomes = run["outcomes"]
    tx = run.get("tx") or []
    trials = run.get("trials") or []

    # Allow a convenience shape where "packets" already holds PacketOutcome
    # (W04 summarize_run contract argument name).  Distinguish by fields.
    if not outcomes and isinstance(run.get("packets"), list) and run["packets"]:
        sample = run["packets"][0]
        if isinstance(sample, dict) and "completion_ns" in sample and "remaining_bits" in sample:
            outcomes = run["packets"]

    structural = validate_run_records(outcomes=outcomes, tx=tx, intervals=intervals, trials=trials)
    bit_errors = validate_bit_partition(outcomes, tx)

    win = window or _window_from_run(run, summary)
    recompute: dict[str, Any] = {"structural": structural, "bit_partition_errors": bit_errors}
    mismatches: list[dict] = []

    if win is None:
        return {
            "status": "NO_WINDOW",
            "reason": "cannot locate observation window on run/summary; pass window=",
            "structural": structural,
            "bit_partition_errors": bit_errors,
            "mismatches": mismatches,
        }

    start_ns = int(win["start_ns"])
    end_ns = int(win["end_ns"])
    pdb_ns = win.get("pdb_ns")
    if pdb_ns is None and win.get("pdb_ms") is not None:
        pdb_ns = int(round(float(win["pdb_ms"]) * 1_000_000))

    energy = integrate_window_energy(intervals, increments, start_ns=start_ns, end_ns=end_ns)
    pkt = packet_metrics(
        outcomes,
        start_ns=start_ns,
        end_ns=end_ns,
        pdb_ns=pdb_ns,
        warmup_end_ns=win.get("warmup_end_ns"),
        drain_end_ns=win.get("drain_end_ns"),
    )
    det = detection_metrics(trials)
    gut = window_goodput(outcomes, start_ns=start_ns, end_ns=end_ns)

    # Independent bit totals (recomputed without trusting outcome.remaining_bits alone).
    success_bits = _successful_unique_bits(outcomes, tx)
    recompute["energy"] = energy
    recompute["packets"] = {
        k: pkt[k]
        for k in (
            "arrived_count",
            "delivered_count",
            "pending_count",
            "dropped_count",
            "right_censored_count",
            "completion_rate",
            "pdb_eligible_count",
            "pdb_satisfied_count",
            "pdb_ratio",
            "delay_mean_ms_delivered",
            "delay_p90_ms_run",
            "delay_p99_ms_run",
        )
    }
    recompute["detection"] = {
        "mdr_point": det["mdr"]["point"],
        "far_point": det["far"]["point"],
        "fdr_point": det["fdr"]["point"],
        "n_H0": det["n_H0"],
        "n_H1": det["n_H1"],
        "n_H2": det["n_H2"],
        "skipped_total": det["skipped_total"],
    }
    recompute["goodput"] = gut["window_goodput_mbps"]
    recompute["successful_unique_bits_by_packet"] = success_bits

    # Consistency: arrived = delivered + pending + dropped (within this module's definitions)
    if pkt["arrived_count"] != pkt["delivered_count"] + pkt["pending_count"] + pkt["dropped_count"]:
        mismatches.append(
            {
                "field": "arrived_partition",
                "expected": pkt["arrived_count"],
                "actual": pkt["delivered_count"] + pkt["pending_count"] + pkt["dropped_count"],
                "counterexample_packet_ids": (
                    pkt["delivered_packet_ids"] + pkt["pending_packet_ids"] + pkt["dropped_packet_ids"]
                )[:3],
            }
        )

    if summary is not None:
        mismatches.extend(_diff_summary(summary, energy, pkt, det, gut, rtol=rtol, atol=atol))

    status = "PASS"
    if not structural["valid"] or bit_errors:
        status = "FAIL_STRUCTURAL"
    elif mismatches:
        status = "FAIL_MISMATCH"
    elif summary is None:
        status = "RECOMPUTED"

    return {
        "status": status,
        "window": {"start_ns": start_ns, "end_ns": end_ns, "pdb_ns": pdb_ns},
        "recomputed": recompute,
        "mismatches": mismatches,
        "structural_valid": structural["valid"],
        "bit_partition_ok": not bit_errors,
    }


def _window_from_run(run: dict, summary: dict | None) -> dict | None:
    for source in (run, summary or {}, run.get("summary") or {}, run.get("metadata") or {}):
        if not isinstance(source, dict):
            continue
        win = source.get("metrics_window") or source.get("window")
        if isinstance(win, dict) and {"start_ns", "end_ns"} <= set(win):
            return {
                "start_ns": int(win["start_ns"]),
                "end_ns": int(win["end_ns"]),
                "warmup_end_ns": win.get("warmup_end_ns"),
                "drain_end_ns": win.get("drain_end_ns"),
                "pdb_ns": win.get("pdb_ns"),
                "pdb_ms": win.get("pdb_ms"),
            }
        profile_window = None
        if isinstance(source.get("profile"), dict):
            profile_window = source["profile"].get("metrics_window")
        if isinstance(profile_window, dict) and {"start_ns", "end_ns"} <= set(profile_window):
            return {
                "start_ns": int(profile_window["start_ns"]),
                "end_ns": int(profile_window["end_ns"]),
                "warmup_end_ns": profile_window.get("warmup_end_ns"),
                "drain_end_ns": profile_window.get("drain_end_ns"),
                "pdb_ns": profile_window.get("pdb_ns"),
                "pdb_ms": profile_window.get("pdb_ms"),
            }
    # Fallback: infer from interval coverage only when unique and complete.
    intervals = run.get("intervals") or []
    if intervals:
        starts = [int(i["start_ns"]) for i in intervals]
        ends = [int(i["end_ns"]) for i in intervals]
        if len({min(starts)}) == 1 and len({max(ends)}) == 1 and max(ends) > min(starts):
            return {"start_ns": min(starts), "end_ns": max(ends), "warmup_end_ns": None, "drain_end_ns": None, "pdb_ns": None}
    return None


def _successful_unique_bits(outcomes: list[dict], tx: list[dict]) -> dict[str, int]:
    spans: dict[str, list[tuple[int, int]]] = {}
    for record in tx:
        if not record.get("success"):
            continue
        offset = int(record.get("offset_bits", 0))
        payload = int(record.get("payload_bits", 0))
        if payload <= 0:
            continue
        spans.setdefault(record["packet_id"], []).append((offset, offset + payload))
    out: dict[str, int] = {}
    for pid, list_spans in spans.items():
        list_spans.sort()
        total = 0
        cur_s, cur_e = list_spans[0]
        for start, end in list_spans[1:]:
            if start <= cur_e:
                cur_e = max(cur_e, end)
            else:
                total += cur_e - cur_s
                cur_s, cur_e = start, end
        total += cur_e - cur_s
        out[pid] = total
    for outcome in outcomes:
        out.setdefault(outcome["packet_id"], 0)
    return out


def _diff_summary(
    summary: dict,
    energy: dict,
    pkt: dict,
    det: dict,
    gut: dict,
    *,
    rtol: float,
    atol: float,
) -> list[dict]:
    mismatches: list[dict] = []

    def cmp_num(path: str, expected: Any, actual: Any) -> None:
        if expected is None and actual is None:
            return
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            if not _close(float(expected), float(actual), tol=max(rtol * max(abs(float(expected)), abs(float(actual))), atol)):
                mismatches.append({"field": path, "summary": expected, "recomputed": actual})
            return
        if expected != actual:
            mismatches.append({"field": path, "summary": expected, "recomputed": actual})

    energy_block = summary.get("energy") or {}
    if energy_block:
        cmp_num("energy.energy_unit_ms", energy_block.get("energy_unit_ms"), energy.get("energy_unit_ms"))
        cmp_num("energy.avg_power_unit", energy_block.get("avg_power_unit"), energy.get("avg_power_unit"))
        cmp_num("energy.window_ns", energy_block.get("window_ns"), energy.get("window_ns"))

    packet_block = summary.get("packets") or {}
    if packet_block:
        for key in (
            "arrived_count",
            "delivered_count",
            "pending_count",
            "dropped_count",
            "right_censored_count",
            "completion_rate",
            "pdb_eligible_count",
            "pdb_satisfied_count",
            "pdb_ratio",
            "delay_mean_ms_delivered",
            "delay_p90_ms_run",
            "delay_p99_ms_run",
        ):
            if key in packet_block:
                cmp_num(f"packets.{key}", packet_block.get(key), pkt.get(key))

    detection_block = summary.get("detection") or {}
    if detection_block:
        for key, nested in (
            ("far", "far"),
            ("mdr", "mdr"),
            ("fdr", "fdr"),
        ):
            block = detection_block.get(key)
            if isinstance(block, dict):
                cmp_num(f"detection.{key}.point", block.get("point"), det[nested]["point"])
                cmp_num(f"detection.{key}.n", block.get("n"), det[nested]["n"])
        for key in ("n_H0", "n_H1", "n_H2", "skipped_total"):
            if key in detection_block:
                cmp_num(f"detection.{key}", detection_block.get(key), det.get(key))

    goodput_block = summary.get("goodput") or {}
    if isinstance(goodput_block, dict) and "window_goodput_mbps" in goodput_block:
        cmp_num("goodput.window_goodput_mbps", goodput_block.get("window_goodput_mbps"), gut.get("window_goodput_mbps"))

    # UPT must remain null
    upt = summary.get("upt") or {}
    if upt.get("upt_mbps") is not None:
        mismatches.append(
            {
                "field": "upt.upt_mbps",
                "summary": upt.get("upt_mbps"),
                "recomputed": None,
                "note": "UPT must be null until session definition is frozen",
            }
        )

    return mismatches
