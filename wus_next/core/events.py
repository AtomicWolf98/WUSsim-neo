"""Stable, deterministic event heap for causal simulation.

Default processing order (contract section 5, abstract profile):

1. completion of the previous service interval (SERVICE_COMPLETE)
2. expiring timers (TIMER_EXPIRY)
3. packet arrivals (PACKET_ARRIVAL)
4. completed detection indications (DETECTION_INDICATION)
5. measurement / monitoring requests (MEASUREMENT_REQUEST, MONITOR_REQUEST)
6. scheduling decisions (SCHEDULING)
7. recording of the next open interval (INTERVAL_RECORD)

Remaining ties break on the stable ``event_id`` string.  A profile may pass
its own ``order`` mapping at construction.  The heap rejects events with
non-integer times, negative times, times before ``now_ns``, duplicate IDs,
unknown kinds and non-positive ``payload.duration_ns``.  Nothing is clamped;
failures carry reason codes.
"""

from __future__ import annotations

import heapq

from .reasons import (
    CoreError,
    EVENT_DUPLICATE_ID,
    EVENT_DURATION_INVALID,
    EVENT_IN_PAST,
    EVENT_KIND_UNORDERED,
    RECORD_FIELD_MISSING,
    RECORD_ID_INVALID,
    TIME_NOT_INTEGER,
    TIME_REWIND,
)

DEFAULT_EVENT_ORDER = {
    "SERVICE_COMPLETE": 0,
    "TIMER_EXPIRY": 1,
    "PACKET_ARRIVAL": 2,
    "DETECTION_INDICATION": 3,
    "MEASUREMENT_REQUEST": 4,
    "MONITOR_REQUEST": 4,
    "SCHEDULING": 5,
    "INTERVAL_RECORD": 6,
}

EVENT_REQUIRED_FIELDS = ("event_id", "time_ns", "kind", "ue_id", "payload", "source_id")


class EventHeap:
    """Deterministic priority queue over contract ``Event`` records."""

    def __init__(self, order: dict[str, int] | None = None, now_ns: int = 0) -> None:
        self._order = dict(order) if order is not None else dict(DEFAULT_EVENT_ORDER)
        self._now = self._clean_now(now_ns)
        self._heap: list[tuple[int, int, str, dict]] = []
        self._ids: set[str] = set()

    @staticmethod
    def _clean_now(now_ns) -> int:
        if isinstance(now_ns, bool) or not isinstance(now_ns, int):
            raise CoreError(TIME_NOT_INTEGER, f"initial now_ns must be an integer, got {type(now_ns).__name__}")
        if now_ns < 0:
            from .reasons import TIME_NEGATIVE

            raise CoreError(TIME_NEGATIVE, f"initial now_ns is negative ({now_ns})")
        return now_ns

    @property
    def now_ns(self) -> int:
        return self._now

    def advance(self, now_ns) -> None:
        """Move the causal horizon forward; rewinding fails immediately."""

        candidate = self._require_int(now_ns, "advance now_ns")
        if candidate < self._now:
            raise CoreError(TIME_REWIND, f"cannot move now_ns from {self._now} back to {candidate}")
        self._now = candidate

    @staticmethod
    def _require_int(value, what: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise CoreError(TIME_NOT_INTEGER, f"{what} must be an integer nanosecond count, got {type(value).__name__} {value!r}")
        return value

    def push(self, event: dict) -> None:
        """Validate and insert one event record; the heap never mutates on failure."""

        if not isinstance(event, dict):
            raise CoreError(RECORD_FIELD_TYPE, "event must be a JSON object")
        for field in EVENT_REQUIRED_FIELDS:
            if field not in event:
                raise CoreError(RECORD_FIELD_MISSING, f"event is missing required field {field!r}")
        event_id = event["event_id"]
        if not isinstance(event_id, str) or not event_id or any(ch.isspace() for ch in event_id):
            raise CoreError(RECORD_ID_INVALID, f"event_id must be a non-empty identifier without whitespace, got {event_id!r}")
        if event_id in self._ids:
            raise CoreError(EVENT_DUPLICATE_ID, f"event_id {event_id!r} is already queued")
        kind = event["kind"]
        if not isinstance(kind, str) or not kind or any(ch.isspace() for ch in kind):
            raise CoreError(RECORD_ID_INVALID, f"event kind must be a non-empty identifier, got {kind!r}")
        if kind not in self._order:
            raise CoreError(
                EVENT_KIND_UNORDERED,
                f"event kind {kind!r} has no order rank; register it via the heap order mapping (profile order or contract default)",
            )
        time_ns = self._require_int(event["time_ns"], f"event {event_id} time_ns")
        if time_ns < 0:
            from .reasons import TIME_NEGATIVE

            raise CoreError(TIME_NEGATIVE, f"event {event_id} time_ns is negative ({time_ns})")
        if time_ns < self._now:
            raise CoreError(EVENT_IN_PAST, f"event {event_id} at {time_ns} ns precedes now_ns {self._now}")
        payload = event["payload"]
        if isinstance(payload, dict) and "duration_ns" in payload:
            duration = self._require_int(payload["duration_ns"], f"event {event_id} payload.duration_ns")
            if duration <= 0:
                raise CoreError(EVENT_DURATION_INVALID, f"event {event_id} payload.duration_ns must be positive, got {duration}")
        heapq.heappush(self._heap, (time_ns, self._order[kind], event_id, dict(event)))
        self._ids.add(event_id)

    def peek(self) -> dict | None:
        """Return the next due event without popping it."""

        return dict(self._heap[0][3]) if self._heap else None

    def pop(self) -> dict:
        """Pop the smallest due event; raises when the heap is empty."""

        if not self._heap:
            raise CoreError("EVENT_HEAP_EMPTY", "no events left to pop")
        _, _, _, event = heapq.heappop(self._heap)
        self._ids.discard(event["event_id"])
        return event

    def pop_due(self, upto_ns) -> list[dict]:
        """Pop every event with ``time_ns`` in ``[now, upto_ns]`` in stable order."""

        limit = self._require_int(upto_ns, "pop_due upto_ns")
        due: list[dict] = []
        while self._heap and self._heap[0][0] <= limit:
            due.append(self.pop())
        return due

    def drain(self) -> list[dict]:
        """Pop all remaining events in stable order (audits/tests only)."""

        out: list[dict] = []
        while self._heap:
            out.append(self.pop())
        return out

    def __len__(self) -> int:
        return len(self._heap)
