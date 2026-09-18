"""Read-only structural validation of contract records used by W04.

Checks packet ID uniqueness, outcome conservation identities, bit
partition mutual-exclusion, interval non-overlap, and detection trial
shape. Never mutates inputs and never invents missing packets.
"""

from __future__ import annotations

from typing import Any, Iterable


class MetricsValidationError(ValueError):
    """Raised when run records violate conservation or uniqueness rules."""


def _fail(errors: list[dict], code: str, message: str, **extra: Any) -> None:
    errors.append({"code": code, "message": message, **extra})


def validate_packet_ids_unique(packets: Iterable[dict]) -> list[dict]:
    """Require globally unique packet_id values."""

    errors: list[dict] = []
    seen: set[str] = set()
    for index, packet in enumerate(packets):
        pid = packet.get("packet_id")
        if not isinstance(pid, str) or not pid:
            _fail(errors, "PACKET_ID_INVALID", "packet_id must be a non-empty string", index=index)
            continue
        if pid in seen:
            _fail(errors, "PACKET_ID_DUPLICATE", f"duplicate packet_id {pid!r}", packet_id=pid, index=index)
        seen.add(pid)
    return errors


def validate_outcome_conservation(outcomes: list[dict]) -> list[dict]:
    """arrived = delivered + dropped + pending; completion/drop exclusivity."""

    errors: list[dict] = []
    seen: set[str] = set()
    for index, outcome in enumerate(outcomes):
        pid = outcome.get("packet_id")
        if not isinstance(pid, str) or not pid:
            _fail(errors, "OUTCOME_ID_INVALID", "packet_id required", index=index)
            continue
        if pid in seen:
            _fail(errors, "OUTCOME_ID_DUPLICATE", f"duplicate outcome for {pid!r}", packet_id=pid)
        seen.add(pid)
        completion = outcome.get("completion_ns")
        drop = outcome.get("drop_ns")
        remaining = outcome.get("remaining_bits")
        size = outcome.get("size_bits")
        if completion is not None and drop is not None:
            _fail(errors, "OUTCOME_EXCLUSIVE", "completion_ns and drop_ns are mutually exclusive", packet_id=pid)
        if not isinstance(size, int) or size < 0:
            _fail(errors, "OUTCOME_SIZE", "size_bits must be a non-negative int", packet_id=pid)
        if not isinstance(remaining, int) or remaining < 0:
            _fail(errors, "OUTCOME_REMAINING", "remaining_bits must be a non-negative int", packet_id=pid)
        elif isinstance(size, int) and remaining > size:
            _fail(errors, "OUTCOME_REMAINING_GT_SIZE", "remaining_bits cannot exceed size_bits", packet_id=pid)
        if completion is not None and remaining != 0:
            _fail(errors, "OUTCOME_COMPLETED_REMAINING", "completed packet must have remaining_bits == 0", packet_id=pid)
        if (
            completion is None
            and drop is None
            and isinstance(remaining, int)
            and remaining == 0
            and isinstance(size, int)
            and size > 0
        ):
            _fail(
                errors,
                "OUTCOME_PENDING_ZERO_REMAINING",
                "pending outcome with remaining_bits==0 is invalid; use completion or drop",
                packet_id=pid,
            )
        if drop is not None and not outcome.get("drop_reason"):
            _fail(errors, "OUTCOME_DROP_REASON", "drop requires drop_reason", packet_id=pid)
        if drop is None and outcome.get("drop_reason") is not None:
            _fail(errors, "OUTCOME_DROP_REASON_STRAY", "drop_reason must be null without drop_ns", packet_id=pid)
    return errors


def classify_outcome(outcome: dict) -> str:
    if outcome.get("completion_ns") is not None:
        return "delivered"
    if outcome.get("drop_ns") is not None:
        return "dropped"
    return "pending"


def validate_bit_partition(outcomes: list[dict], tx_records: list[dict]) -> list[dict]:
    """Original bits = successful unique bits + pending remaining + dropped remaining.

    Successful unique bits are the union of successful TxRecord bit intervals.
    Retransmissions of the same segment do not add new successful payload.
    Airtime bits on failed attempts are not part of the conservation identity.
    """

    errors: list[dict] = []
    success_intervals: dict[str, list[tuple[int, int]]] = {}
    for index, tx in enumerate(tx_records):
        pid = tx.get("packet_id")
        if not isinstance(pid, str):
            _fail(errors, "TX_PACKET_ID", "tx.packet_id must be a string", index=index)
            continue
        offset = tx.get("offset_bits")
        payload = tx.get("payload_bits")
        if not isinstance(offset, int) or offset < 0 or not isinstance(payload, int) or payload < 0:
            _fail(
                errors,
                "TX_BITS",
                "offset_bits/payload_bits must be non-negative ints",
                index=index,
                packet_id=pid,
            )
            continue
        if not tx.get("success"):
            continue
        if payload == 0:
            continue
        success_intervals.setdefault(pid, []).append((offset, offset + payload))

    success_bits: dict[str, int] = {}
    for pid, spans in success_intervals.items():
        spans = sorted(spans)
        total = 0
        cur_s, cur_e = spans[0]
        for start, end in spans[1:]:
            if start <= cur_e:
                cur_e = max(cur_e, end)
            else:
                total += cur_e - cur_s
                cur_s, cur_e = start, end
        total += cur_e - cur_s
        success_bits[pid] = total

    for outcome in outcomes:
        pid = outcome["packet_id"]
        size = outcome.get("size_bits")
        remaining = outcome.get("remaining_bits")
        if not isinstance(size, int) or not isinstance(remaining, int):
            continue
        succ = success_bits.get(pid, 0)
        has_tx_evidence = pid in success_bits
        status = classify_outcome(outcome)
        if status == "delivered":
            if remaining != 0:
                _fail(
                    errors,
                    "BIT_DELIVERED_REMAINING",
                    f"delivered packet {pid!r} must have remaining_bits=0",
                    packet_id=pid,
                )
            # Without Tx evidence, trust the outcome completion and skip
            # bit-union equality (oracle fixtures may omit tx rows).
            if has_tx_evidence and succ != size:
                _fail(
                    errors,
                    "BIT_DELIVERED_MISMATCH",
                    f"delivered packet {pid!r} has {succ} successful unique bits but size_bits={size}",
                    packet_id=pid,
                    successful_unique_bits=succ,
                    size_bits=size,
                )
        else:
            expected_remaining = size - succ
            if expected_remaining < 0:
                _fail(
                    errors,
                    "BIT_SUCCESS_GT_SIZE",
                    f"packet {pid!r} successful unique bits exceed size_bits",
                    packet_id=pid,
                    successful_unique_bits=succ,
                    size_bits=size,
                )
            elif remaining != expected_remaining:
                _fail(
                    errors,
                    "BIT_REMAINING_MISMATCH",
                    f"packet {pid!r} remaining_bits={remaining} but size-success={expected_remaining}",
                    packet_id=pid,
                    successful_unique_bits=succ,
                    remaining_bits=remaining,
                    expected_remaining_bits=expected_remaining,
                )
            total_parts = succ + remaining
            if total_parts != size:
                _fail(
                    errors,
                    "BIT_PARTITION",
                    f"packet {pid!r} partition {succ}+{remaining} != size_bits {size}",
                    packet_id=pid,
                    successful_unique_bits=succ,
                    remaining_bits=remaining,
                    size_bits=size,
                )
    return errors


def validate_interval_nonoverlap(
    intervals: list[dict], *, by: tuple[str, str] = ("ue_id", "receiver_id")
) -> list[dict]:
    """Position-level overlap check per (ue_id, receiver_id)."""

    errors: list[dict] = []
    groups: dict[tuple[str, str], list[dict]] = {}
    for index, interval in enumerate(intervals):
        key = (interval.get(by[0]), interval.get(by[1]))
        start = interval.get("start_ns")
        end = interval.get("end_ns")
        if not isinstance(start, int) or not isinstance(end, int) or end <= start:
            _fail(
                errors,
                "INTERVAL_RANGE",
                "interval must have end_ns > start_ns",
                index=index,
                interval_id=interval.get("interval_id"),
            )
            continue
        groups.setdefault(key, []).append(interval)
    for key, values in groups.items():
        values = sorted(values, key=lambda item: (item["start_ns"], item["end_ns"], item.get("interval_id") or ""))
        for prev, curr in zip(values, values[1:]):
            if curr["start_ns"] < prev["end_ns"]:
                _fail(
                    errors,
                    "INTERVAL_OVERLAP",
                    f"overlap in {key}: {prev.get('interval_id')} and {curr.get('interval_id')}",
                    ue_id=key[0],
                    receiver_id=key[1],
                    interval_id_a=prev.get("interval_id"),
                    interval_id_b=curr.get("interval_id"),
                    overlap_start_ns=curr["start_ns"],
                    overlap_end_ns=min(prev["end_ns"], curr["end_ns"]),
                )
    return errors


def validate_detection_trials(trials: list[dict]) -> list[dict]:
    """Trial condition/outcome domain checks; SKIPPED is legal and separate."""

    errors: list[dict] = []
    seen: set[str] = set()
    for index, trial in enumerate(trials):
        tid = trial.get("trial_id")
        if not isinstance(tid, str) or not tid:
            _fail(errors, "TRIAL_ID", "trial_id required", index=index)
            continue
        if tid in seen:
            _fail(errors, "TRIAL_ID_DUPLICATE", f"duplicate trial_id {tid!r}", trial_id=tid)
        seen.add(tid)
        if trial.get("condition") not in {"H0", "H1", "H2"}:
            _fail(errors, "TRIAL_CONDITION", "condition must be H0/H1/H2", trial_id=tid)
        if trial.get("outcome") not in {"WAKE", "NO_WAKE", "SKIPPED"}:
            _fail(errors, "TRIAL_OUTCOME", "outcome must be WAKE/NO_WAKE/SKIPPED", trial_id=tid)
        start = trial.get("mo_start_ns")
        end = trial.get("mo_end_ns")
        if not isinstance(start, int) or not isinstance(end, int) or end <= start:
            _fail(errors, "TRIAL_MO_RANGE", "mo_end_ns must be > mo_start_ns", trial_id=tid)
        metadata = trial.get("metadata")
        role = metadata.get("role") if isinstance(metadata, dict) else None
        if role in {"train", "threshold_fit", "calibration"}:
            _fail(
                errors,
                "TRIAL_TRAINING_IN_EVAL",
                "training/threshold trials must not appear in evaluation denominators",
                trial_id=tid,
                role=role,
            )
    return errors


def validate_run_records(
    *,
    packets: list[dict] | None = None,
    outcomes: list[dict] | None = None,
    tx: list[dict] | None = None,
    intervals: list[dict] | None = None,
    trials: list[dict] | None = None,
) -> dict:
    """Run all read-only validators; return {valid, errors, counts}."""

    errors: list[dict] = []
    if packets is not None:
        errors.extend(validate_packet_ids_unique(packets))
    if outcomes is not None:
        errors.extend(validate_outcome_conservation(outcomes))
        if tx is not None:
            errors.extend(validate_bit_partition(outcomes, tx))
    if intervals is not None:
        errors.extend(validate_interval_nonoverlap(intervals))
    if trials is not None:
        errors.extend(validate_detection_trials(trials))
    counts = {
        "packets": len(packets or []),
        "outcomes": len(outcomes or []),
        "tx": len(tx or []),
        "intervals": len(intervals or []),
        "trials": len(trials or []),
        "errors": len(errors),
    }
    return {"valid": not errors, "errors": errors, "counts": counts}


def assert_valid_run_records(**kwargs: Any) -> dict:
    report = validate_run_records(**kwargs)
    if not report["valid"]:
        first = report["errors"][0]
        raise MetricsValidationError(f"{first['code']}: {first['message']}")
    return report
