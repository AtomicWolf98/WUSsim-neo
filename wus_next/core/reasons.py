"""Stable reason codes for the W01 service core.

Every check or decision result carries a machine-readable ``code`` plus a
human-readable detail.  Codes are frozen contract vocabulary: consumers may
branch on them; new codes enter through ``handoffs/W01/contract_change.md``.
Reason codes exist so that no check ever degrades to a bare boolean.
"""

from __future__ import annotations

# time / window
TIME_NOT_INTEGER = "TIME_NOT_INTEGER"
TIME_NEGATIVE = "TIME_NEGATIVE"
DURATION_NOT_POSITIVE = "DURATION_NOT_POSITIVE"
WINDOW_NOT_POSITIVE = "WINDOW_NOT_POSITIVE"

# generic record structure
RECORD_FIELD_MISSING = "RECORD_FIELD_MISSING"
RECORD_FIELD_TYPE = "RECORD_FIELD_TYPE"
RECORD_UNKNOWN_FIELD = "RECORD_UNKNOWN_FIELD"
RECORD_ID_INVALID = "RECORD_ID_INVALID"

# events
EVENT_TIME_NOT_INTEGER = "EVENT_TIME_NOT_INTEGER"
EVENT_IN_PAST = "EVENT_IN_PAST"
EVENT_DUPLICATE_ID = "EVENT_DUPLICATE_ID"
EVENT_KIND_UNORDERED = "EVENT_KIND_UNORDERED"
EVENT_DURATION_INVALID = "EVENT_DURATION_INVALID"
TIME_REWIND = "TIME_REWIND"

# interval audit
INTERVAL_OVERLAP = "INTERVAL_OVERLAP"
COVERAGE_HOLE = "COVERAGE_HOLE"
WINDOW_TAIL_HOLE = "WINDOW_TAIL_HOLE"
NO_INTERVALS = "NO_INTERVALS"
INTERVAL_ID_DUPLICATE = "INTERVAL_ID_DUPLICATE"
INTERVAL_OUT_OF_WINDOW = "INTERVAL_OUT_OF_WINDOW"
INTERVALS_NOT_CLOSED = "INTERVALS_NOT_CLOSED"

# energy
NEGATIVE_ENERGY = "NEGATIVE_ENERGY"
NEGATIVE_POWER = "NEGATIVE_POWER"
POWER_NOT_NUMERIC = "POWER_NOT_NUMERIC"
EE_POLICY_CONFLICT = "EE_POLICY_CONFLICT"
TRANSITION_ENERGY_WITHOUT_TIME = "TRANSITION_ENERGY_WITHOUT_TIME"

# sleep selection
EPISODE_WINDOW_TOO_SHORT = "EPISODE_WINDOW_TOO_SHORT"
WAKE_LATENCY_EXCEEDED = "WAKE_LATENCY_EXCEEDED"
NET_SAVING_NON_POSITIVE = "NET_SAVING_NON_POSITIVE"
SLEEP_NO_CANDIDATES = "SLEEP_NO_CANDIDATES"
SLEEP_PHASE_NEGATIVE = "SLEEP_PHASE_NEGATIVE"

# FIFO service / ledger
SERVICE_QUEUE_ELEMENT_INVALID = "SERVICE_QUEUE_ELEMENT_INVALID"
SERVICE_OPPORTUNITY_INVALID = "SERVICE_OPPORTUNITY_INVALID"
SERVICE_OPPORTUNITY_UNKNOWN_FIELD = "SERVICE_OPPORTUNITY_UNKNOWN_FIELD"
SERVICE_ZERO_REMAINING = "SERVICE_ZERO_REMAINING"
SERVICE_NEGATIVE_REMAINING = "SERVICE_NEGATIVE_REMAINING"
TX_PACKET_UNKNOWN = "TX_PACKET_UNKNOWN"
TX_UE_MISMATCH = "TX_UE_MISMATCH"
TX_BEFORE_ARRIVAL = "TX_BEFORE_ARRIVAL"
CONSERVATION_DUPLICATE_BITS = "CONSERVATION_DUPLICATE_BITS"
CONSERVATION_OVERFLOW = "CONSERVATION_OVERFLOW"
DROP_AFTER_COMPLETION = "DROP_AFTER_COMPLETION"
DROP_TIME_INVALID = "DROP_TIME_INVALID"
DEGENERATE_PACKET = "DEGENERATE_PACKET"

# receiver readiness
MR_NOT_READY = "MR_NOT_READY"
SERVICEABLE_STATES = frozenset({"PDCCH", "PDSCH"})


class CoreError(ValueError):
    """Failure carrying a stable reason code; never a bare boolean."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def audit_error(code, ue_id, receiver_id, start_ns, end_ns, interval_ids, detail) -> dict:
    """Build one deterministic audit finding (JSON-compatible)."""

    return {
        "code": code,
        "ue_id": ue_id,
        "receiver_id": receiver_id,
        "start_ns": start_ns,
        "end_ns": end_ns,
        "interval_ids": sorted(interval_ids),
        "detail": detail,
    }
