"""Position-level radio interval audit with separate MR/LR closure.

``audit_intervals`` performs, for every ``(ue_id, receiver_id)`` group, a
sorted sweep over ``RadioInterval`` records after clipping them to the
observation window ``[start_ns, end_ns)``:

* overlapping pairs are reported with the exact overlap span and both
  interval IDs (same receiver only; different receivers of the same UE may
  run in parallel and are legal);
* coverage holes are reported with the hole span and neighbour interval IDs,
  so a run whose interval durations merely sum to the window length is still
  rejected (oracle O02: ``[0,6) + [4,8)`` over ``[0,10)`` has overlap
  ``[4,6)`` and a tail hole ``[8,10)`` although the total is 10 ms);
* each receiver closes independently: MR closure never substitutes for a
  broken LR closure.

Malformed records or an invalid window raise :class:`CoreError`; semantic
findings are returned as structured ``errors`` with reason codes.  The
module never invents intervals and never defines protocol priorities.
"""

from __future__ import annotations

from .timebase import clip_interval, require_window_ns
from .reasons import (
    COVERAGE_HOLE,
    INTERVAL_ID_DUPLICATE,
    INTERVAL_OVERLAP,
    INTERVALS_NOT_CLOSED,
    NO_INTERVALS,
    WINDOW_TAIL_HOLE,
    audit_error,
    CoreError,
    RECORD_FIELD_MISSING,
    RECORD_FIELD_TYPE,
    RECORD_ID_INVALID,
    RECORD_UNKNOWN_FIELD,
)

INTERVAL_ALLOWED_FIELDS = frozenset(
    {"interval_id", "ue_id", "receiver_id", "start_ns", "end_ns", "state", "power_unit", "reason_event_id", "metadata"}
)
RECEIVERS = ("MR", "LR")


def validate_interval_record(interval, index):
    """Structural validation shared by audit and energy integration."""

    if not isinstance(interval, dict):
        raise CoreError(RECORD_FIELD_MISSING, f"intervals[{index}] must be a JSON object")
    unknown = sorted(set(interval) - INTERVAL_ALLOWED_FIELDS)
    if unknown:
        raise CoreError(RECORD_UNKNOWN_FIELD, f"intervals[{index}] unknown field(s): {', '.join(unknown)}; use metadata")
    for field in ("interval_id", "ue_id", "receiver_id", "start_ns", "end_ns", "state", "power_unit", "reason_event_id"):
        if field not in interval:
            raise CoreError(RECORD_FIELD_MISSING, f"intervals[{index}] missing required field {field!r}")
    for field in ("interval_id", "ue_id", "receiver_id", "state", "reason_event_id"):
        value = interval[field]
        if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
            raise CoreError(
                RECORD_ID_INVALID, f"intervals[{index}].{field} must be a non-empty identifier without whitespace"
            )
    if interval["receiver_id"] not in RECEIVERS:
        raise CoreError(
            RECORD_ID_INVALID, f"intervals[{index}].receiver_id must be MR or LR, got {interval['receiver_id']!r}"
        )
    for field in ("start_ns", "end_ns"):
        value = interval[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise CoreError("TIME_NOT_INTEGER", f"intervals[{index}].{field} must be an integer ns count")
    if interval["end_ns"] <= interval["start_ns"]:
        raise CoreError("DURATION_NOT_POSITIVE", f"intervals[{index}] has end_ns <= start_ns")
    power = interval["power_unit"]
    if isinstance(power, bool) or not isinstance(power, (int, float)):
        raise CoreError("POWER_NOT_NUMERIC", f"intervals[{index}].power_unit must be numeric")
    if power < 0:
        raise CoreError("NEGATIVE_POWER", f"intervals[{index}].power_unit is negative")
    if "metadata" in interval and not isinstance(interval["metadata"], dict):
        raise CoreError(RECORD_FIELD_TYPE, f"intervals[{index}].metadata must be an object")
    return interval


def _audit_one_group(ue_id, receiver_id, ivs, ws, we):
    """Audit one (ue_id, receiver_id) group; returns (group report, findings)."""

    clipped = []
    findings: list[dict] = []
    for interval in sorted(ivs, key=lambda item: (item["start_ns"], item["end_ns"], item["interval_id"])):
        piece = clip_interval(interval["start_ns"], interval["end_ns"], ws, we)
        if piece is None:
            findings.append(
                audit_error(
                    "INTERVAL_OUT_OF_WINDOW",
                    ue_id,
                    receiver_id,
                    interval["start_ns"],
                    interval["end_ns"],
                    [interval["interval_id"]],
                    "interval does not intersect the observation window",
                )
            )
            continue
        clipped.append((piece[0], piece[1], interval["interval_id"]))
    clipped.sort(key=lambda item: (item[0], item[1], item[2]))
    prev_end = ws
    prev_id = None
    for start, end, iid in clipped:
        if start > prev_end:
            findings.append(
                audit_error(
                    COVERAGE_HOLE,
                    ue_id,
                    receiver_id,
                    prev_end,
                    start,
                    [x for x in (prev_id, iid) if x],
                    f"coverage hole between previous end {prev_end} and this start {start}",
                )
            )
        if start < prev_end:
            findings.append(
                audit_error(
                    INTERVAL_OVERLAP,
                    ue_id,
                    receiver_id,
                    start,
                    min(prev_end, end),
                    [prev_id, iid],
                    f"intervals {prev_id} and {iid} overlap on the same receiver",
                )
            )
        prev_end = max(prev_end, end)
        prev_id = iid
    if prev_end < we:
        findings.append(
            audit_error(
                WINDOW_TAIL_HOLE,
                ue_id,
                receiver_id,
                prev_end,
                we,
                [iv["interval_id"] for iv in ivs],
                f"receiver time does not close the window: tail uncovered [{prev_end}, {we})",
            )
        )
    covered = sum(end - start for start, end, _ in clipped)
    state = "EMPTY" if not clipped else ("BROKEN" if findings else "CLOSED")
    report = {
        "ue_id": ue_id,
        "receiver_id": receiver_id,
        "interval_count": len(clipped),
        "covered_ns": covered,
        "window_ns": we - ws,
        "closure": state,
        "errors": findings,
    }
    return report, findings


def audit_intervals(intervals, start_ns, end_ns):
    """Audit receiver time closure over the observation window.

    Returns a report with ``valid``, per-group closure state and structured
    ``errors`` carrying reason codes.  Overlap and hole findings are never
    collapsed into a duration sum.  Raises :class:`CoreError` only for
    malformed input (non-object records, invalid IDs, non-positive spans,
    invalid window).
    """

    ws, we = require_window_ns(start_ns, end_ns)
    if not isinstance(intervals, list):
        raise CoreError(RECORD_FIELD_MISSING, "intervals must be a list")

    ids: set[str] = set()
    groups: dict[tuple[str, str], list[dict]] = {}
    id_errors: list[dict] = []
    for order, interval in enumerate(intervals):
        validate_interval_record(interval, order)
        interval_id = interval["interval_id"]
        if interval_id in ids:
            id_errors.append(
                audit_error(
                    INTERVAL_ID_DUPLICATE,
                    interval["ue_id"],
                    interval["receiver_id"],
                    interval["start_ns"],
                    interval["end_ns"],
                    [interval_id],
                    f"interval ID {interval_id!r} appears more than once",
                )
            )
            continue
        ids.add(interval_id)
        groups.setdefault((interval["ue_id"], interval["receiver_id"]), []).append(interval)

    errors: list[dict] = list(id_errors)
    groups_report: list[dict] = []
    for (ue_id, receiver_id), group in sorted(groups.items()):
        report, findings = _audit_one_group(ue_id, receiver_id, group, ws, we)
        groups_report.append(report)
        errors.extend(findings)
    if not intervals:
        errors.append(
            audit_error(
                NO_INTERVALS, "*", "*", ws, we, [], "no intervals supplied; nothing closes the observation window"
            )
        )
    codes = sorted({err["code"] for err in errors})
    window_sum = sum(
        iv["end_ns"] - iv["start_ns"] for iv in intervals if iv["start_ns"] >= ws and iv["end_ns"] <= we
    )
    return {
        "valid": not errors,
        "window": {"start_ns": ws, "end_ns": we, "duration_ns": we - ws},
        "interval_count": len(intervals),
        "sum_duration_ns": window_sum,
        "groups": groups_report,
        "codes": codes,
        "errors": errors,
    }


def require_intervals_closed(report):
    """Raise :class:`CoreError` unless the audit report is fully valid."""

    if not report.get("valid"):
        raise CoreError(
            INTERVALS_NOT_CLOSED,
            f"{len(report.get('errors', []))} audit finding(s)"
            + (
                f"; first: {report['errors'][0]['code']} on "
                f"{report['errors'][0]['ue_id']}/{report['errors'][0]['receiver_id']}"
                if report.get("errors")
                else ""
            ),
        )
    return report
