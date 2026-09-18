"""Sleep-state feasibility and selection with mutually exclusive phases.

The selector never reads traffic or detection results: its inputs are the
causal horizon ``now_ns``, the next event known from the calendar
``next_event_ns`` and device-capability candidates.  Each candidate declares
its sleep power, the entry / exit / resync phase durations, the additional
transition energy (excess over the floor, both ramp edges included) and a
deterministic rank.  The four phases are mutually exclusive:

    entry -> dwell -> exit -> resync

* fresh-episode feasibility: ``available > entry + exit + resync``
  (TR 38.840 Table 18 requires a sleep interval strictly larger than the
  total transition time; a zero dwell cannot pay for the ramp),
* wake feasibility: the device can become ready before the first usable
  PDCCH opportunity iff ``exit + resync <= available`` (and, when provided,
  ``exit + resync <= wake_latency_limit_ns``),
* net-saving rule: a candidate wins only when its estimated episode energy
  beats staying in the reference state (default micro, immediate
  transition); ties break on the candidate rank, then on the state name.

Every excluded candidate carries a reason code; the decision is a structure,
never a bare boolean.  Oracle O08 is executed by :func:`wake_feasible`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .timebase import require_int_ns, require_window_ns
from .reasons import (
    EPISODE_WINDOW_TOO_SHORT,
    NET_SAVING_NON_POSITIVE,
    NEGATIVE_POWER,
    SLEEP_NO_CANDIDATES,
    SLEEP_PHASE_NEGATIVE,
    WAKE_LATENCY_EXCEEDED,
    CoreError,
    RECORD_ID_INVALID,
)

SLEEP_PHASE_FIELDS = ("entry_ns", "exit_ns", "resync_ns")


@dataclass(frozen=True)
class SleepCandidate:
    """One device-capability sleep state the caller offers for evaluation."""

    state: str
    power_unit: float
    entry_ns: int = 0
    exit_ns: int = 0
    resync_ns: int = 0
    additional_energy_unit_ms: float = 0.0
    rank: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.state, str) or not self.state or any(ch.isspace() for ch in self.state):
            raise CoreError(RECORD_ID_INVALID, f"sleep candidate state must be a valid identifier, got {self.state!r}")
        for value, name in (
            (self.power_unit, "power_unit"),
            (self.additional_energy_unit_ms, "additional_energy_unit_ms"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CoreError("POWER_NOT_NUMERIC", f"sleep candidate {self.state} {name} must be numeric")
            if value < 0:
                raise CoreError(NEGATIVE_POWER, f"sleep candidate {self.state} {name} is negative")
        for name in SLEEP_PHASE_FIELDS:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise CoreError("TIME_NOT_INTEGER", f"sleep candidate {self.state} {name} must be an integer ns count")
            if value < 0:
                raise CoreError(SLEEP_PHASE_NEGATIVE, f"sleep candidate {self.state} {name} is negative ({value})")


def wake_feasible(exit_ns, resync_ns, window_ns) -> bool:
    """True when a wake with ``exit + resync`` fits inside ``window_ns``.

    Oracle O08: deep exit 10 ms + resync 1 ms needs 11 ms, so a 5 ms gap is
    infeasible and a 13 ms gap is feasible.  This checks the wake side only;
    entry/dwell feasibility is a separate fresh-episode check.
    """

    _, window_end = require_window_ns(0, window_ns)
    exit_value = require_int_ns(exit_ns, "wake exit_ns")
    sync_value = require_int_ns(resync_ns, "wake resync_ns")
    if exit_value < 0 or sync_value < 0:
        raise CoreError(SLEEP_PHASE_NEGATIVE, "wake exit/resync must not be negative")
    return (exit_value + sync_value) <= window_end


@dataclass
class SleepDecision:
    """Structured selection outcome with per-candidate exclusion reasons."""

    now_ns: int
    next_event_ns: int
    available_ns: int
    reference_state: str
    reference_cost_unit_ms: float
    state: str
    feasible: bool
    net_saving_unit_ms: float
    wake_command_deadline_ns: int | None
    wake_ready_ns: int | None
    dwell_ns: int | None
    overhead_ns: int | None
    fallback_code: str | None
    fallback_detail: str | None
    evaluated: list = field(default_factory=list)


def select_sleep_state(
    now_ns,
    next_event_ns,
    candidates,
    *,
    reference_state="micro",
    reference_power_unit,
    resync_power_unit=0.0,
    wake_latency_limit_ns=None,
) -> SleepDecision:
    """Evaluate candidates and return the feasible cheapest sleep state.

    ``next_event_ns`` is the next MR event known from the calendar (never a
    future traffic or detection result).  ``wake_latency_limit_ns``, when
    given, is the device wake-latency capability: ``exit + resync`` must fit
    inside it.  ``resync_power_unit`` is charged during the resync phase
    (serving-cell SSB/CSI processing in the current company assumptions).
    """

    now = require_int_ns(now_ns, "now_ns")
    deadline = require_int_ns(next_event_ns, "next_event_ns")
    if deadline <= now:
        raise CoreError("WINDOW_NOT_POSITIVE", f"next_event_ns {deadline} is not after now_ns {now}")
    for value, what in ((reference_power_unit, "reference_power_unit"), (resync_power_unit, "resync_power_unit")):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CoreError("POWER_NOT_NUMERIC", f"{what} must be numeric")
        if value < 0:
            raise CoreError(NEGATIVE_POWER, f"{what} is negative")
    if not isinstance(reference_state, str) or not reference_state or any(ch.isspace() for ch in reference_state):
        raise CoreError(RECORD_ID_INVALID, f"reference_state must be a valid identifier, got {reference_state!r}")
    if not isinstance(candidates, list):
        raise CoreError(RECORD_FIELD_MISSING, "candidates must be a list")
    limit = None if wake_latency_limit_ns is None else require_int_ns(wake_latency_limit_ns, "wake_latency_limit_ns")

    available = deadline - now
    reference_cost = float(reference_power_unit) * available / 1_000_000

    evaluated: list[dict] = []
    best = None
    for candidate in candidates:
        if not isinstance(candidate, SleepCandidate):
            raise CoreError(RECORD_FIELD_TYPE, "candidates must be SleepCandidate instances")
        overhead = candidate.entry_ns + candidate.exit_ns + candidate.resync_ns
        entry = {
            "state": candidate.state,
            "power_unit": candidate.power_unit,
            "entry_ns": candidate.entry_ns,
            "exit_ns": candidate.exit_ns,
            "resync_ns": candidate.resync_ns,
            "additional_energy_unit_ms": candidate.additional_energy_unit_ms,
            "overhead_ns": overhead,
        }
        wake_ok = (candidate.exit_ns + candidate.resync_ns) <= available
        entry["wake_feasible_in_window"] = wake_ok
        if limit is not None:
            entry["wake_latency_ok"] = (candidate.exit_ns + candidate.resync_ns) <= limit
        if available <= overhead:
            entry.update(
                feasible=False,
                code=EPISODE_WINDOW_TOO_SHORT,
                detail=(
                    f"episode overhead {overhead} ns exceeds the {available} ns before the next known event "
                    f"(dwell would be {available - overhead} ns); wake-in-time alone: {wake_ok}"
                ),
            )
        elif limit is not None and (candidate.exit_ns + candidate.resync_ns) > limit:
            entry.update(
                feasible=False,
                code=WAKE_LATENCY_EXCEEDED,
                detail=f"exit+resync {candidate.exit_ns + candidate.resync_ns} ns exceeds the wake latency limit {limit} ns",
            )
        else:
            dwell = available - overhead
            cost = (
                float(candidate.power_unit) * (available - candidate.resync_ns) / 1_000_000
                + float(candidate.additional_energy_unit_ms)
                + float(resync_power_unit) * candidate.resync_ns / 1_000_000
            )
            entry.update(feasible=True, dwell_ns=dwell, cost_unit_ms=cost)
            if cost < reference_cost:
                key = (cost, candidate.rank, candidate.state)
                if best is None or key < best[0]:
                    best = (key, candidate, dwell, cost, entry)
        evaluated.append(entry)

    if not candidates:
        return SleepDecision(
            now_ns=now,
            next_event_ns=deadline,
            available_ns=available,
            reference_state=reference_state,
            reference_cost_unit_ms=reference_cost,
            state=reference_state,
            feasible=False,
            net_saving_unit_ms=0.0,
            wake_command_deadline_ns=None,
            wake_ready_ns=None,
            dwell_ns=None,
            overhead_ns=None,
            fallback_code=SLEEP_NO_CANDIDATES,
            fallback_detail="no sleep candidates were supplied; staying in the reference state",
            evaluated=evaluated,
        )

    if best is not None:
        _, candidate, dwell, cost, best_entry = best
        return SleepDecision(
            now_ns=now,
            next_event_ns=deadline,
            available_ns=available,
            reference_state=reference_state,
            reference_cost_unit_ms=reference_cost,
            state=candidate.state,
            feasible=True,
            net_saving_unit_ms=reference_cost - cost,
            wake_command_deadline_ns=deadline - candidate.exit_ns - candidate.resync_ns,
            wake_ready_ns=deadline,
            dwell_ns=dwell,
            overhead_ns=candidate.entry_ns + candidate.exit_ns + candidate.resync_ns,
            fallback_code=None,
            fallback_detail=None,
            evaluated=evaluated,
        )

    feasible_any = any(entry.get("feasible") for entry in evaluated)
    if feasible_any:
        fallback_code, detail = NET_SAVING_NON_POSITIVE, (
            "no feasible candidate beats the reference-state energy over this gap"
        )
    else:
        fallback_code, detail = _primary_exclusion(evaluated)
    return SleepDecision(
        now_ns=now,
        next_event_ns=deadline,
        available_ns=available,
        reference_state=reference_state,
        reference_cost_unit_ms=reference_cost,
        state=reference_state,
        feasible=False,
        net_saving_unit_ms=0.0,
        wake_command_deadline_ns=None,
        wake_ready_ns=None,
        dwell_ns=None,
        overhead_ns=None,
        fallback_code=fallback_code,
        fallback_detail=detail,
        evaluated=evaluated,
    )


def _primary_exclusion(evaluated):
    """Reason code of the excluded candidate that would have won.

    The primary reason is taken from the candidate with the largest phase
    overhead (the deepest transition), tie-broken on the state name; for a
    deeper candidate the wake-side result is already visible in its detail.
    """

    excluded = [entry for entry in evaluated if not entry.get("feasible")]
    ordered = sorted(excluded, key=lambda entry: (-entry["overhead_ns"], entry["state"]))
    for entry in ordered:
        return entry["code"], entry["detail"]
    return EPISODE_WINDOW_TOO_SHORT, "no candidate fits the gap; staying in the reference state"
