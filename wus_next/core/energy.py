"""Energy integration over state intervals plus additional-energy increments.

Semantics (contract section 2, W01 task steps 3 and 5):

* state energy: every ``RadioInterval`` contributes
  ``power_unit * clipped_duration_ns / 1e6`` in relative energy unit ms;
  overlapping intervals on one (ue_id, receiver_id) pair are a modeling
  error and fail immediately (never clamped away);
* additional energies (TR 38.840 Table 19 transition energy, EE processing)
  arrive as separate ``EnergyIncrement`` records that add energy without
  occupying MR time.  The additional 450 of a deep transition must never
  also be spread into the interval power (oracle O03);
* interval increments are clipped to the window and scaled by the clipped
  share of their span under the constant-power interpretation, so window
  edges integrate exactly;
* instantaneous edges use ``at_ns`` and count when
  ``start_ns <= at_ns < end_ns`` (an edge at the window end does not count);
* EE sharing is executed per (ue_id, hardware_group) with one policy:
  ``shared_max`` charges the maximum instantaneous power of concurrent
  increments, ``serial`` postpones an overlapping job until the hardware is
  free (explicit added latency), ``independent_sum`` charges every record in
  full.  Merged shared_max records keep all pre-merge source IDs.
"""

from __future__ import annotations

from .timebase import clip_interval, energy_unit_ms, require_window_ns
from .intervals import validate_interval_record
from .reasons import (
    EE_POLICY_CONFLICT,
    NEGATIVE_ENERGY,
    TRANSITION_ENERGY_WITHOUT_TIME,
    CoreError,
    RECORD_FIELD_MISSING,
    RECORD_FIELD_TYPE,
    RECORD_ID_INVALID,
    RECORD_UNKNOWN_FIELD,
)

EE_POLICIES = ("shared_max", "serial", "independent_sum")
INCREASE_ALLOWED_FIELDS = frozenset(
    {
        "increment_id",
        "ue_id",
        "start_ns",
        "end_ns",
        "at_ns",
        "energy_unit_ms",
        "hardware_group",
        "source_event_ids",
        "policy_id",
        "metadata",
    }
)


def validate_increment_record(increment, index):
    """Structural validation of one ``EnergyIncrement`` record."""

    if not isinstance(increment, dict):
        raise CoreError(RECORD_FIELD_MISSING, f"increments[{index}] must be a JSON object")
    unknown = sorted(set(increment) - INCREASE_ALLOWED_FIELDS)
    if unknown:
        raise CoreError(RECORD_UNKNOWN_FIELD, f"increments[{index}] unknown field(s): {', '.join(unknown)}; use metadata")
    for field in ("increment_id", "ue_id", "energy_unit_ms", "hardware_group", "source_event_ids", "policy_id"):
        if field not in increment:
            raise CoreError(RECORD_FIELD_MISSING, f"increments[{index}] missing required field {field!r}")
    for field in ("increment_id", "ue_id", "hardware_group"):
        value = increment[field]
        if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
            raise CoreError(RECORD_ID_INVALID, f"increments[{index}].{field} must be a non-empty identifier without whitespace")
    if increment["policy_id"] not in EE_POLICIES:
        raise CoreError(EE_POLICY_CONFLICT, f"increments[{index}].policy_id must be one of {EE_POLICIES}, got {increment['policy_id']!r}")
    energy = increment["energy_unit_ms"]
    if isinstance(energy, bool) or not isinstance(energy, (int, float)):
        raise CoreError(RECORD_FIELD_TYPE, f"increments[{index}].energy_unit_ms must be numeric")
    if energy < 0:
        raise CoreError(NEGATIVE_ENERGY, f"increments[{index}] has negative energy {energy}")
    source_ids = increment["source_event_ids"]
    if not isinstance(source_ids, list) or len(source_ids) != len(set(source_ids)):
        raise CoreError(RECORD_FIELD_TYPE, f"increments[{index}].source_event_ids must be a list without duplicates")
    for value in source_ids:
        if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
            raise CoreError(RECORD_ID_INVALID, f"increments[{index}].source_event_ids entries must be valid IDs")
    start, end, at = increment.get("start_ns"), increment.get("end_ns"), increment.get("at_ns")
    if start is None and end is None:
        if at is None:
            raise CoreError(RECORD_FIELD_MISSING, f"increments[{index}] needs an interval or at_ns (instantaneous edge)")
        _require_int_ns(at, f"increments[{index}].at_ns")
    elif start is None or end is None:
        raise CoreError(RECORD_FIELD_TYPE, f"increments[{index}] start_ns and end_ns must both be null or both be set")
    else:
        _require_int_ns(start, f"increments[{index}].start_ns")
        _require_int_ns(end, f"increments[{index}].end_ns")
        if end <= start:
            raise CoreError("DURATION_NOT_POSITIVE", f"increments[{index}] has end_ns <= start_ns")
        if at is not None:
            raise CoreError(RECORD_FIELD_TYPE, f"increments[{index}].at_ns must be null for an interval increment")
    if "metadata" in increment and not isinstance(increment["metadata"], dict):
        raise CoreError(RECORD_FIELD_TYPE, f"increments[{index}].metadata must be an object")
    return increment


def _require_int_ns(value, what):
    if isinstance(value, bool) or not isinstance(value, int):
        raise CoreError("TIME_NOT_INTEGER", f"{what} must be an integer ns count, got {type(value).__name__}")
    return value


def _increment_parts(increment):
    """Return (span, at_ns); span=(start,end) for interval records else None."""

    if increment.get("at_ns") is not None:
        return None, increment["at_ns"]
    return (increment["start_ns"], increment["end_ns"]), None


def _clipped_increment(increment, ws, we):
    """Window-clipped contribution of one increment.

    Returns ``None`` when the increment does not intersect the window, else a
    dict with keys ``start_ns``/``end_ns``/``at_ns``/``energy_unit_ms``.  The
    energy of an interval increment scales exactly with the clipped share of
    its span (constant-power interpretation).
    """

    span, at = _increment_parts(increment)
    if span is None:
        if ws <= at < we:
            return {"start_ns": None, "end_ns": None, "at_ns": at, "energy_unit_ms": float(increment["energy_unit_ms"]), "share": 1.0}
        return None
    start, end = span
    clipped = clip_interval(start, end, ws, we)
    if clipped is None:
        return None
    share = (clipped[1] - clipped[0]) / (end - start)
    return {
        "start_ns": clipped[0],
        "end_ns": clipped[1],
        "at_ns": None,
        "energy_unit_ms": float(increment["energy_unit_ms"]) * share,
        "share": share,
    }


def _group_key(increment):
    return (increment["ue_id"], increment["hardware_group"])


def integrate_energy(intervals, increments, start_ns, end_ns):
    """Integrate state power and additional energies over the window."""

    ws, we = require_window_ns(start_ns, end_ns)
    if not isinstance(intervals, list):
        raise CoreError(RECORD_FIELD_MISSING, "intervals must be a list")
    if not isinstance(increments, list):
        raise CoreError(RECORD_FIELD_MISSING, "increments must be a list")

    by_receiver: dict[tuple[str, str], list[dict]] = {}
    for order, interval in enumerate(intervals):
        validate_interval_record(interval, order)
        by_receiver.setdefault((interval["ue_id"], interval["receiver_id"]), []).append(interval)
    for (ue_id, receiver_id), group in sorted(by_receiver.items()):
        ordered = sorted(group, key=lambda item: (item["start_ns"], item["end_ns"], item["interval_id"]))
        for previous, current in zip(ordered, ordered[1:]):
            if current["start_ns"] < previous["end_ns"]:
                raise CoreError(
                    "INTERVAL_OVERLAP",
                    f"cannot integrate overlapping state intervals for {ue_id}/{receiver_id}: "
                    f"{previous['interval_id']} [{previous['start_ns']},{previous['end_ns']}) and "
                    f"{current['interval_id']} [{current['start_ns']},{current['end_ns']})",
                )

    state_energy = 0.0
    state_by_receiver: dict[str, float] = {}
    state_by_state: dict[str, float] = {}
    for (ue_id, receiver_id), group in sorted(by_receiver.items()):
        receiver_energy = 0.0
        for interval in group:
            piece = clip_interval(interval["start_ns"], interval["end_ns"], ws, we)
            if piece is None:
                continue
            amount = energy_unit_ms(interval["power_unit"], piece[1] - piece[0])
            state_energy += amount
            receiver_energy += amount
            state_by_state[interval["state"]] = state_by_state.get(interval["state"], 0.0) + amount
        state_by_receiver[f"{ue_id}/{receiver_id}"] = receiver_energy

    groups: dict[tuple[str, str], list[dict]] = {}
    for order, increment in enumerate(increments):
        validate_increment_record(increment, order)
        groups.setdefault(_group_key(increment), []).append(increment)

    increment_energy = 0.0
    merged_records: list[dict] = []
    group_reports: list[dict] = []
    for (ue_id, hardware_group), group in sorted(groups.items()):
        policies = sorted({increment["policy_id"] for increment in group})
        if len(policies) > 1:
            raise CoreError(
                EE_POLICY_CONFLICT,
                f"hardware group {hardware_group} of {ue_id} mixes EE policies {policies}; one policy per group",
            )
        policy = policies[0]
        if policy == "independent_sum":
            energy, effective = _apply_independent_sum(group, ws, we)
        elif policy == "serial":
            energy, effective = _apply_serial(group, ws, we)
        else:
            energy, effective = _apply_shared_max(group, ws, we)
        increment_energy += energy
        merged_records.extend(effective)
        group_reports.append(
            {
                "ue_id": ue_id,
                "hardware_group": hardware_group,
                "policy_id": policy,
                "increment_count": len(group),
                "energy_unit_ms": energy,
                "merged_count": len(effective),
            }
        )

    total = state_energy + increment_energy
    if total < 0:
        raise CoreError(NEGATIVE_ENERGY, f"total integrated energy is negative ({total})")
    return {
        "valid": True,
        "window": {"start_ns": ws, "end_ns": we, "duration_ns": we - ws},
        "state_energy_unit_ms": state_energy,
        "state_energy_by_receiver": state_by_receiver,
        "state_energy_by_state": state_by_state,
        "increment_energy_unit_ms": increment_energy,
        "energy_by_hardware_group": {
            f"{report['ue_id']}/{report['hardware_group']}": report["energy_unit_ms"] for report in group_reports
        },
        "total_energy_unit_ms": total,
        "merged_increments": merged_records,
        "groups": group_reports,
    }


def _sorted_increments(group):
    return sorted(group, key=lambda item: (item["increment_id"]))


def _apply_independent_sum(group, ws, we):
    energy = 0.0
    effective: list[dict] = []
    for increment in _sorted_increments(group):
        clipped = _clipped_increment(increment, ws, we)
        if clipped is None:
            continue
        energy += clipped["energy_unit_ms"]
        effective.append(_effective_record(increment, clipped))
    return energy, effective


def _apply_serial(group, ws, we):
    energy = 0.0
    effective: list[dict] = []
    cursor = None
    for increment in sorted(group, key=lambda item: (item["start_ns"] if item.get("start_ns") is not None else -1, item["increment_id"])):
        clipped = _clipped_increment(increment, ws, we)
        if clipped is None:
            continue
        if clipped["at_ns"] is not None:
            energy += clipped["energy_unit_ms"]
            effective.append(_effective_record(increment, clipped))
            continue
        span = increment["end_ns"] - increment["start_ns"]
        start = increment["start_ns"] if cursor is None else max(increment["start_ns"], cursor)
        cursor = start + span
        piece = clip_interval(start, cursor, ws, we)
        if piece is None:
            continue
        share = (piece[1] - piece[0]) / span
        amount = float(increment["energy_unit_ms"]) * share
        energy += amount
        effective.append(
            _effective_record(
                increment,
                {"start_ns": piece[0], "end_ns": piece[1], "at_ns": None, "energy_unit_ms": amount},
                original_start=increment["start_ns"],
                deferred_ns=start - increment["start_ns"],
            )
        )
    return energy, effective


def _apply_shared_max(group, ws, we):
    energy = 0.0
    effective: list[dict] = []
    items = []
    for increment in _sorted_increments(group):
        clipped = _clipped_increment(increment, ws, we)
        if clipped is None:
            continue
        if clipped["at_ns"] is not None:
            energy += clipped["energy_unit_ms"]
            effective.append(_effective_record(increment, clipped))
            continue
        start = increment["start_ns"]
        span = increment["end_ns"] - start
        power = float(increment["energy_unit_ms"]) * 1_000_000 / span
        items.append((clipped["start_ns"], clipped["end_ns"], power, increment))
    if items:
        points = sorted({item[0] for item in items} | {item[1] for item in items})
        segments: list[dict] = []
        for left, right in zip(points, points[1:]):
            active = [item for item in items if item[0] <= left and right <= item[1]]
            if not active:
                continue
            power = max(item[2] for item in active)
            ids = sorted(item[3]["increment_id"] for item in active)
            energy += energy_unit_ms(power, right - left)
            if segments and segments[-1]["end_ns"] == left and segments[-1]["_ids"] == ids:
                segments[-1]["end_ns"] = right
            else:
                segments.append({"start_ns": left, "end_ns": right, "_power": power, "_ids": ids})
        for order, segment in enumerate(segments):
            contributing = [increment for increment in group if increment["increment_id"] in segment["_ids"]]
            source_event_ids = sorted({event for increment in contributing for event in increment["source_event_ids"]})
            effective.append(
                {
                    "increment_id": f"{contributing[0]['ue_id']}:{contributing[0]['hardware_group']}#merged{order}",
                    "ue_id": contributing[0]["ue_id"],
                    "start_ns": segment["start_ns"],
                    "end_ns": segment["end_ns"],
                    "at_ns": None,
                    "energy_unit_ms": energy_unit_ms(segment["_power"], segment["end_ns"] - segment["start_ns"]),
                    "hardware_group": contributing[0]["hardware_group"],
                    "source_event_ids": source_event_ids,
                    "policy_id": "shared_max",
                    "metadata": {"merged_from_increment_ids": segment["_ids"]},
                }
            )
    return energy, effective


def _effective_record(increment, clipped, original_start=None, deferred_ns=None):
    metadata = dict(increment.get("metadata") or {})
    if original_start is not None:
        metadata["original_start_ns"] = original_start
        metadata["deferred_ns"] = deferred_ns
    return {
        "increment_id": increment["increment_id"],
        "ue_id": increment["ue_id"],
        "start_ns": clipped.get("start_ns"),
        "end_ns": clipped.get("end_ns"),
        "at_ns": clipped.get("at_ns"),
        "energy_unit_ms": clipped["energy_unit_ms"],
        "hardware_group": increment["hardware_group"],
        "source_event_ids": list(increment["source_event_ids"]),
        "policy_id": increment["policy_id"],
        "metadata": metadata,
    }


def build_transition(
    ue_id,
    start_ns,
    dwell_ns,
    *,
    state,
    floor_power_unit,
    additional_energy_unit_ms,
    ramp_total_ns,
    resync_ns=0,
    resync_power_unit=0.0,
    reason_event_id,
    id_base,
    transition_policy_id="independent_sum",
):
    """Build the canonical mutually exclusive deep/light transition episode.

    TR 38.840 Table 19 defines the *additional* transition energy as the
    excess over the sleep floor covering BOTH ramp edges, with half of the
    total transition time on each edge.  The canonical representation built
    here is therefore:

    * ``[start, start+entry)``        state RAMP,   power = floor
    * ``[start+entry, +dwell)``       sleep state,  power = floor
    * ``[start+entry+dwell, +exit)``  state RAMP,   power = floor
    * optional ``[..., +resync)``     state RESYNC, power = resync power
    * one ``EnergyIncrement`` of the additional energy over the ramp span

    Never also spread the additional energy into the interval power; that
    would double count it (oracle O03 would return 930 instead of 480).
    """

    start = _require_int_ns(start_ns, "transition start_ns")
    dwell = _require_int_ns(dwell_ns, "transition dwell_ns")
    if dwell <= 0:
        raise CoreError("DURATION_NOT_POSITIVE", f"transition dwell must be positive, got {dwell}")
    ramp_total = _require_int_ns(ramp_total_ns, "transition ramp_total_ns")
    if ramp_total < 0:
        raise CoreError("DURATION_NOT_POSITIVE", f"transition ramp_total_ns is negative ({ramp_total})")
    if additional_energy_unit_ms > 0 and ramp_total == 0:
        raise CoreError(
            TRANSITION_ENERGY_WITHOUT_TIME,
            f"transition has {additional_energy_unit_ms} unit ms of additional energy but zero ramp time",
        )
    if not isinstance(state, str) or not state or any(ch.isspace() for ch in state):
        raise CoreError(RECORD_ID_INVALID, f"transition state must be a valid identifier, got {state!r}")
    for label, value in (("reason_event_id", reason_event_id), ("id_base", id_base)):
        if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
            raise CoreError(RECORD_ID_INVALID, f"transition {label} must be a valid identifier, got {value!r}")
    for value, what in (
        (floor_power_unit, "floor_power_unit"),
        (additional_energy_unit_ms, "additional_energy_unit_ms"),
        (resync_power_unit, "resync_power_unit"),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CoreError("POWER_NOT_NUMERIC", f"transition {what} must be numeric")
        if value < 0:
            raise CoreError(NEGATIVE_POWER, f"transition {what} is negative")
    entry = ramp_total // 2
    exit_ns = ramp_total - entry
    sync = _require_int_ns(resync_ns, "transition resync_ns")
    if sync < 0:
        raise CoreError("TIME_NEGATIVE", f"transition resync_ns is negative ({sync})")

    intervals = []
    cursor = start
    if entry > 0:
        intervals.append(_interval(f"{id_base}#entry", ue_id, cursor, cursor + entry, "RAMP", floor_power_unit, reason_event_id))
        cursor += entry
    intervals.append(_interval(f"{id_base}#dwell", ue_id, cursor, cursor + dwell, state, floor_power_unit, reason_event_id))
    cursor += dwell
    if exit_ns > 0:
        intervals.append(_interval(f"{id_base}#exit", ue_id, cursor, cursor + exit_ns, "RAMP", floor_power_unit, reason_event_id))
        cursor += exit_ns
    if sync > 0:
        intervals.append(_interval(f"{id_base}#resync", ue_id, cursor, cursor + sync, "RESYNC", resync_power_unit, reason_event_id))
        cursor += sync
    increments = []
    if ramp_total > 0:
        increments.append(
            {
                "increment_id": f"{id_base}#trans",
                "ue_id": ue_id,
                "start_ns": start,
                "end_ns": start + ramp_total,
                "at_ns": None,
                "energy_unit_ms": additional_energy_unit_ms,
                "hardware_group": "mr_ramp",
                "source_event_ids": [reason_event_id],
                "policy_id": transition_policy_id,
            }
        )
    return intervals, increments


def _interval(interval_id, ue_id, start_ns, end_ns, state, power, reason_event_id):
    return {
        "interval_id": interval_id,
        "ue_id": ue_id,
        "receiver_id": "MR",
        "start_ns": start_ns,
        "end_ns": end_ns,
        "state": state,
        "power_unit": power,
        "reason_event_id": reason_event_id,
    }
