"""W01 service core: causal timeline, interval audit, energy ledger,
FIFO service and auditable trace for the WUS simulator upgrade.

Public functions of this package are the W01 contract interfaces:

* ``audit_intervals(intervals, start_ns, end_ns)`` — position-level
  MR/LR closure, overlap and hole findings with reason codes;
* ``integrate_energy(intervals, increments, start_ns, end_ns)`` — state
  power integration plus additional transition/EE energy with the profile
  EE sharing policies;
* ``serve_fifo(queue, opportunity)`` — capacity-limited FIFO service with
  partial packets across opportunities and TxRecord generation;
* ``select_sleep_state(...)`` — mutually exclusive entry/dwell/exit/resync
  feasibility and cheapest-state selection from known-calendar inputs only;
* ``build_packet_outcomes(...)`` — TxRecord/PacketOutcome mapping with
  packet/bit/resource conservation;
* ``EventHeap`` — stable deterministic event queue in contract order.

This module never defines Rel-19 protocol priorities, never modifies
statistical formulas and never tunes against historical results.
"""

from .reasons import CoreError
from .timebase import (
    NS_PER_MS,
    clip_interval,
    energy_unit_ms,
    ms_to_ns,
    ns_to_ms_exact,
    overlap_interval,
    require_int_ns,
    require_nonnegative_ns,
    require_positive_duration_ns,
    require_window_ns,
)
from .events import DEFAULT_EVENT_ORDER, EventHeap
from .intervals import (
    audit_intervals,
    require_intervals_closed,
    validate_interval_record,
)
from .energy import build_transition, integrate_energy, validate_increment_record
from .sleep import SleepCandidate, SleepDecision, select_sleep_state, wake_feasible
from .service import serve_fifo
from .ledger import (
    assert_mr_ready,
    build_packet_outcomes,
    check_resource_exclusivity,
    require_resources_exclusive,
)
from .trace import TraceWriter, filter_trace, read_trace, summarize_trace

__all__ = [
    "CoreError",
    "NS_PER_MS",
    "clip_interval",
    "energy_unit_ms",
    "ms_to_ns",
    "ns_to_ms_exact",
    "overlap_interval",
    "require_int_ns",
    "require_nonnegative_ns",
    "require_positive_duration_ns",
    "require_window_ns",
    "DEFAULT_EVENT_ORDER",
    "EventHeap",
    "audit_intervals",
    "require_intervals_closed",
    "validate_interval_record",
    "integrate_energy",
    "validate_increment_record",
    "build_transition",
    "SleepCandidate",
    "SleepDecision",
    "select_sleep_state",
    "wake_feasible",
    "serve_fifo",
    "build_packet_outcomes",
    "check_resource_exclusivity",
    "require_resources_exclusive",
    "assert_mr_ready",
    "TraceWriter",
    "read_trace",
    "filter_trace",
    "summarize_trace",
]
