"""Action builders and timer identifier constants for Case 1 policy.

Only contract Action kinds are emitted:
REQUEST_MONITOR / REQUEST_SLEEP / START_TIMER / STOP_TIMER.
The policy never integrates energy or computes KPI values.
"""

from __future__ import annotations

from typing import Any


REQUEST_MONITOR = "REQUEST_MONITOR"
REQUEST_SLEEP = "REQUEST_SLEEP"
START_TIMER = "START_TIMER"
STOP_TIMER = "STOP_TIMER"

TIMER_DRX_ON = "drx_on_duration"
TIMER_DRX_IAT = "drx_inactivity"
TIMER_WUS = "wus_timer"
TIMER_POST_DATA = "post_data_timer"

MONITOR_PDCCH = "PDCCH"
MONITOR_MEASUREMENT_NON_EE = "MEASUREMENT_NON_EE"
MONITOR_MEASUREMENT_EE = "MEASUREMENT_EE"
MONITOR_WUS_RX = "WUS_RX"
MONITOR_DCP_RX = "DCP_RX"

SKIP_ACTIVE_TIME = "ACTIVE_TIME_CONFLICT"
SKIP_MEASUREMENT = "MEASUREMENT_CONFLICT"
SKIP_RADIO_NOT_READY = "RADIO_NOT_READY"
SKIP_WUS_BUSY = "WUS_BUSY"
SKIP_UNKNOWN = "UNKNOWN"

REASON_PACKET = "PACKET_ARRIVAL"
REASON_MO_DETECTION = "MO_DETECTION"
REASON_MO_SKIP_FALLBACK = "MO_SKIP_FALLBACK"
REASON_DRX_CYCLE = "DRX_CYCLE"
REASON_MEASUREMENT = "MEASUREMENT_CALENDAR"
REASON_IAT_EXPIRY = "IAT_EXPIRY"
REASON_TIMER_EXPIRY = "TIMER_EXPIRY"
REASON_WUS_EFFECTIVE = "WUS_EFFECTIVE"
REASON_POST_DATA = "POST_DATA"
REASON_RADIO = "RADIO_READINESS"


def _payload_dict(payload: Any) -> dict:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        return {"value": payload}
    return dict(payload)


class ActionBuilder:
    """Allocate stable action_ids inside one policy episode."""

    def __init__(self, ue_id: str, start_seq: int = 0) -> None:
        self.ue_id = ue_id
        self.seq = int(start_seq)

    def next_id(self) -> str:
        self.seq += 1
        return f"act-{self.ue_id}-{self.seq}"

    def request_monitor(self, time_ns: int, caused_by: str, payload: dict | None = None) -> dict:
        return {
            "action_id": self.next_id(),
            "time_ns": int(time_ns),
            "ue_id": self.ue_id,
            "kind": REQUEST_MONITOR,
            "payload": _payload_dict(payload),
            "caused_by": str(caused_by),
        }

    def request_sleep(self, time_ns: int, caused_by: str, payload: dict | None = None) -> dict:
        return {
            "action_id": self.next_id(),
            "time_ns": int(time_ns),
            "ue_id": self.ue_id,
            "kind": REQUEST_SLEEP,
            "payload": _payload_dict(payload),
            "caused_by": str(caused_by),
        }

    def start_timer(self, time_ns: int, caused_by: str, timer_id: str, duration_ns: int, restart: bool = False, extra: dict | None = None) -> dict:
        payload = {
            "timer_id": timer_id,
            "duration_ns": int(duration_ns),
            "restart": bool(restart),
            "end_ns": int(time_ns) + int(duration_ns),
        }
        if extra:
            payload.update(_payload_dict(extra))
        return {
            "action_id": self.next_id(),
            "time_ns": int(time_ns),
            "ue_id": self.ue_id,
            "kind": START_TIMER,
            "payload": payload,
            "caused_by": str(caused_by),
        }

    def stop_timer(self, time_ns: int, caused_by: str, timer_id: str, reason: str | None = None) -> dict:
        payload: dict[str, Any] = {"timer_id": timer_id}
        if reason is not None:
            payload["reason"] = reason
        return {
            "action_id": self.next_id(),
            "time_ns": int(time_ns),
            "ue_id": self.ue_id,
            "kind": STOP_TIMER,
            "payload": payload,
            "caused_by": str(caused_by),
        }
