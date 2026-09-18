"""Throughput fields.

window_goodput_mbps is a fixed-window completed-payload rate.  It is never
renamed to UPT.  UPT remains null until W00 freezes a session definition
whose completion quantity and service duration match the target template.
"""

from __future__ import annotations

from typing import Any

from .validation import classify_outcome

NS_PER_MS = 1_000_000


UPT_NULL_REASON = (
    "UPT session definition (start/end, multi-packet burst, queue-idle rules) "
    "is not frozen by W00; field is null and must not be aliased to "
    "throughput_mbps or window_goodput_mbps"
)


def window_goodput(
    outcomes: list[dict],
    *,
    start_ns: int,
    end_ns: int,
    cohort_only: bool = True,
) -> dict:
    """Completed payload bits inside the observation window over window time.

    cohort_only=True restricts to packets whose arrival is in [start_ns,end_ns).
    Completion must lie in [start_ns,end_ns) to count toward window goodput.
    """

    if end_ns <= start_ns:
        raise ValueError("end_ns must be > start_ns")
    bits = 0
    n = 0
    for outcome in outcomes:
        if classify_outcome(outcome) != "delivered":
            continue
        completion = outcome.get("completion_ns")
        if completion is None:
            continue
        if cohort_only and not (start_ns <= outcome["arrival_ns"] < end_ns):
            continue
        if start_ns <= completion < end_ns:
            bits += int(outcome.get("size_bits", 0))
            n += 1
    window_s = (end_ns - start_ns) / 1e9
    mbps = (bits / 1e6) / window_s if window_s > 0 else None
    return {
        "window_goodput_mbps": mbps,
        "window_completed_bits": bits,
        "window_completed_packets": n,
        "window_ns": end_ns - start_ns,
        "cohort_only": cohort_only,
        "status": "OK" if end_ns > start_ns else "INVALID_WINDOW",
        "reason": None,
        "definition": "completed payload bits with completion in [start_ns,end_ns) divided by window seconds; NOT UPT",
    }


def upt_placeholder() -> dict:
    """Always-null UPT field with an explicit unfrozen-definition reason."""

    return {
        "upt_mbps": None,
        "session_payload_bits": None,
        "session_service_duration_ns": None,
        "status": "NOT_DEFINED",
        "reason": UPT_NULL_REASON,
    }
