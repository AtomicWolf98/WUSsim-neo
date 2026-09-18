"""Auditable trace output as streaming JSONL.

One JSON object per line with a ``trace_type`` discriminator:

* ``interval`` — interval_id, ue_id, receiver_id, state, start_ns, end_ns,
  power_unit, reason_event_id (interval start/end and receiver state);
* ``increment`` — increment_id, ue_id, hardware_group, policy_id, energy,
  window span or at_ns, source_event_ids (increment provenance);
* ``tx`` — tx_id, ue_id, packet_id, segment_id, offset_bits, payload_bits,
  attempt, success, resource_ids, start_ns, end_ns (packet service volume);
* ``event`` — event_id, time_ns, kind, ue_id, source_id (triggering events);
* ``decision`` — any decision object that includes ``now_ns`` (for example
  a :class:`~wus_next.core.sleep.SleepDecision` summary);
* ``window`` — opens/closes the audit window marker.

The writer streams to disk with O(1) memory, so long runs never need
per-sample logging in memory.  Readers may filter by ``trace_type`` and
summarize counts without loading everything.
"""

from __future__ import annotations

import json

from .reasons import CoreError

TRACE_TYPES = ("interval", "increment", "tx", "event", "decision", "window")


class TraceWriter:
    """Streaming JSONL trace writer with stable key ordering."""

    def __init__(self, path, append: bool = False, encoding: str = "utf-8") -> None:
        mode = "a" if append else "w"
        self._handle = open(path, mode, encoding=encoding, newline="\n")
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def _write(self, trace_type: str, fields: dict) -> dict:
        if trace_type not in TRACE_TYPES:
            raise CoreError("TRACE_TYPE_UNKNOWN", f"unknown trace_type {trace_type!r}; expected {TRACE_TYPES}")
        if trace_type == "decision" and "now_ns" not in fields:
            raise CoreError("TRACE_DECISION_NEEDS_NOW", "a decision trace record must include now_ns")
        record = dict(fields)
        record["trace_type"] = trace_type
        self._handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self._count += 1
        return record

    def interval(self, **fields) -> dict:
        return self._write("interval", fields)

    def increment(self, **fields) -> dict:
        return self._write("increment", fields)

    def tx(self, **fields) -> dict:
        return self._write("tx", fields)

    def event(self, **fields) -> dict:
        return self._write("event", fields)

    def decision(self, **fields) -> dict:
        return self._write("decision", fields)

    def window_open(self, start_ns: int, end_ns: int) -> dict:
        return self._write("window", {"phase": "open", "start_ns": start_ns, "end_ns": end_ns})

    def window_close(self, start_ns: int, end_ns: int) -> dict:
        return self._write("window", {"phase": "close", "start_ns": start_ns, "end_ns": end_ns})

    def flush(self) -> None:
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "TraceWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def read_trace(path) -> list[dict]:
    """Read a JSONL trace back into a list of dicts."""

    records = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def filter_trace(records, trace_type: str) -> list[dict]:
    if trace_type not in TRACE_TYPES:
        raise CoreError("TRACE_TYPE_UNKNOWN", f"unknown trace_type {trace_type!r}; expected {TRACE_TYPES}")
    return [record for record in records if record.get("trace_type") == trace_type]


def summarize_trace(records) -> dict:
    """Count records per trace type and report receiver/time coverage hints."""

    counts = {name: 0 for name in TRACE_TYPES}
    for record in records:
        kind = record.get("trace_type")
        if kind in counts:
            counts[kind] += 1
    counts["total"] = sum(counts[name] for name in TRACE_TYPES)
    return counts
