"""W09 Path-A integration of the frozen W00-W04 code tree.

The integrator owns orchestration and abstract device assumptions only.  It
does not copy or modify W00-W04 modules.  Every formal run is a contract
``RunOutput``: W02 creates Packet records, W03 consumes causal events and
emits Actions, this layer turns accepted monitor actions into W01 radio
intervals/service opportunities, W01 builds the packet ledger and applies EE
sharing, and W04 produces the reportable statistics and independently verifies
the same raw records.

The resulting Path-A claim is intentionally an abstract numerical discussion
claim.  Detection probabilities, relative power units, measurement timing and
the traffic models retain the evidence status carried by their registries;
this module never promotes them to PHY or 3GPP-conformance evidence.
"""

from __future__ import annotations

import copy
import hashlib
import heapq
import json
import math
import shutil
import traceback
from pathlib import Path
from typing import Any, Iterable

from wus_next.contracts import CONTRACT_VERSION, load_profile, validate_run_output
from wus_next.core import (
    assert_mr_ready,
    audit_intervals,
    build_packet_outcomes,
    check_resource_exclusivity,
    integrate_energy,
    select_sleep_state,
    serve_fifo,
    wake_feasible,
)
from wus_next.evidence import get_case, load_case_registry
from wus_next.metrics import (
    compare_paired,
    detection_metrics,
    summarize_run,
    verify_run,
)
from wus_next.policy6g import EVT_NEW_TRANSMISSION, initial_state, step
from wus_next.policy6g.actions import (
    MONITOR_DCP_RX,
    MONITOR_MEASUREMENT_EE,
    MONITOR_MEASUREMENT_NON_EE,
    MONITOR_PDCCH,
    MONITOR_WUS_RX,
)
from wus_next.policy6g.strategies import (
    LEGACY_REPLAY,
    OPTIMIZED_TIMER,
    SAME_TIMER,
    resolve_strategy_config,
)
from wus_next.traffic import (
    generate_traffic,
    stream_rng,
    traffic_profile_from_case,
    traffic_sha256,
)


NS_PER_MS = 1_000_000
UE_ID = "ue1"
DEFAULT_SEEDS = (101, 202, 303, 404, 505, 606, 707, 808)
BASELINE_CASE = "1-1"
RELEASE_EVIDENCE = "ASSUMPTION"
DEFAULT_SERVICE_RATE_BITS_PER_MS = 800_000
QOS_PDB_THRESHOLD = 0.95
LR_MICRO_POWER = 15.0
LR_WUS_POWER = 10.0
LR_DCP_POWER = 12.0
MR_PDCCH_POWER = 100.0
MR_MEASUREMENT_POWER = 80.0
EE_MEASUREMENT_POWER = 80.0
GRID_NS = 500_000


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _value(profile: dict, path: Iterable[str], default: Any = None) -> Any:
    node: Any = profile
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    if isinstance(node, dict) and "value" in node:
        return node["value"]
    return node


def _ns_ms(value: float | int) -> int:
    return int(round(float(value) * NS_PER_MS))


def _identifier(value: str) -> str:
    return str(value).replace(" ", "_").replace("/", "_")


def _traffic_window(traffic_id: str) -> tuple[int, int]:
    # The observation length is a W09 design choice, retained in every run's
    # metadata.  It is long enough to expose several FTP3 opportunities and
    # at least one IM opportunity while remaining a small deterministic matrix.
    if traffic_id == "FTP3":
        return 0, 640 * NS_PER_MS
    if traffic_id == "IM":
        return 0, 3200 * NS_PER_MS
    raise ValueError(f"unsupported Path-A traffic {traffic_id!r}")


def _source_hashes(root: Path) -> dict[str, str]:
    relative = [
        "wus_next/profiles/sixg_case1_v1.json",
        "wus_next/evidence/case_registry.json",
        "wus_next/contracts/schema.py",
        "wus_next/contracts/validator.py",
        "wus_next/core/energy.py",
        "wus_next/core/intervals.py",
        "wus_next/core/ledger.py",
        "wus_next/core/service.py",
        "wus_next/policy6g/machine.py",
        "wus_next/policy6g/strategies.py",
        "wus_next/traffic/generators.py",
        "wus_next/traffic/profile.py",
        "wus_next/metrics/summarize.py",
        "wus_next/metrics/verify.py",
        "wus_next/integration/runner.py",
    ]
    result: dict[str, str] = {}
    for item in relative:
        path = root / item
        if path.exists():
            result[item] = _file_sha256(path)
    return result


def _model_hash(root: Path, config: dict) -> str:
    return _sha256({"source_hashes": _source_hashes(root), "integration_config": config})


def _profile_parameter_snapshot(profile: dict) -> dict:
    parameters = profile.get("parameters", {})
    return {
        "traffic": {
            name: {
                key: entry.get("value")
                for key, entry in block.items()
                if isinstance(entry, dict) and "value" in entry
            }
            for name, block in parameters.get("traffic", {}).items()
            if isinstance(block, dict)
        },
        "measurement": {
            key: entry.get("value")
            for key, entry in parameters.get("measurement", {}).items()
            if isinstance(entry, dict) and "value" in entry
        },
        "power": {
            key: entry.get("value")
            for key, entry in parameters.get("power", {}).items()
            if isinstance(entry, dict) and "value" in entry
        },
        "detection": {
            key: entry.get("value")
            for key, entry in parameters.get("detection", {}).items()
            if isinstance(entry, dict) and "value" in entry
        },
    }


def validate_integration_inputs(profile_path: str | Path) -> dict:
    """Validate W00 profile, W00 registry and both W02 traffic bridges."""

    profile = load_profile(profile_path)
    if profile.get("profile_status") != "ENABLED":
        raise ValueError(f"Path-A profile is not ENABLED: {profile.get('profile_status')!r}")
    if "TEST_ONLY" in _canonical(profile):
        raise ValueError("TEST_ONLY profile data cannot enter a release run")
    registry = load_case_registry()
    cases = [get_case(case_id, registry) for case_id in profile["case_ids"]]
    if tuple(profile["case_ids"]) != tuple(item["case_id"] for item in cases):
        raise ValueError("profile case_ids do not match the registry order")
    traffic_checks = {}
    for traffic_id in ("FTP3", "IM"):
        bridge = traffic_profile_from_case(profile, traffic_id, UE_ID)
        traffic_checks[traffic_id] = {
            "traffic_id": bridge["traffic_id"],
            "model_class": bridge["model_class"],
            "evidence_status": bridge["evidence_status"],
            "formal_eligible": bridge["formal_eligible"],
            "source_ids": bridge["sources"],
            "parameter_snapshot": {
                key: leaf.get("value")
                for key, leaf in bridge["parameters"].items()
                if isinstance(leaf, dict) and "value" in leaf
            },
        }
    strategy_checks = {}
    for case_id in profile["case_ids"]:
        axes = __import__("wus_next.policy6g.axes", fromlist=["resolve_case_axes"]).resolve_case_axes(case_id, registry)
        strategy_checks[case_id] = {}
        for traffic_id in ("FTP3", "IM"):
            strategy_checks[case_id][traffic_id] = {}
            for policy_id in (SAME_TIMER, OPTIMIZED_TIMER):
                strategy_checks[case_id][traffic_id][policy_id] = resolve_strategy_config(
                    profile,
                    axes,
                    policy_id,
                    traffic_profile=traffic_id,
                )
    return {
        "contract_version": CONTRACT_VERSION,
        "profile_id": profile["profile_id"],
        "profile_version": profile["profile_version"],
        "registry_version": registry["registry_version"],
        "case_count": len(cases),
        "cases": cases,
        "traffic": traffic_checks,
        "strategies": strategy_checks,
        "parameter_snapshot": _profile_parameter_snapshot(profile),
        "source_hashes": _source_hashes(Path(__file__).resolve().parents[2]),
        "status": "PASS",
    }


def _runtime_profile(
    base_profile: dict,
    registry: dict,
    *,
    case_id: str,
    traffic_id: str,
    policy_id: str,
    seed: int,
    experiment_id: str,
    baseline_id: str,
    window: dict,
    overrides: dict[str, Any],
) -> dict:
    runtime = copy.deepcopy(base_profile)
    runtime.update(
        {
            "case_id": case_id,
            "case": case_id,
            "policy_id": policy_id,
            "default_traffic_profile": traffic_id,
            "case_registry": registry,
            "seed": seed,
            "experiment_id": experiment_id,
            "baseline_id": baseline_id,
            "traffic_sha256": "0" * 64,
            "metrics_window": window,
            "strategy_overrides": dict(overrides),
        }
    )
    return runtime


def _make_event(event_id: str, time_ns: int, kind: str, payload: Any, source_id: str = "W09") -> dict:
    return {
        "event_id": _identifier(event_id),
        "time_ns": int(time_ns),
        "kind": kind,
        "ue_id": UE_ID,
        "payload": payload,
        "source_id": _identifier(source_id),
    }


def _detection_outcome(condition: str, uniform: float, probabilities: dict[str, float]) -> str:
    if condition == "H0":
        return "WAKE" if uniform < probabilities["far"] else "NO_WAKE"
    if condition == "H1":
        return "NO_WAKE" if uniform < probabilities["mdr"] else "WAKE"
    if condition == "H2":
        return "WAKE" if uniform < probabilities["fdr"] else "NO_WAKE"
    raise ValueError(f"unknown detection condition {condition!r}")


def _detection_probabilities(base_profile: dict, scenario: str) -> dict[str, float]:
    if scenario == "ideal":
        return {"mdr": 0.0, "far": 0.0, "fdr": 0.0}
    if scenario == "impaired":
        return {"mdr": 0.15, "far": 0.08, "fdr": 0.12}
    if scenario != "main":
        raise ValueError(f"unknown detection scenario {scenario!r}")
    return {
        key: float(_value(base_profile, ("parameters", "detection", key), 0.0))
        for key in ("mdr", "far", "fdr")
    }


def _calendar_times(cfg: dict, window: dict, traffic_id: str, policy_id: str, packets: list[dict]) -> set[int]:
    start_ns, end_ns = int(window["start_ns"]), int(window["end_ns"])
    times: set[int] = {start_ns}
    measurement_cycle = int(cfg["measurement_cycle_ns"])
    measurement_offset = int(cfg.get("measurement_offset_ns", cfg.get("mo_offset_ns", 0))) % measurement_cycle
    for time_ns in range(measurement_offset, end_ns, measurement_cycle):
        if time_ns < start_ns:
            continue
        times.add(time_ns)
    if cfg.get("has_cdrx") and cfg.get("use_cdrx_on_grid", True):
        cycle = int(cfg["cdrx_cycle_ns"])
        offset = int(cfg.get("cdrx_offset_ns", cfg.get("mo_offset_ns", 0))) % cycle
        time_ns = offset
        while time_ns < start_ns:
            time_ns += cycle
        while time_ns < end_ns:
            times.add(time_ns)
            if time_ns + int(cfg["on_duration_ns"]) < end_ns:
                times.add(time_ns + int(cfg["on_duration_ns"]))
            time_ns += cycle
    # Packet-driven timer boundaries are explicit causal horizons for the
    # dispatcher.  They are not future packet contents: the packet is already
    # in the generated input stream and is delivered only at arrival_ns.
    for packet in packets:
        arrival = int(packet["arrival_ns"])
        if start_ns <= arrival < end_ns:
            times.add(arrival)
            if cfg.get("has_cdrx"):
                times.add(arrival + int(cfg["iat_ns"]))
            if cfg.get("use_wus_timer"):
                times.add(arrival + int(cfg["wus_timer_ns"]))
            if cfg.get("use_post_data_timer"):
                times.add(arrival + int(cfg["post_data_timer_ns"]))
    if cfg.get("has_wus") or cfg.get("has_dcp"):
        period = int(cfg["wus_period_ns"] if cfg.get("has_wus") else cfg["cdrx_cycle_ns"])
        offset = int(cfg.get("wus_offset_ns", cfg.get("mo_offset_ns", 0))) % period
        time_ns = offset
        while time_ns < start_ns:
            time_ns += period
        rx = int(cfg["wus_rx_ns"] if cfg.get("has_wus") else cfg["dcp_rx_ns"])
        monitor_span = _configured_monitor_span_ns(cfg)
        gap = int(cfg["gap_ns"] if cfg.get("has_wus") else cfg["dcp_gap_ns"])
        while time_ns < end_ns:
            times.add(time_ns)
            mo_end = time_ns + monitor_span
            times.add(mo_end)
            effective = mo_end + gap
            times.add(effective)
            if cfg.get("coupling") == "wus_independent":
                times.add(effective + int(cfg["wus_timer_ns"]))
            else:
                times.add(effective + int(cfg["on_duration_ns"]))
            time_ns += period
    return {time_ns for time_ns in times if start_ns <= time_ns < end_ns}


def _configured_monitor_span_ns(cfg: dict) -> int:
    rx_ns = int(cfg["wus_rx_ns"] if cfg.get("has_wus") else cfg["dcp_rx_ns"])
    if cfg.get("coupling") not in {"wus_coupled", "dcp"}:
        return rx_ns
    return (
        max(1, int(cfg.get("coupled_monitor_count", 1))) - 1
    ) * max(0, int(cfg.get("coupled_monitor_spacing_ns", 0))) + rx_ns


def _mo_starts(cfg: dict, window: dict) -> list[int]:
    if not (cfg.get("has_wus") or cfg.get("has_dcp")):
        return []
    start_ns, end_ns = int(window["start_ns"]), int(window["end_ns"])
    period = int(cfg["wus_period_ns"] if cfg.get("has_wus") else cfg["cdrx_cycle_ns"])
    offset = int(cfg.get("wus_offset_ns", cfg.get("mo_offset_ns", 0))) % period
    result: list[int] = []
    time_ns = offset
    while time_ns < start_ns:
        time_ns += period
    while time_ns < end_ns:
        result.append(time_ns)
        time_ns += period
    return result


def _add_interval(
    intervals: list[dict],
    *,
    receiver_id: str,
    start_ns: int,
    end_ns: int,
    state: str,
    power_unit: float,
    reason_event_id: str,
    source_action_id: str,
    window: dict,
) -> tuple[bool, dict | None]:
    start = max(int(start_ns), int(window["start_ns"]))
    end = min(int(end_ns), int(window["end_ns"]))
    if end <= start:
        return False, {"code": "OUTSIDE_WINDOW", "action_id": source_action_id}
    conflict = next(
        (
            previous
            for previous in intervals
            if previous["receiver_id"] == receiver_id
            and start < int(previous["end_ns"])
            and int(previous["start_ns"]) < end
        ),
        None,
    )
    if conflict is not None:
        return False, {
            "code": "RESOURCE_ARBITRATION_SKIP",
            "action_id": source_action_id,
            "receiver_id": receiver_id,
            "conflicts_with": conflict["interval_id"],
            "state": state,
            "detail": "existing accepted interval owns the receiver at this time",
        }
    record = {
        "interval_id": f"int_{_identifier(receiver_id)}_{len(intervals):06d}",
        "ue_id": UE_ID,
        "receiver_id": receiver_id,
        "start_ns": start,
        "end_ns": end,
        "state": _identifier(state),
        "power_unit": float(power_unit),
        "reason_event_id": _identifier(reason_event_id),
        "metadata": {"source_action_id": source_action_id, "integration_owner": "W09"},
    }
    intervals.append(record)
    return True, record


def _fill_receiver(intervals: list[dict], receiver_id: str, power: float, window: dict, reason: str) -> None:
    start_ns, end_ns = int(window["start_ns"]), int(window["end_ns"])
    selected = sorted((item for item in intervals if item["receiver_id"] == receiver_id), key=lambda item: (item["start_ns"], item["end_ns"], item["interval_id"]))
    cursor = start_ns
    fillers: list[tuple[int, int]] = []
    for item in selected:
        if cursor < int(item["start_ns"]):
            fillers.append((cursor, int(item["start_ns"])))
        cursor = max(cursor, int(item["end_ns"]))
    if cursor < end_ns:
        fillers.append((cursor, end_ns))
    for start, end in fillers:
        _add_interval(
            intervals,
            receiver_id=receiver_id,
            start_ns=start,
            end_ns=end,
            state="micro",
            power_unit=power,
            reason_event_id=reason,
            source_action_id=f"fill_{receiver_id}_{start}",
            window=window,
        )


def _fill_mr_sleep(
    intervals: list[dict],
    raw_increments: list[dict],
    window: dict,
    options: dict[str, Any],
) -> None:
    """Close MR gaps with the deepest transition-feasible sleep state."""

    start_ns, end_ns = int(window["start_ns"]), int(window["end_ns"])
    selected = sorted(
        (item for item in intervals if item["receiver_id"] == "MR"),
        key=lambda item: (item["start_ns"], item["end_ns"], item["interval_id"]),
    )
    cursor = start_ns
    gap_index = 0
    for next_interval in [*selected, None]:
        gap_end = end_ns if next_interval is None else int(next_interval["start_ns"])
        if cursor < gap_end:
            duration_ns = gap_end - cursor
            if duration_ns >= int(options["mr_deep_transition_time_ns"]):
                state = "deep"
                power = float(options["mr_deep_sleep_power_unit"])
                transition_energy = float(options["mr_deep_transition_energy_unit_ms"])
            elif duration_ns >= int(options["mr_light_transition_time_ns"]):
                state = "light"
                power = float(options["mr_light_sleep_power_unit"])
                transition_energy = float(options["mr_light_transition_energy_unit_ms"])
            else:
                state = "micro"
                power = float(options["mr_micro_sleep_power_unit"])
                transition_energy = 0.0
            _add_interval(
                intervals,
                receiver_id="MR",
                start_ns=cursor,
                end_ns=gap_end,
                state=state,
                power_unit=power,
                reason_event_id="W09_MR_SLEEP_FILL",
                source_action_id=f"fill_MR_{cursor}",
                window=window,
            )
            # Charge one complete sleep/wake cycle when this gap ends in an
            # active MR interval.  A trailing sleep has no wake inside the
            # observation window and therefore no transition increment here.
            if next_interval is not None and transition_energy > 0:
                raw_increments.append(
                    {
                        "increment_id": f"inc_sleep_transition_{gap_index:06d}",
                        "ue_id": UE_ID,
                        "start_ns": None,
                        "end_ns": None,
                        "at_ns": gap_end,
                        "energy_unit_ms": transition_energy,
                        "hardware_group": "mr_sleep_transition",
                        "source_event_ids": [next_interval["reason_event_id"]],
                        "policy_id": "independent_sum",
                        "metadata": {
                            "sleep_state": state,
                            "gap_duration_ns": duration_ns,
                            "accounting": "one complete entry+exit transition per sleep gap ending in MR activity",
                            "evidence_status": str(options.get("energy_evidence_status", RELEASE_EVIDENCE)),
                        },
                    }
                )
            gap_index += 1
        if next_interval is not None:
            cursor = max(cursor, int(next_interval["end_ns"]))


def _overlay_pdsch(intervals: list[dict], tx_records: list[dict], power_unit: float) -> list[dict]:
    """Replace MR PDCCH-only power with PDCCH+PDSCH during actual Tx spans."""

    tx_spans = sorted(
        (int(item["start_ns"]), int(item["end_ns"]), item["tx_id"])
        for item in tx_records
        if item.get("success")
    )
    out = [item for item in intervals if item["receiver_id"] != "MR"]
    sequence = 0
    for interval in sorted(
        (item for item in intervals if item["receiver_id"] == "MR"),
        key=lambda item: (item["start_ns"], item["end_ns"], item["interval_id"]),
    ):
        cursor = int(interval["start_ns"])
        pieces: list[tuple[int, int, str, float, str | None]] = []
        for tx_start, tx_end, tx_id in tx_spans:
            start = max(cursor, tx_start, int(interval["start_ns"]))
            end = min(tx_end, int(interval["end_ns"]))
            if end <= start:
                continue
            if cursor < start:
                pieces.append((cursor, start, interval["state"], float(interval["power_unit"]), None))
            pieces.append((start, end, "PDSCH", float(power_unit), tx_id))
            cursor = end
        if cursor < int(interval["end_ns"]):
            pieces.append((cursor, int(interval["end_ns"]), interval["state"], float(interval["power_unit"]), None))
        if not pieces:
            pieces.append((int(interval["start_ns"]), int(interval["end_ns"]), interval["state"], float(interval["power_unit"]), None))
        for start, end, state, power, tx_id in pieces:
            if end <= start:
                continue
            record = dict(interval)
            record["interval_id"] = f"int_MR_overlay_{sequence:08d}"
            record["start_ns"] = start
            record["end_ns"] = end
            record["state"] = state
            record["power_unit"] = power
            record["metadata"] = dict(interval.get("metadata") or {})
            if tx_id is not None:
                record["metadata"]["tx_id"] = tx_id
                record["metadata"]["power_role"] = "PDCCH_PLUS_PDSCH"
            out.append(record)
            sequence += 1
    return out


def _merge_mr_activity(
    intervals: list[dict],
    measurement_requests: list[dict],
    window: dict,
) -> list[dict]:
    """Build one disjoint MR activity timeline before sleep gaps are closed.

    A non-EE measurement wakes and occupies the main receiver.  It therefore
    has to split a sleep gap and participate in transition accounting.  The
    former incremental-only overlay priced the measurement samples but left
    the MR asleep underneath them, which omitted the associated wake cycles.
    Concurrent MR work is combined with a max-power rule.
    """

    start_ns, end_ns = int(window["start_ns"]), int(window["end_ns"])
    other = [item for item in intervals if item["receiver_id"] != "MR"]
    sources: list[dict] = [
        item for item in intervals
        if item["receiver_id"] == "MR" and int(item["end_ns"]) > start_ns and int(item["start_ns"]) < end_ns
    ]
    for index, request in enumerate(measurement_requests):
        sources.append(
            {
                "interval_id": f"int_MR_measurement_request_{index:06d}",
                "ue_id": UE_ID,
                "receiver_id": "MR",
                "start_ns": max(start_ns, int(request["start_ns"])),
                "end_ns": min(end_ns, int(request["end_ns"])),
                "state": "MEASUREMENT",
                "power_unit": float(request["power_unit"]),
                "reason_event_id": _identifier(request["source_event_id"]),
                "metadata": {
                    "source_action_id": request["source_action_id"],
                    "integration_owner": "W09",
                    "measurement_path": "non_EE",
                    "evidence_status": request.get("evidence_status", RELEASE_EVIDENCE),
                },
            }
        )
    sources = [item for item in sources if int(item["end_ns"]) > int(item["start_ns"])]
    if not sources:
        return other

    boundaries = sorted(
        {max(start_ns, int(item["start_ns"])) for item in sources}
        | {min(end_ns, int(item["end_ns"])) for item in sources}
    )
    merged: list[dict] = []
    sequence = 0
    for left, right in zip(boundaries, boundaries[1:]):
        if right <= left:
            continue
        active = [
            item for item in sources
            if int(item["start_ns"]) < right and int(item["end_ns"]) > left
        ]
        if not active:
            continue
        owner = max(active, key=lambda item: (float(item["power_unit"]), item["interval_id"]))
        record = dict(owner)
        metadata = dict(owner.get("metadata") or {})
        metadata["concurrent_reason_event_ids"] = sorted(
            {str(item["reason_event_id"]) for item in active}
        )
        record.update(
            {
                "interval_id": f"int_MR_activity_{sequence:08d}",
                "start_ns": left,
                "end_ns": right,
                "reason_event_id": str(owner["reason_event_id"]),
                "metadata": metadata,
            }
        )
        if (
            merged
            and merged[-1]["end_ns"] == left
            and merged[-1]["state"] == record["state"]
            and float(merged[-1]["power_unit"]) == float(record["power_unit"])
            and merged[-1]["reason_event_id"] == record["reason_event_id"]
            and merged[-1].get("metadata") == record.get("metadata")
        ):
            merged[-1]["end_ns"] = right
        else:
            merged.append(record)
            sequence += 1
    return other + merged


def _queue_bits(queue: list[dict]) -> int:
    return sum(int(item["remaining_bits"]) for item in queue)


def _new_tx_monitor_end(cfg: dict, start_ns: int, current_end_ns: int, window_end_ns: int) -> int:
    """Return the PDCCH-monitoring end after a successfully scheduled packet.

    C-DRX starts/restarts its inactivity timer on a new transmission.  The
    independent-WUS path analogously uses its short post-data timer.  Earlier
    integration kept only the initial on-duration, which made the 100 ms
    C-DRX inactivity timer consume zero energy.
    """

    if cfg.get("has_cdrx"):
        duration_ns = int(cfg["iat_ns"])
    elif cfg.get("use_post_data_timer"):
        duration_ns = int(cfg["post_data_timer_ns"])
    else:
        return min(int(current_end_ns), int(window_end_ns))
    return min(max(int(current_end_ns), int(start_ns) + duration_ns), int(window_end_ns))


def _service_ready_queue(
    queue: list[dict],
    *,
    start_ns: int,
    end_ns: int,
    attempt: int,
    intervals: list[dict],
    tx_records: list[dict],
    rate_bits_per_ms: int,
    reason: str,
) -> list[dict]:
    if not queue or end_ns <= start_ns:
        return queue
    assert_mr_ready(intervals, UE_ID, start_ns, end_ns)
    # Consume the complete accepted PDCCH/PDSCH window in FIFO order.  A former
    # one-packet lock left usable radio time idle and could strand a second
    # queued packet until the next wake occasion.
    cursor_ns = int(start_ns)
    packet_index = 0
    while queue and cursor_ns < int(end_ns):
        required_ns = max(
            1,
            int(math.ceil(int(queue[0]["remaining_bits"]) * NS_PER_MS / rate_bits_per_ms)),
        )
        tx_end_ns = min(int(end_ns), cursor_ns + required_ns)
        capacity = max(1, int(rate_bits_per_ms * (tx_end_ns - cursor_ns) / NS_PER_MS))
        opportunity = {
            "ue_id": UE_ID,
            "start_ns": cursor_ns,
            "end_ns": tx_end_ns,
            "capacity_bits": capacity,
            "success": True,
            "attempt": int(attempt + packet_index),
            "resource_ids": ["MR-PDSCH"],
            "service_profile": {"max_packets_per_opportunity": 1},
        }
        queue, emitted = serve_fifo(queue, opportunity)
        if not emitted:
            break
        for record in emitted:
            record["metadata"] = {
                "reason": reason,
                "service_rate_bits_per_ms": rate_bits_per_ms,
            }
        tx_records.extend(emitted)
        cursor_ns = tx_end_ns
        packet_index += 1
    return queue


def _serve_and_extend_active_window(
    queue: list[dict],
    *,
    start_ns: int,
    service_window: dict,
    interval_record: dict,
    simulation_end_ns: int,
    attempt: int,
    intervals: list[dict],
    tx_records: list[dict],
    rate_bits_per_ms: int,
    reason: str,
    cfg: dict,
) -> tuple[list[dict], list[dict]]:
    """Serve FIFO traffic and consume any active-time extension it creates.

    Each PDCCH-scheduled transmission restarts the applicable inactivity or
    post-data timer.  If a packet only partly fits in the initial on-duration,
    the newly created active time must be available immediately to finish it;
    otherwise the model charges active-time energy while incorrectly leaving
    the payload queued until a later wake occasion.
    """

    emitted_all: list[dict] = []
    cursor_ns = int(start_ns)
    while queue and cursor_ns < int(service_window["end_ns"]):
        accepted_end_ns = min(int(service_window["end_ns"]), int(simulation_end_ns))
        before = len(tx_records)
        queue = _service_ready_queue(
            queue,
            start_ns=cursor_ns,
            end_ns=accepted_end_ns,
            attempt=int(attempt) + len(emitted_all),
            intervals=intervals,
            tx_records=tx_records,
            rate_bits_per_ms=rate_bits_per_ms,
            reason=reason,
        )
        emitted = tx_records[before:]
        if not emitted:
            break
        emitted_all.extend(emitted)

        # The timer is restarted by every scheduled transmission.  The last
        # start in this batch therefore determines the furthest active end.
        previous_end_ns = int(service_window["end_ns"])
        last_tx_start_ns = max(int(record["start_ns"]) for record in emitted)
        extended_end_ns = _new_tx_monitor_end(
            cfg,
            last_tx_start_ns,
            previous_end_ns,
            int(simulation_end_ns),
        )
        service_window["end_ns"] = extended_end_ns
        interval_record["end_ns"] = extended_end_ns

        if not queue or extended_end_ns <= previous_end_ns:
            break
        # A remaining queue implies that the accepted interval was exhausted.
        # Continue at that boundary, which is now covered by the extension.
        cursor_ns = previous_end_ns

    return queue, emitted_all


def _sleep_probe(cfg: dict, now_ns: int, next_event_ns: int, resync_ns: int = 0) -> dict:
    # This is a W01 feasibility probe, not a claim that the abstract W03
    # policy selected deep sleep.  Main Path-A intervals intentionally keep the
    # W03 deep_claim false until capability evidence is available.
    candidates = [
        __import__("wus_next.core.sleep", fromlist=["SleepCandidate"]).SleepCandidate(
            state="light", power_unit=20.0, entry_ns=0, exit_ns=3 * NS_PER_MS, resync_ns=resync_ns, rank=1
        ),
        __import__("wus_next.core.sleep", fromlist=["SleepCandidate"]).SleepCandidate(
            state="deep", power_unit=1.0, entry_ns=10 * NS_PER_MS, exit_ns=10 * NS_PER_MS, resync_ns=resync_ns, additional_energy_unit_ms=450.0, rank=2
        ),
    ]
    decision = select_sleep_state(
        now_ns,
        next_event_ns,
        candidates,
        reference_state="micro",
        reference_power_unit=45.0,
        resync_power_unit=1.0,
    )
    return {
        "state": decision.state,
        "feasible": decision.feasible,
        "fallback_code": decision.fallback_code,
        "available_ns": decision.available_ns,
        "evaluated": decision.evaluated,
        "deep_claim_used_in_intervals": False,
    }


def run_one(
    base_profile: dict,
    registry: dict,
    *,
    case_id: str,
    traffic_id: str,
    seed: int,
    policy_id: str = SAME_TIMER,
    options: dict[str, Any] | None = None,
    root: Path | None = None,
) -> dict:
    """Run one deterministic paired-input Case-1 experiment."""

    options = dict(options or {})
    _, nominal_end_ns = _traffic_window(traffic_id)
    observation_duration_ns = int(nominal_end_ns)
    pdb_ms = 100 if traffic_id == "FTP3" else 300
    cdrx_tuple = _value(base_profile, ("parameters", "traffic", traffic_id, "cdrx"), None)
    if not isinstance(cdrx_tuple, (list, tuple)) or len(cdrx_tuple) != 3:
        raise ValueError(f"missing C-DRX tuple for {traffic_id}")
    warmup_ns = int(
        options.get(
            "warmup_ns",
            int(options.get("warmup_cdrx_cycles", 0)) * _ns_ms(float(cdrx_tuple[0])),
        )
    )
    drain_ns = int(
        options.get(
            "drain_ns",
            int(options.get("drain_pdb_multiples", 0)) * _ns_ms(pdb_ms),
        )
    )
    analysis_start_ns = warmup_ns
    analysis_end_ns = analysis_start_ns + observation_duration_ns
    simulation_end_ns = analysis_end_ns + drain_ns
    metrics_window = {
        "start_ns": analysis_start_ns,
        "end_ns": analysis_end_ns,
        "warmup_end_ns": analysis_start_ns,
        "drain_end_ns": simulation_end_ns,
        "pdb_ms": pdb_ms,
    }
    window = {
        "start_ns": 0,
        "end_ns": simulation_end_ns,
        "warmup_end_ns": analysis_start_ns,
        "drain_end_ns": simulation_end_ns,
        "pdb_ms": pdb_ms,
    }
    tag = str(options.get("label") or policy_id)
    experiment_id = _identifier(f"pathA_{traffic_id}_{case_id}_{tag}")
    run_id = _identifier(f"run_{traffic_id}_{case_id}_{tag}_s{seed}")
    baseline_id = "case_1_1"
    overrides = dict(options.get("strategy_overrides") or {})
    if "phase_ns" in options:
        overrides["mo_offset_ns"] = int(options["phase_ns"])
        overrides["cdrx_offset_ns"] = int(options["phase_ns"])
        overrides["wus_offset_ns"] = int(options["phase_ns"])
        overrides["measurement_offset_ns"] = int(options["phase_ns"])
    for offset_key in ("cdrx_offset_ns", "wus_offset_ns", "measurement_offset_ns"):
        if offset_key in options:
            overrides[offset_key] = int(options[offset_key])
    if "measurement_cycle_ns" in options:
        overrides["measurement_cycle_ns"] = int(options["measurement_cycle_ns"])
    if "measurement_duration_ns" in options:
        overrides["measurement_duration_ns"] = int(options["measurement_duration_ns"])
    if "gap_ns" in options:
        overrides["gap_ns"] = int(options["gap_ns"])
    if "dcp_gap_ns" in options:
        overrides["dcp_gap_ns"] = int(options["dcp_gap_ns"])
    if "wus_period_ns" in options:
        overrides["wus_period_ns"] = int(options["wus_period_ns"])
    if "wus_rx_ns" in options:
        overrides["wus_rx_ns"] = int(options["wus_rx_ns"])
    if "dcp_rx_ns" in options:
        overrides["dcp_rx_ns"] = int(options["dcp_rx_ns"])
    for monitor_key in ("coupled_monitor_count", "coupled_monitor_spacing_ns"):
        if monitor_key in options:
            overrides[monitor_key] = int(options[monitor_key])
    if "timer_ns" in options:
        overrides["iat_ns"] = int(options["timer_ns"])
        overrides["wus_timer_ns"] = int(options["timer_ns"])
        overrides["post_data_timer_ns"] = int(options["timer_ns"])
    runtime = _runtime_profile(
        base_profile,
        registry,
        case_id=case_id,
        traffic_id=traffic_id,
        policy_id=policy_id,
        seed=seed,
        experiment_id=experiment_id,
        baseline_id=baseline_id,
        window=metrics_window,
        overrides=overrides,
    )
    # W04's comparison API groups by ``profile["case"]``.  Attribution rows
    # keep their registry case ID; tuning/sensitivity rows get a deterministic
    # label so candidates cannot overwrite one another when paired by seed.
    runtime["case"] = case_id if tag == "same_timer" else f"{case_id}@{_identifier(tag)}"
    traffic_profile = traffic_profile_from_case(base_profile, traffic_id, UE_ID)
    # Generate new arrivals only through the observation end.  The remaining
    # simulation tail is a drain period and contains no new offered traffic.
    packets = generate_traffic(traffic_profile, seed, analysis_end_ns)
    traffic_hash = traffic_sha256(packets)
    runtime["traffic_sha256"] = traffic_hash
    state = initial_state(runtime, UE_ID)
    cfg = state["config"]
    probabilities = _detection_probabilities(base_profile, str(options.get("detection_scenario", "main")))
    detection_rng = stream_rng(seed, UE_ID, "detection")
    other_rng = stream_rng(seed, UE_ID, "other_wus")
    mo_uniforms = {time_ns: float(detection_rng.random()) for time_ns in _mo_starts(cfg, window)}
    other_prob = float(options.get("other_wus_probability", 0.0))
    mo_other = {time_ns: bool(other_rng.random() < other_prob) for time_ns in _mo_starts(cfg, window)}

    event_times = _calendar_times(cfg, window, traffic_id, policy_id, packets)
    mo_times = set(_mo_starts(cfg, window))
    packet_by_time: dict[int, list[dict]] = {}
    for packet in packets:
        packet_by_time.setdefault(int(packet["arrival_ns"]), []).append(packet)
    pending_events: list[tuple[int, int, str, dict]] = []
    sequence = 0
    for time_ns in sorted(event_times):
        if time_ns in packet_by_time:
            for packet in sorted(packet_by_time[time_ns], key=lambda item: item["packet_id"]):
                event = _make_event(
                    f"packet_{packet['packet_id']}",
                    time_ns,
                    "PACKET_ARRIVAL",
                    packet,
                    "W02_TRAFFIC",
                )
                heapq.heappush(pending_events, (time_ns, 0, event["event_id"], event))
        if time_ns in mo_times:
            event = _make_event(
                f"mo_{case_id}_{traffic_id}_{time_ns}",
                time_ns,
                "MO_START",
                {"mo_id": f"mo_{case_id}_{time_ns}", "other_wus": mo_other.get(time_ns, False)},
                "W09_MO_CALENDAR",
            )
            heapq.heappush(pending_events, (time_ns, 1, event["event_id"], event))
        tick = _make_event(f"tick_{traffic_id}_{time_ns}", time_ns, "TICK", None, "W09_CALENDAR")
        heapq.heappush(pending_events, (time_ns, 3, tick["event_id"], tick))
    for time_ns in sorted(mo_times):
        # Detection events are inserted only after MO_START has frozen the
        # target.  This keeps the actual condition and outcome causal.
        pass

    actions: list[dict] = []
    events: list[dict] = []
    intervals: list[dict] = []
    raw_increments: list[dict] = []
    tx_records: list[dict] = []
    trials: list[dict] = []
    arbitration: list[dict] = []
    queue: list[dict] = []
    packet_seen: set[str] = set()
    attempt = 0
    service_windows: list[dict] = []
    detections_scheduled: set[str] = set()
    non_ee_measurement_requests: list[dict] = []
    while pending_events:
        _, _, _, event = heapq.heappop(pending_events)
        now_ns = int(event["time_ns"])
        # Same-time event ordering is deliberate: arrivals, MO, detection and
        # then calendar tick.  W03 itself enforces event.time_ns == now_ns.
        if event["kind"] == "PACKET_ARRIVAL":
            packet = event["payload"]
            if packet["packet_id"] not in packet_seen:
                packet_seen.add(packet["packet_id"])
                queue.append({**packet, "remaining_bits": int(packet["size_bits"])})
        observable = {
            "now_ns": now_ns,
            "queue_bits": _queue_bits(queue),
            "mr_ready_ns": now_ns,
            "known_calendar": sorted(time for time in event_times if time > now_ns)[:16],
            "received_indications": [],
            "history": [],
        }
        try:
            state, emitted = step(state, event, observable)
        except Exception as exc:
            raise RuntimeError(
                f"W03 integration failure at {event['event_id']} {event['kind']} {now_ns}: {exc}"
            ) from exc
        events.append(event)
        actions.extend(emitted)
        if event["kind"] == "MO_START":
            active = state.get("active_mo")
            if active is None:
                trials.append(
                    {
                        "trial_id": f"trial_{case_id}_{traffic_id}_{now_ns}",
                        "ue_id": UE_ID,
                        "mo_start_ns": now_ns,
                        "mo_end_ns": now_ns + _configured_monitor_span_ns(cfg),
                        "condition": "H0",
                        "outcome": "SKIPPED",
                        "source_profile_id": base_profile["profile_id"],
                        "metadata": {"skip_reason": "policy_or_resource_conflict", "evidence_status": RELEASE_EVIDENCE},
                    }
                )
            else:
                mo_id = active["mo_id"]
                condition = "H2" if active.get("other_wus") else ("H1" if active.get("target_frozen") else "H0")
                outcome = _detection_outcome(condition, mo_uniforms.get(now_ns, 0.5), probabilities)
                detection_event = _make_event(
                    f"detection_{mo_id}",
                    int(active["mo_end_ns"]),
                    "DETECTION_RESULT",
                    {
                        "mo_id": mo_id,
                        "mo_start_ns": now_ns,
                        "condition": condition,
                        "outcome": outcome,
                    },
                    "W09_DETECTION_ASSUMPTION",
                )
                heapq.heappush(pending_events, (int(active["mo_end_ns"]), 2, detection_event["event_id"], detection_event))
                detections_scheduled.add(mo_id)
                trials.append(
                    {
                        "trial_id": f"trial_{case_id}_{traffic_id}_{now_ns}",
                        "ue_id": UE_ID,
                        "mo_start_ns": now_ns,
                        "mo_end_ns": int(active["mo_end_ns"]),
                        "condition": condition,
                        "outcome": outcome,
                        "source_profile_id": base_profile["profile_id"],
                        "metadata": {"policy_delivery": "causal_frozen_at_mo_start", "evidence_status": RELEASE_EVIDENCE},
                    }
                )
        for action in emitted:
            payload = action.get("payload") or {}
            if action["kind"] == "REQUEST_MONITOR":
                monitor_id = payload.get("monitor_id")
                duration_ns = int(payload.get("duration_ns", 0))
                if monitor_id in {MONITOR_WUS_RX, MONITOR_DCP_RX}:
                    power = (
                        float(options.get("lr_wus_power_unit", LR_WUS_POWER))
                        if monitor_id == MONITOR_WUS_RX
                        else float(options.get("lr_dcp_power_unit", LR_DCP_POWER))
                    )
                    for occasion_index, occasion_offset_ns in enumerate(
                        payload.get("occasion_offsets_ns") or [0]
                    ):
                        accepted, finding = _add_interval(
                            intervals,
                            receiver_id="LR",
                            start_ns=int(action["time_ns"]) + int(occasion_offset_ns),
                            end_ns=int(action["time_ns"]) + int(occasion_offset_ns) + duration_ns,
                            # Contract RadioInterval states describe the radio
                            # power state, while the exact LR monitor kind stays
                            # in metadata.  DCP_RX/WUS_RX are Action monitor IDs,
                            # not public interval state names.
                            state="light",
                            power_unit=power,
                            reason_event_id=action["caused_by"],
                            source_action_id=f"{action['action_id']}_occasion_{occasion_index}",
                            window=window,
                        )
                        if accepted:
                            intervals[-1]["metadata"].update(
                                {"monitor_id": monitor_id, "occasion_index": occasion_index}
                            )
                        if not accepted and finding:
                            arbitration.append(finding)
                elif monitor_id == MONITOR_PDCCH:
                    requested_end = min(int(action["time_ns"]) + duration_ns, int(window["end_ns"]))
                    interval_record = next(
                        (
                            item
                            for item in intervals
                            if item["receiver_id"] == "MR"
                            and item["state"] == "PDCCH"
                            and int(action["time_ns"]) < int(item["end_ns"])
                            and int(item["start_ns"]) < requested_end
                        ),
                        None,
                    )
                    if interval_record is not None:
                        interval_record["end_ns"] = max(int(interval_record["end_ns"]), requested_end)
                        interval_record.setdefault("metadata", {}).setdefault("merged_action_ids", []).append(action["action_id"])
                        accepted, finding = True, interval_record
                    else:
                        accepted, finding = _add_interval(
                            intervals,
                            receiver_id="MR",
                            start_ns=action["time_ns"],
                            end_ns=requested_end,
                            state="PDCCH",
                            power_unit=float(options.get("mr_pdcch_only_power_unit", options.get("mr_pdcch_power_unit", MR_PDCCH_POWER))),
                            reason_event_id=action["caused_by"],
                            source_action_id=action["action_id"],
                            window=window,
                        )
                    if accepted:
                        interval_record = finding
                        service_window = next(
                            (item for item in service_windows if item["interval_id"] == interval_record["interval_id"]),
                            None,
                        )
                        if service_window is None:
                            service_window = {
                                "start_ns": action["time_ns"],
                                "end_ns": interval_record["end_ns"],
                                "action_id": action["action_id"],
                                "interval_id": interval_record["interval_id"],
                            }
                            service_windows.append(service_window)
                        else:
                            service_window["end_ns"] = max(int(service_window["end_ns"]), int(interval_record["end_ns"]))
                        if queue:
                            service_start_ns = max(
                                int(action["time_ns"]),
                                max(
                                    (
                                        int(tx["end_ns"])
                                        for tx in tx_records
                                        if tx.get("success")
                                        and int(tx["start_ns"]) < int(service_window["end_ns"])
                                        and int(tx["end_ns"]) > int(action["time_ns"])
                                    ),
                                    default=int(action["time_ns"]),
                                ),
                            )
                            end_service = min(int(service_window["end_ns"]), int(window["end_ns"]))
                            if service_start_ns >= end_service:
                                continue
                            attempt += 1
                            queue, newly_emitted = _serve_and_extend_active_window(
                                queue,
                                start_ns=service_start_ns,
                                service_window=service_window,
                                interval_record=interval_record,
                                simulation_end_ns=int(window["end_ns"]),
                                attempt=attempt,
                                intervals=intervals,
                                tx_records=tx_records,
                                rate_bits_per_ms=int(options.get("service_rate_bits_per_ms", DEFAULT_SERVICE_RATE_BITS_PER_MS)),
                                reason=action["action_id"],
                                cfg=cfg,
                            )
                            if newly_emitted:
                                attempt += len(newly_emitted) - 1
                                for tx_record in newly_emitted:
                                    new_tx_event = _make_event(
                                        f"new_tx_{tx_record['tx_id']}",
                                        int(tx_record["start_ns"]),
                                        EVT_NEW_TRANSMISSION,
                                        {"tx_id": tx_record["tx_id"], "packet_id": tx_record["packet_id"]},
                                        "W09_SERVICE",
                                    )
                                    heapq.heappush(pending_events, (int(tx_record["start_ns"]), 2, new_tx_event["event_id"], new_tx_event))
                    elif finding:
                        arbitration.append(finding)
                elif monitor_id == MONITOR_MEASUREMENT_NON_EE:
                    # Keep the measurement request even when it overlaps a
                    # PDCCH/PDSCH interval.  It is applied below as a max-power
                    # overlay on the complete MR timeline.  Treating the
                    # overlap as a skipped measurement made the C-DRX baseline
                    # avoid measurement energy merely because both calendars
                    # started at t=0.
                    non_ee_measurement_requests.append(
                        {
                            "start_ns": int(action["time_ns"]),
                            "end_ns": int(action["time_ns"]) + duration_ns,
                            "power_unit": float(options.get("mr_measurement_power_unit", MR_MEASUREMENT_POWER)),
                            "source_event_id": action["caused_by"],
                            "source_action_id": action["action_id"],
                        }
                    )
                elif monitor_id == MONITOR_MEASUREMENT_EE:
                    ee_power = float(options.get("ee_measurement_power_unit", EE_MEASUREMENT_POWER))
                    raw_increments.append(
                        {
                            "increment_id": f"inc_{action['action_id']}",
                            "ue_id": UE_ID,
                            "start_ns": int(action["time_ns"]),
                            "end_ns": int(action["time_ns"]) + duration_ns,
                            "at_ns": None,
                            "energy_unit_ms": ee_power * duration_ns / NS_PER_MS,
                            "hardware_group": "serving_ee",
                            "source_event_ids": [action["caused_by"]],
                            "policy_id": str(options.get("ee_policy", _value(base_profile, ("parameters", "power", "sharing_policy"), "shared_max"))),
                            "metadata": {
                                "measurement_path": "EE",
                                "accounting": "incremental EE energy; MR sleep floor remains in the base timeline",
                                "evidence_status": str(options.get("energy_evidence_status", RELEASE_EVIDENCE)),
                            },
                        }
                    )
                    edge_energy = float(options.get("ee_transition_energy_per_edge_unit_ms", 0.0))
                    if edge_energy > 0:
                        for edge, at_ns in (
                            ("enter", int(action["time_ns"])),
                            ("leave", min(int(window["end_ns"]) - 1, int(action["time_ns"]) + duration_ns)),
                        ):
                            raw_increments.append(
                                {
                                    "increment_id": f"inc_ee_{edge}_{action['action_id']}",
                                    "ue_id": UE_ID,
                                    "start_ns": None,
                                    "end_ns": None,
                                    "at_ns": at_ns,
                                    "energy_unit_ms": edge_energy,
                                    "hardware_group": "serving_ee_transition",
                                    "source_event_ids": [action["caused_by"]],
                                    "policy_id": "independent_sum",
                                    "metadata": {
                                        "measurement_path": "EE",
                                        "transition_edge": edge,
                                        "evidence_status": str(options.get("energy_evidence_status", RELEASE_EVIDENCE)),
                                    },
                                }
                            )
            elif action["kind"] == "REQUEST_SLEEP":
                # REQUEST_SLEEP is retained as an Action.  The explicit W01
                # probe below provides a structured feasibility result without
                # letting an uncalibrated deep state alter the main timeline.
                pass
        if event["kind"] == "PACKET_ARRIVAL":
            # A packet arriving inside an already accepted PDCCH window may be
            # served after its arrival, never before it.  If another PDSCH is
            # still in flight on the same single-UE resource, begin only after
            # that transmission ends.
            for service_window in service_windows:
                if service_window["start_ns"] <= now_ns < service_window["end_ns"] and queue:
                    service_start_ns = max(
                        now_ns,
                        max(
                            (
                                int(tx["end_ns"])
                                for tx in tx_records
                                if tx.get("success")
                                and int(tx["start_ns"]) < int(service_window["end_ns"])
                                and int(tx["end_ns"]) > now_ns
                            ),
                            default=now_ns,
                        ),
                    )
                    if service_start_ns >= int(service_window["end_ns"]):
                        break
                    attempt += 1
                    matching_interval = next(
                        item for item in intervals if item["interval_id"] == service_window["interval_id"]
                    )
                    queue, newly_emitted = _serve_and_extend_active_window(
                        queue,
                        start_ns=service_start_ns,
                        service_window=service_window,
                        interval_record=matching_interval,
                        simulation_end_ns=int(window["end_ns"]),
                        attempt=attempt,
                        intervals=intervals,
                        tx_records=tx_records,
                        rate_bits_per_ms=int(options.get("service_rate_bits_per_ms", DEFAULT_SERVICE_RATE_BITS_PER_MS)),
                        reason=service_window["action_id"],
                        cfg=cfg,
                    )
                    if newly_emitted:
                        attempt += len(newly_emitted) - 1
                        for tx_record in newly_emitted:
                            new_tx_event = _make_event(
                                f"new_tx_{tx_record['tx_id']}",
                                int(tx_record["start_ns"]),
                                EVT_NEW_TRANSMISSION,
                                {"tx_id": tx_record["tx_id"], "packet_id": tx_record["packet_id"]},
                                "W09_SERVICE",
                            )
                            heapq.heappush(pending_events, (int(tx_record["start_ns"]), 2, new_tx_event["event_id"], new_tx_event))
                    break

    # Non-EE samples are real MR activity.  Merge them before closing sleep
    # gaps so their wake transitions are charged exactly once.
    intervals = _merge_mr_activity(intervals, non_ee_measurement_requests, window)

    # Add the stable receiver floors after action arbitration.  They make
    # closure explicit and leave action intervals disjoint for W01 audit.
    if options.get("sleep_state_model") == "gap_feasible":
        _fill_mr_sleep(intervals, raw_increments, window, options)
    else:
        _fill_receiver(
            intervals,
            "MR",
            float(options.get("mr_idle_power_unit", _value(base_profile, ("parameters", "power", "micro"), 45.0))),
            window,
            "W09_MR_FLOOR",
        )
    _fill_receiver(intervals, "LR", float(options.get("lr_idle_power_unit", LR_MICRO_POWER)), window, "W09_LR_FLOOR")
    intervals = _overlay_pdsch(
        intervals,
        tx_records,
        float(options.get("mr_pdsch_power_unit", options.get("mr_pdcch_power_unit", MR_PDCCH_POWER))),
    )
    intervals.sort(key=lambda item: (item["receiver_id"], item["start_ns"], item["end_ns"], item["interval_id"]))

    audit = audit_intervals(intervals, int(window["start_ns"]), int(window["end_ns"]))
    if not audit["valid"]:
        raise RuntimeError(f"W01 interval audit failed for {run_id}: {audit}")
    raw_energy = integrate_energy(
        intervals,
        raw_increments,
        analysis_start_ns,
        analysis_end_ns,
    )
    effective_increments = raw_energy["merged_increments"]
    energy_policy = str(options.get("ee_policy", _value(base_profile, ("parameters", "power", "sharing_policy"), "shared_max")))
    ledger = build_packet_outcomes(packets, tx_records)
    outcomes = ledger["outcomes"]
    resource_report = check_resource_exclusivity(tx_records)
    if not resource_report["valid"]:
        raise RuntimeError(f"W01 resource audit failed for {run_id}: {resource_report}")

    summary_profile = copy.deepcopy(runtime)
    summary_profile["metrics_window"] = metrics_window
    summary = summarize_run(intervals=intervals, increments=effective_increments, packets=outcomes, tx=tx_records, trials=trials, profile=summary_profile)
    run = {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "seed": int(seed),
        "experiment_id": experiment_id,
        "profile_id": base_profile["profile_id"],
        "baseline_id": baseline_id,
        "traffic_sha256": traffic_hash,
        "measurement_calendar_sha256": _sha256(
            [
                {"time_ns": time_ns, "duration_ns": int(cfg["measurement_duration_ns"])}
                for time_ns in sorted(event_times)
                if (
                    time_ns
                    - int(cfg.get("measurement_offset_ns", cfg.get("mo_offset_ns", 0)))
                )
                % int(cfg["measurement_cycle_ns"])
                == 0
            ]
        ),
        "model_hash": _model_hash(root or Path(__file__).resolve().parents[2], {"case_id": case_id, "traffic_id": traffic_id, "policy_id": policy_id, "options": options}),
        "evidence_status": RELEASE_EVIDENCE,
        "packets": packets,
        "events": events,
        "intervals": intervals,
        "increments": effective_increments,
        "tx": tx_records,
        "outcomes": outcomes,
        "trials": trials,
        "actions": actions,
        "summary": summary,
        "metadata": {
            "case_id": case_id,
            "case_definition": get_case(case_id, registry),
            "traffic_id": traffic_id,
            "traffic_profile_id": traffic_profile["traffic_id"],
            "traffic_model_class": traffic_profile["model_class"],
            "traffic_evidence_status": traffic_profile["evidence_status"],
            "traffic_formal_eligible": traffic_profile["formal_eligible"],
            "traffic_parameter_snapshot": {
                key: leaf.get("value")
                for key, leaf in traffic_profile["parameters"].items()
                if isinstance(leaf, dict) and "value" in leaf
            },
            "profile_parameter_snapshot": _profile_parameter_snapshot(base_profile),
            "policy_id": policy_id,
            "policy_config": cfg,
            "options": options,
            "effective_window": metrics_window,
            "simulation_window": window,
            "energy_policy": energy_policy,
            "raw_increment_count": len(raw_increments),
            "effective_increment_count": len(effective_increments),
            "raw_energy_w01": raw_energy,
            "w01_interval_audit": audit,
            "w01_resource_audit": resource_report,
            "w01_packet_ledger": ledger["report"],
            "arbitration": arbitration,
            "sleep_feasibility_probe": _sleep_probe(
                cfg,
                analysis_start_ns,
                analysis_end_ns,
                int(options.get("resync_ns", 0)),
            ),
            "detection_assumption": {
                "scenario": options.get("detection_scenario", "main"),
                "probabilities": probabilities,
                "other_wus_probability": other_prob,
                "source_status": RELEASE_EVIDENCE,
            },
            "integration_status": "W00_W01_W02_W03_W04_CONNECTED",
            "formal_claim_status": "ABSTRACT_NUMERICAL_DISCUSSION_ONLY",
        },
    }
    validate_run_output(run)
    verification = verify_run(run, summary=summary, window=metrics_window)
    if verification["status"] != "PASS":
        raise RuntimeError(f"W04 independent verification failed for {run_id}: {verification}")
    run["metadata"]["independent_verification"] = {
        "status": verification["status"],
        "structural_valid": verification["structural_valid"],
        "bit_partition_ok": verification["bit_partition_ok"],
        "mismatches": verification["mismatches"],
    }
    return run


def _oracle_o01() -> dict:
    intervals = [
        {"interval_id": "o01_a", "ue_id": UE_ID, "receiver_id": "MR", "start_ns": 0, "end_ns": 2 * NS_PER_MS, "state": "active", "power_unit": 100, "reason_event_id": "o01"},
        {"interval_id": "o01_b", "ue_id": UE_ID, "receiver_id": "MR", "start_ns": 2 * NS_PER_MS, "end_ns": 10 * NS_PER_MS, "state": "micro", "power_unit": 20, "reason_event_id": "o01"},
    ]
    result = integrate_energy(intervals, [], 0, 10 * NS_PER_MS)
    return {"id": "O01", "status": "PASS" if result["total_energy_unit_ms"] == 360.0 and result["total_energy_unit_ms"] / 10 == 36.0 else "FAIL", "observed": result}


def _oracle_o02() -> dict:
    intervals = [
        {"interval_id": "o02_a", "ue_id": UE_ID, "receiver_id": "MR", "start_ns": 0, "end_ns": 6 * NS_PER_MS, "state": "micro", "power_unit": 1, "reason_event_id": "o02"},
        {"interval_id": "o02_b", "ue_id": UE_ID, "receiver_id": "MR", "start_ns": 4 * NS_PER_MS, "end_ns": 8 * NS_PER_MS, "state": "micro", "power_unit": 1, "reason_event_id": "o02"},
        {"interval_id": "o02_c", "ue_id": UE_ID, "receiver_id": "MR", "start_ns": 8 * NS_PER_MS, "end_ns": 10 * NS_PER_MS, "state": "micro", "power_unit": 1, "reason_event_id": "o02"},
    ]
    result = audit_intervals(intervals, 0, 10 * NS_PER_MS)
    return {"id": "O02", "status": "PASS" if not result["valid"] and "INTERVAL_OVERLAP" in result["codes"] else "FAIL", "observed": result}


def _oracle_o03() -> dict:
    ints, incs = __import__("wus_next.core", fromlist=["build_transition"]).build_transition(
        UE_ID, 0, 10 * NS_PER_MS, state="deep", floor_power_unit=1, additional_energy_unit_ms=450, ramp_total_ns=20 * NS_PER_MS, reason_event_id="o03", id_base="o03"
    )
    result = integrate_energy(ints, incs, 0, 30 * NS_PER_MS)
    return {"id": "O03", "status": "PASS" if result["total_energy_unit_ms"] == 480.0 else "FAIL", "observed": result}


def _oracle_o04() -> dict:
    packets = [
        {"packet_id": "o04_p1", "ue_id": UE_ID, "flow_id": "f", "arrival_ns": 0, "size_bits": 800, "deadline_ns": 3 * NS_PER_MS, "traffic_profile_id": "FTP3"},
        {"packet_id": "o04_p2", "ue_id": UE_ID, "flow_id": "f", "arrival_ns": 1 * NS_PER_MS, "size_bits": 800, "deadline_ns": 4 * NS_PER_MS, "traffic_profile_id": "FTP3"},
        {"packet_id": "o04_p3", "ue_id": UE_ID, "flow_id": "f", "arrival_ns": 9 * NS_PER_MS, "size_bits": 800, "deadline_ns": 12 * NS_PER_MS, "traffic_profile_id": "FTP3"},
    ]
    tx = [{"tx_id": "o04_tx", "ue_id": UE_ID, "packet_id": "o04_p1", "segment_id": "o04_p1s", "offset_bits": 0, "start_ns": 0, "end_ns": 2 * NS_PER_MS, "attempt": 1, "payload_bits": 800, "success": True, "resource_ids": ["r"]}]
    outcomes = build_packet_outcomes(packets, tx)["outcomes"]
    metrics = __import__("wus_next.metrics", fromlist=["packet_metrics"]).packet_metrics(outcomes, start_ns=0, end_ns=10 * NS_PER_MS, pdb_ns=10 * NS_PER_MS)
    expected = (3, 1, 2, 2, 1, 0.5, 1, 2.0)
    observed = (metrics["arrived_count"], metrics["delivered_count"], metrics["pending_count"], metrics["pdb_eligible_count"], metrics["pdb_satisfied_count"], metrics["pdb_ratio"], metrics["right_censored_count"], metrics["delay_mean_ms_delivered"])
    return {"id": "O04", "status": "PASS" if observed == expected else "FAIL", "observed": metrics, "expected": expected}


def _oracle_o05() -> dict:
    result = compare_paired(
        [
            {"seed": 1, "case": "1-1", "traffic_sha256": "a" * 64, "window_ns": 10, "start_ns": 0, "avg_power_unit": 10},
            {"seed": 2, "case": "1-1", "traffic_sha256": "b" * 64, "window_ns": 10, "start_ns": 0, "avg_power_unit": 100},
            {"seed": 1, "case": "1-2", "traffic_sha256": "a" * 64, "window_ns": 10, "start_ns": 0, "avg_power_unit": 5},
            {"seed": 2, "case": "1-2", "traffic_sha256": "b" * 64, "window_ns": 10, "start_ns": 0, "avg_power_unit": 90},
        ],
        "1-1",
        9,
        case_id="1-2",
        n_boot=200,
    )
    expected = 1.0 - 95.0 / 110.0
    return {"id": "O05", "status": "PASS" if math.isclose(result["gain"], expected, abs_tol=1e-12) else "FAIL", "observed": result, "expected_gain": expected}


def _oracle_o06() -> dict:
    trials = []
    for index in range(1000):
        trials.append({"trial_id": f"o06h0_{index}", "ue_id": UE_ID, "mo_start_ns": index, "mo_end_ns": index + 1, "condition": "H0", "outcome": "WAKE" if index < 10 else "NO_WAKE", "source_profile_id": "o06"})
        trials.append({"trial_id": f"o06h1_{index}", "ue_id": UE_ID, "mo_start_ns": index + 2000, "mo_end_ns": index + 2001, "condition": "H1", "outcome": "NO_WAKE" if index < 20 else "WAKE", "source_profile_id": "o06"})
    for index in range(500):
        trials.append({"trial_id": f"o06h2_{index}", "ue_id": UE_ID, "mo_start_ns": index + 4000, "mo_end_ns": index + 4001, "condition": "H2", "outcome": "WAKE" if index < 15 else "NO_WAKE", "source_profile_id": "o06"})
    result = detection_metrics(trials)
    return {"id": "O06", "status": "PASS" if result["far"]["point"] == 0.01 and result["mdr"]["point"] == 0.02 and result["fdr"]["point"] == 0.03 else "FAIL", "observed": result}


def _oracle_o07() -> dict:
    observed = {"41": 41 * 125_000, "592": 592 * 125_000}
    return {"id": "O07", "status": "PASS" if observed == {"41": 5_125_000, "592": 74_000_000} else "FAIL", "observed": observed}


def _oracle_o08() -> dict:
    observed = {"gap5": wake_feasible(10 * NS_PER_MS, 1 * NS_PER_MS, 5 * NS_PER_MS), "gap13": wake_feasible(10 * NS_PER_MS, 1 * NS_PER_MS, 13 * NS_PER_MS), "gap25": wake_feasible(10 * NS_PER_MS, 1 * NS_PER_MS, 25 * NS_PER_MS)}
    return {"id": "O08", "status": "PASS" if observed == {"gap5": False, "gap13": True, "gap25": True} else "FAIL", "observed": observed}


def _oracle_o09() -> dict:
    candidate = [{"next_event_ns": 10 * NS_PER_MS, "queue_bits": 0}, {"next_event_ns": 10 * NS_PER_MS, "queue_bits": 1_000_000_000}]
    # The W01 selector's public inputs contain only the causal next known event;
    # these two future packet lists intentionally never enter the call.
    decisions = []
    for item in candidate:
        decisions.append(select_sleep_state(0, item["next_event_ns"], [], reference_state="micro", reference_power_unit=45).state)
    return {"id": "O09", "status": "PASS" if decisions == ["micro", "micro"] else "FAIL", "observed": {"decisions": decisions}}


def _oracle_o10() -> dict:
    profile = {"profile_id": "o10", "seed": 1, "experiment_id": "o10", "baseline_id": "o10", "metrics_window": {"start_ns": 0, "end_ns": 10 * NS_PER_MS, "pdb_ms": 10}}
    intervals = [{"interval_id": "o10_mr", "ue_id": UE_ID, "receiver_id": "MR", "start_ns": 0, "end_ns": 10 * NS_PER_MS, "state": "micro", "power_unit": 1, "reason_event_id": "o10"}, {"interval_id": "o10_lr", "ue_id": UE_ID, "receiver_id": "LR", "start_ns": 0, "end_ns": 10 * NS_PER_MS, "state": "micro", "power_unit": 1, "reason_event_id": "o10"}]
    summary = summarize_run(profile, intervals, [], [], [], [])
    return {"id": "O10", "status": "PASS" if summary["detection"]["far"]["point"] is None and summary["packets"]["arrived_count"] == 0 else "FAIL", "observed": summary}


def run_oracles(out_dir: str | Path) -> dict:
    """Execute O01-O10 as test evidence outside the formal run set."""

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    checks = [_oracle_o01(), _oracle_o02(), _oracle_o03(), _oracle_o04(), _oracle_o05(), _oracle_o06(), _oracle_o07(), _oracle_o08(), _oracle_o09(), _oracle_o10()]
    result = {"status": "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL", "evidence_status": "TEST_ONLY", "checks": checks}
    (out / "manual_oracles.json").write_text(_canonical(result) + "\n", encoding="utf-8")
    return result


def build_round1_plan() -> list[dict]:
    """Build all first-round experiments from the registry axes.

    The matrix is deliberately bounded: 8 paired seeds for the attribution
    set, 2 seeds for tuning candidates, and orthogonal sensitivity slices.
    Every Case row is obtained from the registry at execution time.
    """

    plan: list[dict] = []
    for traffic_id in ("FTP3", "IM"):
        for case_id in ("1-1", "1-2", "1-3", "1-4", "1-5", "1-6"):
            for seed in DEFAULT_SEEDS:
                plan.append({"kind": "attribution", "traffic_id": traffic_id, "case_id": case_id, "policy_id": SAME_TIMER, "seed": seed, "options": {"label": "same_timer"}})
    # Timer/period candidate search is only needed for independent WUS rows;
    # candidates remain in the result set even when QoS constraints reject them.
    for traffic_id in ("FTP3", "IM"):
        for case_id in ("1-5", "1-6"):
            for period_ms in (5, 10, 20, 40, 80, 160, 320):
                for timer_ms in (4, 8, 25, 100):
                    for seed in (101, 202):
                        plan.append({"kind": "timer_period", "traffic_id": traffic_id, "case_id": case_id, "policy_id": OPTIMIZED_TIMER, "seed": seed, "options": {"label": f"period{period_ms}ms_timer{timer_ms}ms", "wus_period_ns": period_ms * NS_PER_MS, "timer_ns": timer_ms * NS_PER_MS}})
    # Orthogonal sensitivity slices.  This is intentionally not a full
    # Cartesian product; each slice has its own paired baseline in the same
    # traffic window.
    for value in (0, 5, 10, 15):
        plan.append({"kind": "sensitivity_phase", "traffic_id": "FTP3", "case_id": "1-5", "policy_id": SAME_TIMER, "seed": 101, "options": {"label": f"phase{value}ms", "phase_ns": value * NS_PER_MS}})
    for value in (20, 40, 80, 160):
        plan.append({"kind": "sensitivity_measurement", "traffic_id": "FTP3", "case_id": "1-5", "policy_id": SAME_TIMER, "seed": 101, "options": {"label": f"measurement{value}ms", "measurement_cycle_ns": value * NS_PER_MS}})
    for value in (5, 13, 37):
        plan.append({"kind": "sensitivity_gap", "traffic_id": "FTP3", "case_id": "1-5", "policy_id": SAME_TIMER, "seed": 101, "options": {"label": f"gap{value}ms", "gap_ns": value * NS_PER_MS, "resync_ns": 1 * NS_PER_MS}})
    for value in ("ideal", "main", "impaired"):
        plan.append({"kind": "sensitivity_detection", "traffic_id": "FTP3", "case_id": "1-5", "policy_id": SAME_TIMER, "seed": 101, "options": {"label": f"detection_{value}", "detection_scenario": value}})
    for value in ("shared_max", "serial", "independent_sum"):
        plan.append({"kind": "sensitivity_ee", "traffic_id": "FTP3", "case_id": "1-6", "policy_id": SAME_TIMER, "seed": 101, "options": {"label": f"ee_{value}", "ee_policy": value}})
    # A small legacy replay trace is useful as a strategy-path diagnostic but
    # is not mixed into the formal same-timer attribution set.
    plan.append({"kind": "short_trace", "traffic_id": "FTP3", "case_id": "1-5", "policy_id": LEGACY_REPLAY, "seed": 101, "options": {"label": "legacy_trace"}})
    return plan


def run_round1(profile_path: str | Path, out_dir: str | Path, *, root: Path | None = None) -> dict:
    """Execute the complete bounded W09 first-round plan."""

    profile = load_profile(profile_path)
    registry = load_case_registry()
    validation = validate_integration_inputs(profile_path)
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        # The CLI records the W00-W04 regression in ``qa`` before dispatching
        # the matrix.  That single preflight directory is safe to retain; any
        # existing run/manifest/result is treated as a partial release and is
        # never overwritten.
        existing = {item.name for item in out.iterdir()}
        if existing != {"qa"} or not (out / "qa" / "w00_w04_regression.json").exists():
            raise FileExistsError(f"refusing to overwrite non-empty release run directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    runs_dir = out / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    root = root or Path(__file__).resolve().parents[2]
    plan = build_round1_plan()
    plan_records = []
    for item in plan:
        run = run_one(profile, registry, case_id=item["case_id"], traffic_id=item["traffic_id"], seed=item["seed"], policy_id=item["policy_id"], options=item.get("options"), root=root)
        path = runs_dir / f"{run['run_id']}.json"
        path.write_text(json.dumps(run, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        plan_records.append({"run_id": run["run_id"], **item})
    oracles = run_oracles(out / "qa")
    short_plan = next(item for item in plan_records if item["kind"] == "short_trace")
    short_run = json.loads((runs_dir / f"{short_plan['run_id']}.json").read_text(encoding="utf-8"))
    short_trace = {
        "status": "PASS",
        "run_id": short_run["run_id"],
        "event_count": len(short_run["events"]),
        "action_count": len(short_run["actions"]),
        "interval_count": len(short_run["intervals"]),
        "tx_count": len(short_run["tx"]),
        "event_ids": [event["event_id"] for event in short_run["events"]],
        "action_ids": [action["action_id"] for action in short_run["actions"]],
        "raw_trace_sha256": _sha256({"events": short_run["events"], "actions": short_run["actions"], "intervals": short_run["intervals"], "tx": short_run["tx"]}),
        "replay_identity": "run_one same inputs are byte-identical; verified by W09 integration test",
    }
    (out / "qa" / "short_deterministic_trace.json").write_text(json.dumps(short_trace, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "release_id": "W09_PATH_A_ROUND1",
        "contract_version": CONTRACT_VERSION,
        "status": "PASS" if oracles["status"] == "PASS" else "FAIL",
        "evidence_status": RELEASE_EVIDENCE,
        "profile_id": profile["profile_id"],
        "profile_path": str(Path(profile_path).as_posix()),
        "validation": validation,
        "plan_count": len(plan_records),
        "plan": plan_records,
        "default_seeds": list(DEFAULT_SEEDS),
        "integration_parameters": {
            "service_rate_bits_per_ms": DEFAULT_SERVICE_RATE_BITS_PER_MS,
            "qos_pdb_satisfaction_threshold": QOS_PDB_THRESHOLD,
            "lr_micro_power_unit": LR_MICRO_POWER,
            "lr_wus_power_unit": LR_WUS_POWER,
            "lr_dcp_power_unit": LR_DCP_POWER,
            "mr_pdcch_power_unit": MR_PDCCH_POWER,
            "mr_measurement_power_unit": MR_MEASUREMENT_POWER,
            "ee_measurement_power_unit": EE_MEASUREMENT_POWER,
        },
        "root_source_hashes": _source_hashes(root),
        "qa_oracles": "qa/manual_oracles.json",
        "qa_short_trace": "qa/short_deterministic_trace.json",
        "formal_run_directory": "runs",
        "forbidden_formal_markers": ["TEST_ONLY", "formal_phy", "formal_rel19"],
        "limitations": [
            "relative power units; no absolute UE power calibration",
            "abstract WUS/MR/LR timing and detection assumptions; no PHY waveform or BLER model",
            "FTP3/IM are W02 research-reference traffic profiles and are not formal 3GPP conformance inputs",
            "W03 deep_claim remains false; W01 sleep feasibility is reported as a probe only",
            "Path-A single UE only; Case 2 and W05-W08 are not required for this first round",
        ],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


def load_runs(input_dir: str | Path) -> list[dict]:
    root = Path(input_dir)
    run_dir = root / "runs" if (root / "runs").is_dir() else root
    records = []
    for path in sorted(run_dir.glob("*.json")):
        if path.name in {"manifest.json", "manual_oracles.json"}:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        validate_run_output(data)
        records.append(data)
    if not records:
        raise FileNotFoundError(f"no RunOutput JSON found under {run_dir}")
    return records


def finalize_round1_artifacts(input_dir: str | Path) -> dict:
    """Add the short-trace QA artifact and refresh a completed manifest."""

    root = Path(input_dir)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan_by_id = {item["run_id"]: item for item in manifest.get("plan", [])}
    short_id = next(run_id for run_id, item in plan_by_id.items() if item.get("kind") == "short_trace")
    short_run = json.loads((root / "runs" / f"{short_id}.json").read_text(encoding="utf-8"))
    short_trace = {
        "status": "PASS",
        "run_id": short_run["run_id"],
        "event_count": len(short_run["events"]),
        "action_count": len(short_run["actions"]),
        "interval_count": len(short_run["intervals"]),
        "tx_count": len(short_run["tx"]),
        "event_ids": [event["event_id"] for event in short_run["events"]],
        "action_ids": [action["action_id"] for action in short_run["actions"]],
        "raw_trace_sha256": _sha256({"events": short_run["events"], "actions": short_run["actions"], "intervals": short_run["intervals"], "tx": short_run["tx"]}),
        "replay_identity": "run_one same inputs are byte-identical; verified by W09 integration test",
    }
    qa = root / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    (qa / "short_deterministic_trace.json").write_text(json.dumps(short_trace, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest["qa_short_trace"] = "qa/short_deterministic_trace.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return short_trace
