"""Causal Case-1 policy state machine.

Implements the frozen contract interfaces:

* ``initial_state(profile, ue_id) -> state``
* ``step(state, event, observable) -> (state, actions)``

The machine only emits Action records. It freezes MO decisions at MO start,
keeps multiple pending indications, applies detection after rx+gap, and
separates skipped / MDR / FAR / FDR bookkeeping without computing energy.
"""

from __future__ import annotations

import copy
from typing import Any

from .actions import (
    MONITOR_DCP_RX,
    MONITOR_MEASUREMENT_EE,
    MONITOR_MEASUREMENT_NON_EE,
    MONITOR_PDCCH,
    MONITOR_WUS_RX,
    REASON_DRX_CYCLE,
    REASON_IAT_EXPIRY,
    REASON_MEASUREMENT,
    REASON_MO_DETECTION,
    REASON_MO_SKIP_FALLBACK,
    REASON_PACKET,
    REASON_POST_DATA,
    REASON_RADIO,
    REASON_TIMER_EXPIRY,
    REASON_WUS_EFFECTIVE,
    SKIP_ACTIVE_TIME,
    SKIP_MEASUREMENT,
    SKIP_RADIO_NOT_READY,
    SKIP_UNKNOWN,
    SKIP_WUS_BUSY,
    TIMER_DRX_IAT,
    TIMER_DRX_ON,
    TIMER_POST_DATA,
    TIMER_WUS,
    ActionBuilder,
)
from .axes import COUPLING_DCP, COUPLING_NONE, COUPLING_WUS_COUPLED, COUPLING_WUS_INDEPENDENT, resolve_case_axes
from .strategies import IAT_REFRESH_LEGACY_SLOT, StrategyError, resolve_strategy_config


# Public event kinds consumed by W03. Integration/W05 inject detection results.
EVT_PACKET_ARRIVAL = "PACKET_ARRIVAL"
EVT_NEW_TRANSMISSION = "NEW_TRANSMISSION"
EVT_MO_START = "MO_START"
EVT_DETECTION_RESULT = "DETECTION_RESULT"
EVT_TIMER_EXPIRY = "TIMER_EXPIRY"
EVT_MEASUREMENT_WINDOW = "MEASUREMENT_WINDOW"
EVT_TICK = "TICK"  # optional advance for calendar-driven timers

COND_H0 = "H0"
COND_H1 = "H1"
COND_H2 = "H2"
OUT_WAKE = "WAKE"
OUT_NO_WAKE = "NO_WAKE"
OUT_SKIPPED = "SKIPPED"

STATE_SCHEMA_VERSION = "1.0"


class PolicyError(ValueError):
    """Raised for illegal event payloads or non-causal state mutations."""


def _require_int(value: Any, name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PolicyError(f"{name} must be an integer, got {value!r}")
    if value < minimum:
        raise PolicyError(f"{name} must be >= {minimum}, got {value}")
    return value


def _now_ns(observable: dict) -> int:
    if not isinstance(observable, dict):
        raise PolicyError("observable must be an object")
    return _require_int(observable.get("now_ns"), "observable.now_ns")


def _empty_detection_counts() -> dict:
    return {
        "h0_total": 0,
        "h0_false_alarm": 0,
        "h1_total": 0,
        "h1_miss": 0,
        "h1_wake": 0,
        "h2_total": 0,
        "h2_false_detection": 0,
        "h2_wake": 0,
        "h2_no_wake": 0,
        "skipped": 0,
    }


def _empty_state_shell(ue_id: str) -> dict:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "ue_id": ue_id,
        "case_id": None,
        "policy_id": None,
        "axes": None,
        "config": None,
        "on_duration_end_ns": None,
        "iat_end_ns": None,
        "wus_timer_end_ns": None,
        "post_data_timer_end_ns": None,
        "monitor_end_ns": None,
        "pending_indications": [],
        "active_mo": None,
        "last_drx_cycle_start_ns": None,
        "last_measurement_window_start_ns": None,
        "queue_bits_observed_ns": 0,
        "queue_bits": 0,
        "detection_counts": _empty_detection_counts(),
        "skip_reasons": [],
        "readiness_failures": [],
        "decision_log": [],
        "next_seq": 0,
        "evidence_status": "TEST_ONLY",
    }


def initial_state(profile: dict, ue_id: str) -> dict:
    """Create a causal policy state from a contract profile plus selection keys.

    The profile dict must carry ``case_id`` and ``policy_id`` either at the top
    level or under ``policy_selection``. Those keys are W03-local selection
    fields and are not written back into W00 profile files.
    """

    if not isinstance(profile, dict):
        raise PolicyError("profile must be an object")
    if not isinstance(ue_id, str) or not ue_id:
        raise PolicyError("ue_id must be a non-empty string")

    selection = profile.get("policy_selection") if isinstance(profile.get("policy_selection"), dict) else {}
    case_id = profile.get("case_id") or selection.get("case_id")
    policy_id = profile.get("policy_id") or selection.get("policy_id")
    if not case_id or not policy_id:
        raise PolicyError("profile must select case_id and policy_id for W03 initial_state")

    axes = resolve_case_axes(str(case_id), profile.get("case_registry"))
    overrides = profile.get("strategy_overrides")
    if overrides is not None and not isinstance(overrides, dict):
        raise PolicyError("strategy_overrides must be an object when present")
    config = resolve_strategy_config(
        profile,
        axes,
        str(policy_id),
        traffic_profile=profile.get("default_traffic_profile") or selection.get("traffic_profile"),
        overrides=overrides,
    )

    state = _empty_state_shell(ue_id)
    state.update(
        {
            "case_id": axes["case_id"],
            "policy_id": config["policy_id"],
            "axes": {
                "case_id": axes["case_id"],
                "trigger": axes["trigger"],
                "serving_measurement": axes["serving_measurement"],
                "coupling": axes["coupling"],
                "has_cdrx": axes["has_cdrx"],
                "has_dcp": axes["has_dcp"],
                "has_wus": axes["has_wus"],
                "meas_ee": axes["meas_ee"],
            },
            "config": config,
            "known_periods": {
                "cdrx_cycle_ns": config["cdrx_cycle_ns"] if config["has_cdrx"] else None,
                "wus_period_ns": config["wus_period_ns"] if config["has_wus"] or config["has_dcp"] else None,
                "measurement_cycle_ns": config["measurement_cycle_ns"],
                "cdrx_offset_ns": config.get("cdrx_offset_ns", config.get("mo_offset_ns", 0)),
                "wus_offset_ns": config.get("wus_offset_ns", config.get("mo_offset_ns", 0)),
                "measurement_offset_ns": config.get("measurement_offset_ns", config.get("mo_offset_ns", 0)),
            },
            "evidence_status": "ASSUMPTION",
        }
    )
    return state


def _log_decision(state: dict, time_ns: int, kind: str, detail: dict) -> None:
    entry = {"time_ns": int(time_ns), "kind": kind, **detail}
    state["decision_log"].append(entry)
    # Keep logs bounded for long module tests.
    if len(state["decision_log"]) > 256:
        del state["decision_log"][: len(state["decision_log"]) - 256]


def _active_until(state: dict) -> int:
    ends = [0]
    for key in ("on_duration_end_ns", "iat_end_ns", "wus_timer_end_ns", "post_data_timer_end_ns", "monitor_end_ns"):
        value = state.get(key)
        if isinstance(value, int):
            ends.append(value)
    return max(ends)


def _is_active(state: dict, now_ns: int) -> bool:
    return now_ns < _active_until(state)


def _mr_ready_ns(observable: dict, default_now: int) -> int:
    raw = observable.get("mr_ready_ns")
    if raw is None:
        return default_now
    return _require_int(raw, "observable.mr_ready_ns")


def _queue_bits(observable: dict) -> int:
    raw = observable.get("queue_bits", 0)
    if raw is None:
        return 0
    return _require_int(raw, "observable.queue_bits")


def _radio_ready(state: dict, now_ns: int, observable: dict, actions: list, caused_by: str) -> bool:
    ready_at = _mr_ready_ns(observable, now_ns)
    if ready_at <= now_ns:
        return True
    state["readiness_failures"].append(
        {
            "time_ns": now_ns,
            "reason": SKIP_RADIO_NOT_READY,
            "intended_caused_by": caused_by,
            "mr_ready_ns": ready_at,
            "defer_to_ns": ready_at,
        }
    )
    _log_decision(
        state,
        now_ns,
        "READINESS_FAILURE",
        {"reason": SKIP_RADIO_NOT_READY, "mr_ready_ns": ready_at, "caused_by": caused_by},
    )
    # Do not invent a past grant; request sleep only if nothing else is active.
    if not _is_active(state, now_ns):
        actions.append(
            ActionBuilder(state["ue_id"], state["next_seq"]).request_sleep(
                now_ns,
                caused_by,
                {
                    "reason": REASON_RADIO,
                    "earliest_retry_ns": ready_at,
                    "deep_claim": False,
                },
            )
        )
        state["next_seq"] += 1
    return False


def _start_timer_end(state: dict, key: str, now_ns: int, duration_ns: int) -> int:
    end_ns = now_ns + int(duration_ns)
    state[key] = end_ns if state.get(key) is None else max(int(state[key]), end_ns)
    return state[key]


def _push_pending(state: dict, indication: dict) -> None:
    """Append a pending indication without overwriting earlier pending entries."""

    pending = state.setdefault("pending_indications", [])
    pending.append(
        {
            "effective_ns": int(indication["effective_ns"]),
            "source_mo_id": str(indication["source_mo_id"]),
            "mo_start_ns": int(indication["mo_start_ns"]),
            "duration_ns": int(indication["duration_ns"]),
            "condition": indication.get("condition"),
            "wake": bool(indication.get("wake", True)),
        }
    )
    pending.sort(key=lambda item: (item["effective_ns"], item["mo_start_ns"], item["source_mo_id"]))


def _apply_wake_from_indication(
    state: dict,
    now_ns: int,
    indication: dict,
    actions: list,
    *,
    caused_by: str,
) -> None:
    cfg = state["config"]
    axes = state["axes"]
    builder = ActionBuilder(state["ue_id"], state["next_seq"])
    duration: int
    timer_id: str | None = None

    if axes["coupling"] == COUPLING_WUS_INDEPENDENT:
        duration = int(cfg["wus_timer_ns"])
        timer_id = TIMER_WUS
        state["wus_timer_end_ns"] = _start_timer_end(state, "wus_timer_end_ns", now_ns, duration)
        actions.append(
            builder.start_timer(
                now_ns,
                caused_by,
                TIMER_WUS,
                duration,
                restart=True,
                extra={"effective_from_ns": now_ns, "source_mo_id": indication["source_mo_id"]},
            )
        )
        state["next_seq"] = builder.seq
    else:
        # Coupled WUS/DCP/DRX cases: start on-duration after the gap.
        duration = int(cfg["on_duration_ns"])
        state["on_duration_end_ns"] = _start_timer_end(state, "on_duration_end_ns", now_ns, duration)
        actions.append(
            builder.start_timer(
                now_ns,
                caused_by,
                TIMER_DRX_ON,
                duration,
                restart=True,
                extra={"effective_from_ns": now_ns, "source_mo_id": indication["source_mo_id"]},
            )
        )
        state["next_seq"] = builder.seq

    actions.append(
        builder.request_monitor(
            now_ns,
            caused_by,
            {
                "monitor_id": MONITOR_PDCCH,
                "duration_ns": duration,
                "timer_id": timer_id,
                "deep_claim": False,
            },
        )
    )
    state["next_seq"] = builder.seq
    _log_decision(
        state,
        now_ns,
        "WAKE_APPLIED",
        {
            "source_mo_id": indication["source_mo_id"],
            "duration_ns": duration,
            "timer_id": timer_id or TIMER_DRX_ON,
        },
    )


def _drx_grid_on_start(state: dict, now_ns: int) -> int | None:
    cfg = state["config"]
    if not cfg["has_cdrx"] or not cfg.get("use_cdrx_on_grid", True):
        return None
    cycle = int(cfg["cdrx_cycle_ns"])
    offset = int(cfg.get("cdrx_offset_ns", cfg.get("mo_offset_ns", 0))) % cycle
    if now_ns < offset:
        return offset
    if (now_ns - offset) % cycle == 0:
        return now_ns
    return None


def _next_grid_point(now_ns: int, period_ns: int, offset_ns: int = 0) -> int:
    period = int(period_ns)
    if period <= 0:
        raise PolicyError("period_ns must be positive")
    offset = int(offset_ns) % period
    if now_ns <= offset:
        return offset
    return now_ns + (offset - now_ns) % period


def _classify_and_count(state: dict, condition: str, outcome: str) -> None:
    counts = state["detection_counts"]
    if outcome == OUT_SKIPPED:
        counts["skipped"] += 1
        return
    if condition == COND_H0:
        counts["h0_total"] += 1
        if outcome == OUT_WAKE:
            counts["h0_false_alarm"] += 1
    elif condition == COND_H1:
        counts["h1_total"] += 1
        if outcome == OUT_WAKE:
            counts["h1_wake"] += 1
        else:
            counts["h1_miss"] += 1
    elif condition == COND_H2:
        counts["h2_total"] += 1
        if outcome == OUT_WAKE:
            counts["h2_false_detection"] += 1
            counts["h2_wake"] += 1
        else:
            counts["h2_no_wake"] += 1
    else:
        raise PolicyError(f"unknown detection condition {condition!r}")


def _finish_monitoring(state: dict, now_ns: int, actions: list, caused_by: str) -> None:
    if state.get("monitor_end_ns") is not None and now_ns >= state["monitor_end_ns"]:
        state["monitor_end_ns"] = None
        builder = ActionBuilder(state["ue_id"], state["next_seq"])
        actions.append(builder.stop_timer(now_ns, caused_by, TIMER_WUS, "monitor_window_complete"))
        state["next_seq"] = builder.seq


def _maybe_request_sleep(state: dict, now_ns: int, observable: dict, actions: list, caused_by: str) -> None:
    if _is_active(state, now_ns):
        return
    if state.get("pending_indications"):
        # Still waiting on a future indication; allow sleep before effective_ns.
        earliest = state["pending_indications"][0]["effective_ns"]
        if earliest <= now_ns:
            return
    builder = ActionBuilder(state["ue_id"], state["next_seq"])
    ready_at = _mr_ready_ns(observable, now_ns)
    actions.append(
        builder.request_sleep(
            now_ns,
            caused_by,
            {
                "reason": "IDLE_WINDOW",
                "earliest_next_known_ns": _next_known_wakeup_ns(state, now_ns),
                "earliest_radio_ready_ns": ready_at,
                "deep_claim": False,
                "gap_ns": state["config"]["gap_ns"],
            },
        )
    )
    state["next_seq"] = builder.seq


def _next_known_wakeup_ns(state: dict, now_ns: int) -> int:
    cfg = state["config"]
    candidates: list[int] = []
    if cfg["has_cdrx"] and cfg.get("use_cdrx_on_grid", True):
        candidates.append(
            _next_grid_point(
                now_ns,
                cfg["cdrx_cycle_ns"],
                cfg.get("cdrx_offset_ns", cfg.get("mo_offset_ns", 0)),
            )
        )
    if cfg["has_wus"] or cfg["has_dcp"]:
        period = cfg["wus_period_ns"] if cfg["has_wus"] else cfg["cdrx_cycle_ns"]
        candidates.append(
            _next_grid_point(
                now_ns,
                period,
                cfg.get("wus_offset_ns", cfg.get("mo_offset_ns", 0)),
            )
        )
    for item in state.get("pending_indications", []):
        if item["effective_ns"] >= now_ns:
            candidates.append(int(item["effective_ns"]))
    for key in ("on_duration_end_ns", "iat_end_ns", "wus_timer_end_ns", "post_data_timer_end_ns"):
        value = state.get(key)
        if isinstance(value, int) and value >= now_ns:
            candidates.append(value)
    if not candidates:
        return now_ns
    return min(candidates)


def _on_packet_arrival(state: dict, event: dict, observable: dict, actions: list) -> None:
    now_ns = _now_ns(observable)
    event_time = _require_int(event.get("time_ns"), "event.time_ns")
    if event_time > now_ns:
        raise PolicyError("event.time_ns must not be in the future of observable.now_ns")
    if event_time < now_ns:
        raise PolicyError("late event delivery is not allowed; keep causal order")

    packet = event.get("payload") or {}
    if not isinstance(packet, dict):
        raise PolicyError("PACKET_ARRIVAL payload must be an object")
    packet_id = packet.get("packet_id", "unknown-packet")
    size_bits = packet.get("size_bits", 0)
    state["queue_bits"] = _queue_bits(observable)
    state["queue_bits_observed_ns"] = now_ns

    builder = ActionBuilder(state["ue_id"], state["next_seq"])
    cfg = state["config"]
    axes = state["axes"]

    # MO in progress: freeze current WUS decision; do not reverse it.
    active_mo = state.get("active_mo")
    if active_mo is not None and active_mo["mo_start_ns"] <= now_ns < active_mo["mo_end_ns"]:
        _log_decision(
            state,
            now_ns,
            "PACKET_DURING_ACTIVE_MO_IGNORED_FOR_DECISION",
            {
                "packet_id": packet_id,
                "size_bits": size_bits,
                "frozen_target": active_mo["target_frozen"],
                "mo_start_ns": active_mo["mo_start_ns"],
            },
        )
        return

    state["next_seq"] = builder.seq
    _log_decision(state, now_ns, "PACKET_ARRIVAL", {"packet_id": packet_id, "size_bits": size_bits})
    # This event is a gNB-side arrival.  UE timers start only after a PDCCH
    # actually schedules a new transmission; the integrator reports that with
    # NEW_TRANSMISSION.  Starting timers here leaked future network knowledge
    # into the UE and made independent WUS skip the first eligible MO.
    _maybe_request_sleep(state, now_ns, observable, actions, REASON_PACKET)


def _on_new_transmission(state: dict, event: dict, observable: dict, actions: list) -> None:
    """Restart active-time timers after an actually scheduled transmission."""

    now_ns = _now_ns(observable)
    if _require_int(event.get("time_ns"), "event.time_ns") != now_ns:
        raise PolicyError("NEW_TRANSMISSION.time_ns must equal observable.now_ns")
    event_id = str(event.get("event_id") or f"new-tx-{now_ns}")
    cfg = state["config"]
    axes = state["axes"]
    builder = ActionBuilder(state["ue_id"], state["next_seq"])

    if axes["has_cdrx"] and cfg["iat_refresh_mode"] in {IAT_REFRESH_LEGACY_SLOT, "new_transmission"}:
        state["iat_end_ns"] = now_ns + int(cfg["iat_ns"])
        actions.append(builder.start_timer(now_ns, event_id, TIMER_DRX_IAT, int(cfg["iat_ns"]), restart=True))
    if cfg.get("use_wus_timer"):
        state["wus_timer_end_ns"] = now_ns + int(cfg["wus_timer_ns"])
        actions.append(builder.start_timer(now_ns, event_id, TIMER_WUS, int(cfg["wus_timer_ns"]), restart=True, extra={"trigger": "new_transmission"}))
    if cfg.get("use_post_data_timer"):
        state["post_data_timer_end_ns"] = now_ns + int(cfg["post_data_timer_ns"])
        actions.append(builder.start_timer(now_ns, event_id, TIMER_POST_DATA, int(cfg["post_data_timer_ns"]), restart=True, extra={"trigger": "new_transmission"}))

    state["next_seq"] = builder.seq
    _log_decision(state, now_ns, "NEW_TRANSMISSION", {"tx_id": (event.get("payload") or {}).get("tx_id")})


def _maybe_start_drx_cycle(state: dict, now_ns: int, actions: list, caused_by: str) -> None:
    cfg = state["config"]
    if not cfg["has_cdrx"] or not cfg.get("use_cdrx_on_grid", True):
        return
    cycle = int(cfg["cdrx_cycle_ns"])
    offset = int(cfg.get("cdrx_offset_ns", cfg.get("mo_offset_ns", 0))) % cycle
    on_start = None
    if now_ns == offset or (now_ns > offset and (now_ns - offset) % cycle == 0):
        on_start = now_ns
    if on_start is None:
        return
    if state.get("last_drx_cycle_start_ns") == on_start:
        return
    state["last_drx_cycle_start_ns"] = on_start
    if cfg.get("has_wus") or cfg.get("has_dcp"):
        # Coupled WUS/DCP gates the on-duration.  The monitor result starts the
        # on-duration after its configured gap; an unconditional PDCCH window
        # here would reduce Cases 1-2/1-3 to baseline plus extra receiver cost.
        _log_decision(state, now_ns, "DRX_ON_GATED_BY_WAKE_SIGNAL", {"cycle_start_ns": on_start})
        return
    builder = ActionBuilder(state["ue_id"], state["next_seq"])
    duration = int(cfg["on_duration_ns"])
    state["on_duration_end_ns"] = now_ns + duration
    actions.append(builder.start_timer(now_ns, caused_by, TIMER_DRX_ON, duration, restart=True, extra={"source": "drx_cycle"}))
    if cfg["has_cdrx"] and cfg["iat_refresh_mode"] != "off":
        # Entering on-duration does not by itself start IAT in the abstract model;
        # IAT starts on new data. Leave IAT unchanged here.
        pass
    actions.append(
        builder.request_monitor(
            now_ns,
            caused_by,
            {"monitor_id": MONITOR_PDCCH, "duration_ns": duration, "deep_claim": False},
        )
    )
    state["next_seq"] = builder.seq
    _log_decision(state, now_ns, "DRX_ON_START", {"on_duration_ns": duration})


def _maybe_measurement(state: dict, now_ns: int, observable: dict, actions: list, caused_by: str) -> int:
    """Emit a measurement resource request when a measurement window opens.

    Returns 1 if a non-EE measurement occupies this instant, else 0. EE
    measurement is requested on a separate monitor id; cost/sharing is owned
    by W01/W07 and is never priced here.
    """

    cfg = state["config"]
    cycle = int(cfg["measurement_cycle_ns"])
    duration = int(cfg["measurement_duration_ns"])
    offset = int(cfg.get("measurement_offset_ns", cfg.get("mo_offset_ns", 0))) % cycle
    in_window = False
    window_start_ns = None
    if cycle > 0:
        # Anchor windows on the absolute grid starting at 0/offset.
        phase = (now_ns - offset) % cycle
        in_window = 0 <= phase < duration
        if in_window:
            window_start_ns = now_ns - phase

    if not in_window:
        return 0
    # Several causal events can occur inside one measurement window (MO start,
    # detection result, packet arrival and calendar tick).  The configured
    # RRM/RLM activity occurs once per window, not once per event.
    if state.get("last_measurement_window_start_ns") == window_start_ns:
        return 0

    builder = ActionBuilder(state["ue_id"], state["next_seq"])
    if state["axes"]["meas_ee"]:
        monitor_id = MONITOR_MEASUREMENT_EE
        reason = "EE_MEASUREMENT"
    else:
        monitor_id = MONITOR_MEASUREMENT_NON_EE
        reason = REASON_MEASUREMENT

    # Non-EE measurement occupies MR time and can conflict with other MR work.
    # EE measurement is requested on the EE path; it does not count as MR non-EE.
    if not state["axes"]["meas_ee"]:
        if not _radio_ready(state, now_ns, observable, actions, reason):
            return 0
        actions.append(
            builder.request_monitor(
                now_ns,
                caused_by,
                {
                    "monitor_id": monitor_id,
                    "duration_ns": duration,
                    "reason": reason,
                    "deep_claim": False,
                },
            )
        )
        state["last_measurement_window_start_ns"] = window_start_ns
        state["next_seq"] = builder.seq
        _log_decision(state, now_ns, "MEASUREMENT_REQUEST", {"monitor_id": monitor_id, "duration_ns": duration})
        return 1

    actions.append(
        builder.request_monitor(
            now_ns,
            caused_by,
            {
                "monitor_id": monitor_id,
                "duration_ns": duration,
                "reason": reason,
                "hardware_path": "EE",
                "deep_claim": False,
            },
        )
    )
    state["last_measurement_window_start_ns"] = window_start_ns
    state["next_seq"] = builder.seq
    _log_decision(state, now_ns, "EE_MEASUREMENT_REQUEST", {"duration_ns": duration})
    return 0


def _monitor_geometry(cfg: dict, axes: dict) -> tuple[int, list[int], int]:
    """Return per-occasion RX duration, offsets, and logical window span."""

    rx_ns = int(cfg["wus_rx_ns"] if axes["has_wus"] else cfg["dcp_rx_ns"])
    if axes["coupling"] in {COUPLING_WUS_COUPLED, COUPLING_DCP}:
        count = max(1, int(cfg.get("coupled_monitor_count", 1)))
        spacing_ns = max(0, int(cfg.get("coupled_monitor_spacing_ns", 0)))
    else:
        count = 1
        spacing_ns = 0
    offsets = [index * spacing_ns for index in range(count)]
    span_ns = offsets[-1] + rx_ns
    return rx_ns, offsets, span_ns


def _handle_mo_start(state: dict, event: dict, observable: dict, actions: list) -> None:
    now_ns = _now_ns(observable)
    event_time = _require_int(event.get("time_ns"), "event.time_ns")
    if event_time != now_ns:
        raise PolicyError("MO_START.time_ns must equal observable.now_ns")

    cfg = state["config"]
    axes = state["axes"]
    event_id = str(event.get("event_id") or f"mo-{now_ns}")
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        raise PolicyError("MO_START payload must be an object")

    if not (axes["has_wus"] or axes["has_dcp"]):
        raise PolicyError("MO_START is only valid for WUS/DCP cases")

    # Freeze queue observation at MO start. Later packets cannot reverse it.
    queue_bits = _queue_bits(observable)
    other_wus = bool(payload.get("other_wus", False))
    target_frozen = queue_bits > 0
    rx_ns, occasion_offsets_ns, monitor_span_ns = _monitor_geometry(cfg, axes)
    mo_end_ns = now_ns + monitor_span_ns
    mo_id = str(payload.get("mo_id") or f"mo-{axes['case_id']}-{now_ns}")

    # Conflict rules: active time, radio not ready, or independent WUS busy.
    # Non-EE measurement is an MR resource request and does not occupy the LR
    # WUS receiver, so it does not by itself skip a WUS/DCP MO.
    _maybe_measurement(state, now_ns, observable, actions, event_id)

    skip_reason = None
    if _is_active(state, now_ns):
        # Active-time overlap: skip pure WUS MO monitoring.
        if axes["coupling"] in {COUPLING_WUS_COUPLED, COUPLING_WUS_INDEPENDENT, COUPLING_DCP}:
            skip_reason = SKIP_ACTIVE_TIME
    elif not _radio_ready(state, now_ns, observable, actions, event_id):
        skip_reason = SKIP_RADIO_NOT_READY
    elif state.get("monitor_end_ns") is not None and now_ns < state["monitor_end_ns"]:
        skip_reason = SKIP_WUS_BUSY

    if skip_reason:
        state["skip_reasons"].append({"time_ns": now_ns, "reason": skip_reason, "mo_id": mo_id})
        _classify_and_count(state, COND_H0, OUT_SKIPPED)
        builder = ActionBuilder(state["ue_id"], state["next_seq"])
        # Coupled company abstraction: skipped MO still schedules a wake after gap.
        if cfg.get("coupled_skip_fallback") and axes["coupling"] in {COUPLING_WUS_COUPLED, COUPLING_DCP}:
            delay = int(cfg["gap_ns"] if axes["has_wus"] else cfg["dcp_gap_ns"])
            effective_ns = now_ns + monitor_span_ns + delay
            _push_pending(
                state,
                {
                    "effective_ns": effective_ns,
                    "source_mo_id": mo_id,
                    "mo_start_ns": now_ns,
                    "duration_ns": int(cfg["on_duration_ns"]),
                    "condition": None,
                    "wake": True,
                    "fallback": True,
                },
            )
            actions.append(
                builder.request_sleep(
                    now_ns,
                    event_id,
                    {
                        "reason": REASON_MO_SKIP_FALLBACK,
                        "skip_reason": skip_reason,
                        "wake_effective_ns": effective_ns,
                        "deep_claim": False,
                    },
                )
            )
            _log_decision(
                state,
                now_ns,
                "MO_SKIPPED_WITH_FALLBACK",
                {"reason": skip_reason, "wake_effective_ns": effective_ns, "mo_id": mo_id},
            )
        else:
            actions.append(
                builder.request_sleep(
                    now_ns,
                    event_id,
                    {
                        "reason": skip_reason,
                        "mo_id": mo_id,
                        "deep_claim": False,
                    },
                )
            )
            _log_decision(state, now_ns, "MO_SKIPPED", {"reason": skip_reason, "mo_id": mo_id})
        state["next_seq"] = builder.seq
        state["active_mo"] = None
        return

    # Begin monitoring.
    state["monitor_end_ns"] = mo_end_ns
    state["active_mo"] = {
        "mo_id": mo_id,
        "mo_start_ns": now_ns,
        "mo_end_ns": mo_end_ns,
        "target_frozen": target_frozen,
        "other_wus": other_wus,
        "queue_bits_frozen": queue_bits,
        "rx_ns": rx_ns,
    }
    builder = ActionBuilder(state["ue_id"], state["next_seq"])
    monitor_id = MONITOR_WUS_RX if axes["has_wus"] else MONITOR_DCP_RX
    actions.append(
        builder.request_monitor(
            now_ns,
            event_id,
            {
                "monitor_id": monitor_id,
                "duration_ns": rx_ns,
                "occasion_offsets_ns": occasion_offsets_ns,
                "monitor_span_ns": monitor_span_ns,
                "target_frozen": target_frozen,
                "other_wus": other_wus,
                "deep_claim": False,
            },
        )
    )
    actions.append(
        builder.start_timer(
            now_ns,
            event_id,
            TIMER_WUS,
            monitor_span_ns,
            restart=True,
            extra={"role": "monitor_window"},
        )
    )
    state["next_seq"] = builder.seq
    _log_decision(
        state,
        now_ns,
        "MO_START_MONITOR",
        {
            "mo_id": mo_id,
            "target_frozen": target_frozen,
            "other_wus": other_wus,
            "queue_bits_frozen": queue_bits,
            "mo_end_ns": mo_end_ns,
        },
    )


def _handle_detection_result(state: dict, event: dict, observable: dict, actions: list) -> None:
    now_ns = _now_ns(observable)
    event_time = _require_int(event.get("time_ns"), "event.time_ns")
    if event_time != now_ns:
        raise PolicyError("DETECTION_RESULT.time_ns must equal observable.now_ns")

    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        raise PolicyError("DETECTION_RESULT payload must be an object")
    event_id = str(event.get("event_id") or f"det-{now_ns}")

    mo_id = payload.get("mo_id")
    active = state.get("active_mo")
    if active is not None:
        if mo_id is None:
            mo_id = active["mo_id"]
        elif mo_id != active["mo_id"]:
            raise PolicyError("DETECTION_RESULT.mo_id does not match active MO")
        if now_ns < active["mo_end_ns"]:
            raise PolicyError("DETECTION_RESULT arrived before MO end; keep causal order")
    elif mo_id is None:
        raise PolicyError("DETECTION_RESULT requires mo_id when no active MO is stored")

    condition = payload.get("condition")
    outcome = payload.get("outcome")
    if condition not in {COND_H0, COND_H1, COND_H2}:
        raise PolicyError(f"invalid detection condition {condition!r}")
    if outcome not in {OUT_WAKE, OUT_NO_WAKE}:
        raise PolicyError(f"invalid detection outcome {outcome!r}")

    # Causality: frozen target from MO start must dominate over queue now.
    if active is not None and active["target_frozen"] and condition == COND_H0:
        raise PolicyError("active MO froze a target; H0 contradicts frozen_target")
    if active is not None and not active["target_frozen"] and condition == COND_H1:
        # Allow explicit H1 from external detector only if it also freezes a
        # target result (network may page without local queue). Treat as wake.
        pass

    _classify_and_count(state, condition, outcome)
    _finish_monitoring(state, now_ns, actions, event_id)

    delay_ns = int(state["config"]["gap_ns"] if state["axes"]["has_wus"] else state["config"]["dcp_gap_ns"])
    effective_ns = now_ns + delay_ns
    mo_start_ns = active["mo_start_ns"] if active is not None else _require_int(payload.get("mo_start_ns"), "payload.mo_start_ns")
    source_mo_id = str(mo_id)

    if outcome == OUT_WAKE:
        _push_pending(
            state,
            {
                "effective_ns": effective_ns,
                "source_mo_id": source_mo_id,
                "mo_start_ns": mo_start_ns,
                "duration_ns": int(
                    state["config"]["wus_timer_ns"]
                    if state["axes"]["coupling"] == COUPLING_WUS_INDEPENDENT
                    else state["config"]["on_duration_ns"]
                ),
                "condition": condition,
                "wake": True,
            },
        )
        builder = ActionBuilder(state["ue_id"], state["next_seq"])
        actions.append(
            builder.request_sleep(
                now_ns,
                event_id,
                {
                    "reason": REASON_MO_DETECTION,
                    "wake_effective_ns": effective_ns,
                    "gap_ns": delay_ns,
                    "condition": condition,
                    "deep_claim": False,
                },
            )
        )
        state["next_seq"] = builder.seq
        _log_decision(
            state,
            now_ns,
            "DETECTION_WAKE_QUEUED",
            {"mo_id": source_mo_id, "effective_ns": effective_ns, "condition": condition},
        )
    else:
        _log_decision(
            state,
            now_ns,
            "DETECTION_NO_WAKE",
            {"mo_id": source_mo_id, "condition": condition},
        )

    state["active_mo"] = None
    _maybe_request_sleep(state, now_ns, observable, actions, event_id)


def _handle_timer_expiry(state: dict, event: dict, observable: dict, actions: list) -> None:
    now_ns = _now_ns(observable)
    event_time = _require_int(event.get("time_ns"), "event.time_ns")
    if event_time != now_ns:
        raise PolicyError("TIMER_EXPIRY.time_ns must equal observable.now_ns")
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        raise PolicyError("TIMER_EXPIRY payload must be an object")
    timer_id = payload.get("timer_id")
    event_id = str(event.get("event_id") or f"tmr-{now_ns}")

    mapping = {
        TIMER_DRX_ON: "on_duration_end_ns",
        TIMER_DRX_IAT: "iat_end_ns",
        TIMER_WUS: "wus_timer_end_ns",
        TIMER_POST_DATA: "post_data_timer_end_ns",
    }
    if timer_id not in mapping:
        raise PolicyError(f"unknown timer_id {timer_id!r}")
    key = mapping[timer_id]
    end_ns = state.get(key)
    if end_ns is None:
        # Expiry of an already-stopped timer is ignored.
        _log_decision(state, now_ns, "TIMER_EXPIRY_IGNORED", {"timer_id": timer_id})
        return
    if now_ns < end_ns:
        raise PolicyError(f"timer {timer_id} expires at {end_ns}, not {now_ns}")
    # Accept now_ns >= end_ns and close the timer.
    state[key] = None
    builder = ActionBuilder(state["ue_id"], state["next_seq"])
    actions.append(builder.stop_timer(now_ns, event_id, timer_id, REASON_TIMER_EXPIRY))
    state["next_seq"] = builder.seq
    _log_decision(state, now_ns, "TIMER_EXPIRED", {"timer_id": timer_id, "end_ns": end_ns})
    _maybe_request_sleep(state, now_ns, observable, actions, event_id)


def _handle_measurement_window(state: dict, event: dict, observable: dict, actions: list) -> None:
    now_ns = _now_ns(observable)
    if _require_int(event.get("time_ns"), "event.time_ns") != now_ns:
        raise PolicyError("MEASUREMENT_WINDOW.time_ns must equal observable.now_ns")
    event_id = str(event.get("event_id") or f"meas-{now_ns}")
    _maybe_measurement(state, now_ns, observable, actions, event_id)
    _maybe_request_sleep(state, now_ns, observable, actions, event_id)


def _apply_due_pending(state: dict, now_ns: int, observable: dict, actions: list) -> None:
    pending = state.get("pending_indications") or []
    if not pending:
        return
    due = [item for item in pending if item["effective_ns"] <= now_ns]
    if not due:
        return
    remaining = [item for item in pending if item["effective_ns"] > now_ns]
    state["pending_indications"] = remaining
    for item in due:
        if not item.get("wake", True):
            continue
        if not _radio_ready(state, now_ns, observable, actions, REASON_WUS_EFFECTIVE):
            # Keep the indication for a later retry without moving time back.
            _push_pending(state, item)
            continue
        _apply_wake_from_indication(state, now_ns, item, actions, caused_by=REASON_WUS_EFFECTIVE)


def _handle_tick(state: dict, event: dict, observable: dict, actions: list) -> None:
    now_ns = _now_ns(observable)
    if _require_int(event.get("time_ns"), "event.time_ns") != now_ns:
        raise PolicyError("TICK.time_ns must equal observable.now_ns")
    event_id = str(event.get("event_id") or f"tick-{now_ns}")
    # Close expired timers even if the integrator did not inject TIMER_EXPIRY.
    for timer_id, key in (
        (TIMER_DRX_ON, "on_duration_end_ns"),
        (TIMER_DRX_IAT, "iat_end_ns"),
        (TIMER_WUS, "wus_timer_end_ns"),
        (TIMER_POST_DATA, "post_data_timer_end_ns"),
    ):
        end_ns = state.get(key)
        if isinstance(end_ns, int) and now_ns >= end_ns:
            state[key] = None
            builder = ActionBuilder(state["ue_id"], state["next_seq"])
            actions.append(builder.stop_timer(now_ns, event_id, timer_id, REASON_TIMER_EXPIRY))
            state["next_seq"] = builder.seq

    if state["config"]["iat_refresh_mode"] == IAT_REFRESH_LEGACY_SLOT and _is_active(state, now_ns) and state["config"]["has_cdrx"]:
        # Legacy connected_v2 refreshes IAT every active slot.
        builder = ActionBuilder(state["ue_id"], state["next_seq"])
        state["iat_end_ns"] = now_ns + int(state["config"]["iat_ns"])
        actions.append(builder.start_timer(now_ns, event_id, TIMER_DRX_IAT, int(state["config"]["iat_ns"]), restart=True))
        state["next_seq"] = builder.seq

    _maybe_start_drx_cycle(state, now_ns, actions, event_id)
    _maybe_measurement(state, now_ns, observable, actions, event_id)
    _apply_due_pending(state, now_ns, observable, actions)
    _maybe_request_sleep(state, now_ns, observable, actions, event_id)


_HANDLERS = {
    EVT_PACKET_ARRIVAL: _on_packet_arrival,
    EVT_NEW_TRANSMISSION: _on_new_transmission,
    EVT_MO_START: _handle_mo_start,
    EVT_DETECTION_RESULT: _handle_detection_result,
    EVT_TIMER_EXPIRY: _handle_timer_expiry,
    EVT_MEASUREMENT_WINDOW: _handle_measurement_window,
    EVT_TICK: _handle_tick,
}


def step(state: dict, event: dict, observable: dict) -> tuple[dict, list[dict]]:
    """Advance the policy by one causal event and return (state, actions)."""

    if not isinstance(state, dict) or state.get("schema_version") != STATE_SCHEMA_VERSION:
        raise PolicyError("state must be created by initial_state")
    if not isinstance(event, dict):
        raise PolicyError("event must be an object")
    kind = event.get("kind")
    if kind not in _HANDLERS:
        raise PolicyError(f"unsupported event kind {kind!r}")
    now_ns = _now_ns(observable)
    if _require_int(event.get("time_ns"), "event.time_ns") != now_ns:
        raise PolicyError("event.time_ns must equal observable.now_ns for causal delivery")

    # Defensive copy so callers can keep prior snapshots in tests/handoffs.
    new_state = copy.deepcopy(state)
    actions: list[dict] = []
    new_state["queue_bits"] = _queue_bits(observable)
    new_state["queue_bits_observed_ns"] = now_ns
    _HANDLERS[kind](new_state, event, observable, actions)
    return new_state, actions


def action_kinds(actions: list[dict]) -> list[str]:
    return [item["kind"] for item in actions]


def summarize_counts(state: dict) -> dict:
    """Return detection counters plus derived rates with null-safe semantics.

    Rates are null when the denominator is zero. Skipped trials are excluded
    from MDR/FAR/FDR denominators. This is bookkeeping only, not a KPI run.
    """

    c = state["detection_counts"]
    mdr = (c["h1_miss"] / c["h1_total"]) if c["h1_total"] else None
    far = (c["h0_false_alarm"] / c["h0_total"]) if c["h0_total"] else None
    fdr = (c["h2_false_detection"] / c["h2_total"]) if c["h2_total"] else None
    return {
        "counts": dict(c),
        "mdr": mdr,
        "far": far,
        "fdr": fdr,
        "skipped": c["skipped"],
        "evidence_status": "TEST_ONLY",
        "note": "bookkeeping helper for module tests; W04 owns official KPIs",
    }
