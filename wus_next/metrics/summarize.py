"""summarize_run: single entry point that assembles the W04 summary."""

from __future__ import annotations

from typing import Any

from .detection import detection_metrics
from .energy import integrate_window_energy, split_warmup_drain_windows
from .packets import aggregate_multi_run, packet_metrics
from .throughput import UPT_NULL_REASON, upt_placeholder, window_goodput
from .validation import assert_valid_run_records, validate_run_records


def _extract_window(profile: dict) -> tuple[dict | None, str | None]:
    """Read observation window from profile without inventing defaults.

    Accepted locations (first match wins):
      profile["metrics_window"]
      profile["parameters"]["observation"]["window"]  (raw dict or evidence leaf)
    """

    window = profile.get("metrics_window")
    if isinstance(window, dict) and {"start_ns", "end_ns"} <= set(window):
        return {
            "start_ns": int(window["start_ns"]),
            "end_ns": int(window["end_ns"]),
            "warmup_end_ns": None if window.get("warmup_end_ns") is None else int(window["warmup_end_ns"]),
            "drain_end_ns": None if window.get("drain_end_ns") is None else int(window["drain_end_ns"]),
            "pdb_ms": window.get("pdb_ms"),
        }, None

    params = profile.get("parameters") if isinstance(profile.get("parameters"), dict) else {}
    observation = params.get("observation") if isinstance(params.get("observation"), dict) else None
    if isinstance(observation, dict):
        raw = observation.get("window") if isinstance(observation.get("window"), dict) else observation

        def leaf_value(node: Any) -> Any:
            if isinstance(node, dict) and "value" in node:
                return node["value"]
            return node

        start = leaf_value(raw.get("start_ns")) if isinstance(raw, dict) else None
        end = leaf_value(raw.get("end_ns")) if isinstance(raw, dict) else None
        if start is not None and end is not None:
            return {
                "start_ns": int(start),
                "end_ns": int(end),
                "warmup_end_ns": None if leaf_value(raw.get("warmup_end_ns")) is None else int(leaf_value(raw.get("warmup_end_ns"))),
                "drain_end_ns": None if leaf_value(raw.get("drain_end_ns")) is None else int(leaf_value(raw.get("drain_end_ns"))),
                "pdb_ms": leaf_value(raw.get("pdb_ms")) if isinstance(raw, dict) else None,
            }, None

    return None, "observation window not present in profile; metrics_window or parameters.observation required"


def summarize_run(
    profile: dict,
    intervals: list[dict],
    increments: list[dict],
    packets: list[dict],
    tx: list[dict],
    trials: list[dict],
) -> dict:
    """Summarize one run.

    Contract signature (W00 contract-v1).  ``packets`` is the PacketOutcome
    list.  Returns a JSON-compatible summary dict.  Structural violations
    raise MetricsValidationError; missing window raises ValueError via the
    returned error payload when validate_only is not used.
    """

    if not isinstance(profile, dict):
        raise TypeError("profile must be a dict")

    validation = validate_run_records(
        outcomes=packets,
        tx=tx,
        intervals=intervals,
        trials=trials,
    )

    window, window_err = _extract_window(profile)
    base = {
        "contract_version": "1.0",
        "module": "W04",
        "profile_id": profile.get("profile_id"),
        "seed": profile.get("seed"),
        "experiment_id": profile.get("experiment_id"),
        "baseline_id": profile.get("baseline_id"),
        "case": profile.get("case") or profile.get("case_id"),
        "traffic_sha256": profile.get("traffic_sha256"),
        "validation": validation,
        "upt": upt_placeholder(),
    }

    if window_err is not None:
        base.update(
            {
                "status": "MISSING_WINDOW",
                "reason": window_err,
                "energy": None,
                "packets": None,
                "detection": None,
                "goodput": None,
            }
        )
        return base

    if not validation["valid"]:
        base.update(
            {
                "status": "INVALID_RECORDS",
                "reason": "run records failed conservation/uniqueness checks",
                "energy": None,
                "packets": None,
                "detection": None,
                "goodput": None,
            }
        )
        return base

    start_ns = window["start_ns"]
    end_ns = window["end_ns"]
    warmup_end_ns = window.get("warmup_end_ns")
    drain_end_ns = window.get("drain_end_ns")
    pdb_ms = window.get("pdb_ms")
    pdb_ns = None if pdb_ms is None else int(round(float(pdb_ms) * 1_000_000))

    energy = integrate_window_energy(intervals, increments, start_ns=start_ns, end_ns=end_ns)
    windows = split_warmup_drain_windows(
        start_ns=start_ns,
        end_ns=end_ns,
        warmup_end_ns=warmup_end_ns,
        drain_end_ns=drain_end_ns,
    )
    pkt = packet_metrics(
        packets,
        start_ns=start_ns,
        end_ns=end_ns,
        pdb_ns=pdb_ns,
        warmup_end_ns=warmup_end_ns,
        drain_end_ns=drain_end_ns,
    )
    det = detection_metrics(trials)
    gut = window_goodput(packets, start_ns=start_ns, end_ns=end_ns)

    # Attach per-run power for later paired comparison.
    pkt_for_compare = {
        "seed": profile.get("seed"),
        "case": base["case"],
        "baseline_id": base["baseline_id"],
        "traffic_sha256": base["traffic_sha256"],
        "avg_power_unit": energy["avg_power_unit"],
        "energy_unit_ms": energy["energy_unit_ms"],
        "window_ns": energy["window_ns"],
        "start_ns": start_ns,
        "end_ns": end_ns,
    }

    return {
        **base,
        "status": "OK",
        "reason": None,
        "window": {
            "start_ns": start_ns,
            "end_ns": end_ns,
            "window_ns": end_ns - start_ns,
            "warmup_end_ns": warmup_end_ns,
            "drain_end_ns": drain_end_ns,
            "pdb_ms": pdb_ms,
            "pdb_ns": pdb_ns,
            **windows,
        },
        "energy": energy,
        "packets": pkt,
        "detection": det,
        "goodput": gut,
        "upt": {
            **upt_placeholder(),
            "reason": UPT_NULL_REASON,
        },
        "compare_fields": pkt_for_compare,
        "notes": [
            "completion_rate is fixed-horizon delivery, not final reliability",
            "pdb_ratio primary is pooled counts; seed mean is a separate field in multi-run aggregation",
            "delay p90/p99 must distinguish pooled vs mean-run in multi-run aggregation",
            "window_goodput_mbps is not UPT",
            "drain energy is excluded from avg_power_unit",
        ],
    }


def summarize_multi_run(summaries: list[dict]) -> dict:
    """Aggregate single-run summarize_run outputs across seeds.

    Provides both pooled packet metrics and mean-run percentile fields.
    """

    ok = [s for s in summaries if s.get("status") == "OK" and s.get("packets")]
    if not ok:
        return {
            "status": "NO_VALID_RUNS",
            "reason": "no summarize_run outputs with status OK",
            "runs": 0,
        }
    agg = aggregate_multi_run([s["packets"] for s in ok])
    powers = [s["energy"]["avg_power_unit"] for s in ok]
    return {
        "status": "OK",
        "runs": len(ok),
        "mean_avg_power_unit": sum(powers) / len(powers) if powers else None,
        "energy_unit_ms_sum": sum(s["energy"]["energy_unit_ms"] for s in ok),
        "packets_aggregate": agg,
        "detection_note": "multi-run detection pooling is performed by W09 when raw trials are available; this helper only aggregates per-run detection points",
        "delay_aggregation_note": "delay_p90_ms_pooled and delay_p90_ms_mean_run are independent fields and must both be reported",
    }
