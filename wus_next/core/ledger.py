"""Packet ledger: TxRecord/PacketOutcome mapping and conservation checks.

Contract semantics (section 4, W01 task steps 6 and 7):

* each packet owns exactly one outcome: completed (all bits delivered, one
  completion time), dropped (explicit drop decision, positive dropped
  remainder) or pending (warmup backlog or window-end backlog);
* successful transmissions of one packet form unique, mutually disjoint bit
  intervals ``[offset_bits, offset_bits + payload_bits)``; a retransmission
  reuses the same offset and is only counted once when it succeeds;
* a failed attempt records airtime but never removes payload bits and never
  completes a packet; an empty queue never fabricates a success;
* conservation per packet: ``delivered + remaining + dropped == size_bits``;
* application completion exists only after the final required bit succeeds,
  and the completion time is the end of the last successful transmission;
* services may only run when the MR receiver is ready: service time must
  fall inside one MR interval whose state is serviceable (PDCCH/PDSCH).

Stop conditions (task step 8 of W01): time flow before arrival, duplicate
delivered bits, negative/overflowing bits and forced service on a not-ready
MR fail immediately with reason codes; nothing is clamped.  KPI ratios such
as UPT or PDB satisfaction are deliberately out of scope here (W04).
"""

from __future__ import annotations

from .reasons import (
    CONSERVATION_DUPLICATE_BITS,
    CONSERVATION_OVERFLOW,
    DEGENERATE_PACKET,
    DROP_AFTER_COMPLETION,
    DROP_TIME_INVALID,
    MR_NOT_READY,
    SERVICEABLE_STATES,
    TX_BEFORE_ARRIVAL,
    TX_PACKET_UNKNOWN,
    TX_UE_MISMATCH,
    CoreError,
    RECORD_FIELD_MISSING,
    RECORD_FIELD_TYPE,
    RECORD_ID_INVALID,
    RECORD_UNKNOWN_FIELD,
)

PACKET_ALLOWED_FIELDS = frozenset(
    {"packet_id", "ue_id", "flow_id", "arrival_ns", "size_bits", "deadline_ns", "traffic_profile_id", "frame_id", "metadata"}
)
TX_ALLOWED_FIELDS = frozenset(
    {"tx_id", "ue_id", "packet_id", "segment_id", "offset_bits", "start_ns", "end_ns", "attempt", "payload_bits", "success", "resource_ids", "metadata"}
)


def _require_identifier(record, field, path):
    value = record.get(field)
    if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
        raise CoreError(RECORD_ID_INVALID, f"{path}.{field} must be a valid identifier, got {value!r}")
    return value


def _require_int(record, field, path):
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CoreError(RECORD_FIELD_TYPE, f"{path}.{field} must be an integer, got {type(value).__name__}")
    return value


def validate_packet_record(packet, index):
    if not isinstance(packet, dict):
        raise CoreError(RECORD_FIELD_MISSING, f"packets[{index}] must be a JSON object")
    unknown = sorted(set(packet) - PACKET_ALLOWED_FIELDS)
    if unknown:
        raise CoreError(RECORD_UNKNOWN_FIELD, f"packets[{index}] unknown field(s): {', '.join(unknown)}; use metadata")
    for field in ("packet_id", "ue_id", "flow_id", "arrival_ns", "size_bits", "deadline_ns", "traffic_profile_id"):
        if field not in packet:
            raise CoreError(RECORD_FIELD_MISSING, f"packets[{index}] missing required field {field!r}")
    _require_identifier(packet, "packet_id", f"packets[{index}]")
    _require_identifier(packet, "ue_id", f"packets[{index}]")
    _require_identifier(packet, "flow_id", f"packets[{index}]")
    _require_identifier(packet, "traffic_profile_id", f"packets[{index}]")
    arrival = _require_int(packet, "arrival_ns", f"packets[{index}]")
    size = _require_int(packet, "size_bits", f"packets[{index}]")
    deadline = _require_int(packet, "deadline_ns", f"packets[{index}]")
    if arrival < 0 or size < 0 or deadline < 0:
        raise CoreError("TIME_NEGATIVE", f"packets[{index}] has a negative arrival/size/deadline")
    if deadline < arrival:
        raise CoreError(RECORD_FIELD_TYPE, f"packets[{index}] deadline_ns precedes arrival_ns")
    return packet


def validate_tx_record(record, index):
    if not isinstance(record, dict):
        raise CoreError(RECORD_FIELD_MISSING, f"tx[{index}] must be a JSON object")
    unknown = sorted(set(record) - TX_ALLOWED_FIELDS)
    if unknown:
        raise CoreError(RECORD_UNKNOWN_FIELD, f"tx[{index}] unknown field(s): {', '.join(unknown)}; use metadata")
    for field in ("tx_id", "ue_id", "packet_id", "segment_id", "offset_bits", "start_ns", "end_ns", "attempt", "payload_bits", "success", "resource_ids"):
        if field not in record:
            raise CoreError(RECORD_FIELD_MISSING, f"tx[{index}] missing required field {field!r}")
    for field in ("tx_id", "ue_id", "packet_id", "segment_id"):
        _require_identifier(record, field, f"tx[{index}]")
    for field in ("offset_bits", "start_ns", "end_ns", "attempt", "payload_bits"):
        _require_int(record, field, f"tx[{index}]")
    if record["end_ns"] <= record["start_ns"]:
        raise CoreError("DURATION_NOT_POSITIVE", f"tx[{index}] has end_ns <= start_ns")
    if record["attempt"] < 1:
        raise CoreError(RECORD_FIELD_TYPE, f"tx[{index}] attempt must be >= 1")
    if record["payload_bits"] < 0:
        raise CoreError("SERVICE_NEGATIVE_REMAINING", f"tx[{index}] payload_bits is negative")
    if not isinstance(record["success"], bool):
        raise CoreError(RECORD_FIELD_TYPE, f"tx[{index}] success must be a boolean")
    if not isinstance(record["resource_ids"], list):
        raise CoreError(RECORD_FIELD_TYPE, f"tx[{index}] resource_ids must be a list")
    return record


def build_packet_outcomes(packets, tx_records, drops=None):
    """Map packets, transmissions and explicit drops onto outcomes.

    Returns ``{"outcomes": [...], "report": {...}}``.  Causality and
    conservation violations raise :class:`CoreError` (stop conditions), they
    are never silently repaired.
    """

    if drops is None:
        drops = []
    if not isinstance(packets, list) or not isinstance(tx_records, list) or not isinstance(drops, list):
        raise CoreError(RECORD_FIELD_TYPE, "packets/tx/drops must be lists")
    by_packet: dict[str, dict] = {}
    for order, packet in enumerate(packets):
        validate_packet_record(packet, order)
        packet_id = packet["packet_id"]
        if packet_id in by_packet:
            raise CoreError(RECORD_ID_INVALID, f"packet ID {packet_id!r} appears more than once")
        if packet["size_bits"] == 0:
            raise CoreError(
                DEGENERATE_PACKET,
                f"packet {packet_id!r} has size_bits 0; zero-size packets have no deliverable bits",
            )
        by_packet[packet_id] = packet
    for order, record in enumerate(tx_records):
        validate_tx_record(record, order)
        packet_id = record["packet_id"]
        if packet_id not in by_packet:
            raise CoreError(TX_PACKET_UNKNOWN, f"tx {record['tx_id']!r} references unknown packet {packet_id!r}")
        packet = by_packet[packet_id]
        if record["ue_id"] != packet["ue_id"]:
            raise CoreError(
                TX_UE_MISMATCH,
                f"tx {record['tx_id']!r} has ue {record['ue_id']!r} but packet {packet_id!r} belongs to {packet['ue_id']!r}",
            )
        if record["start_ns"] < packet["arrival_ns"]:
            raise CoreError(
                TX_BEFORE_ARRIVAL,
                f"tx {record['tx_id']!r} starts at {record['start_ns']} before packet {packet_id!r} arrival {packet['arrival_ns']}",
            )

    delivered: dict[str, int] = {}
    success_times: dict[str, list] = {}
    failed_checks: dict[str, list] = {}
    for record in tx_records:
        packet_id = record["packet_id"]
        size = by_packet[packet_id]["size_bits"]
        if record["success"]:
            success_times.setdefault(packet_id, []).append(record)
        else:
            failed_checks.setdefault(packet_id, []).append(record)

    for packet_id, records in success_times.items():
        size = by_packet[packet_id]["size_bits"]
        ordered = sorted(records, key=lambda item: (item["offset_bits"], item["end_ns"], item["tx_id"]))
        previous_end = 0
        total = 0
        for record in ordered:
            if record["offset_bits"] < previous_end:
                raise CoreError(
                    CONSERVATION_DUPLICATE_BITS,
                    f"packet {packet_id!r}: successful segment {record['segment_id']!r} "
                    f"[{record['offset_bits']},{record['offset_bits'] + record['payload_bits']}) "
                    f"re-delivers bits already covered up to {previous_end}",
                )
            previous_end = record["offset_bits"] + record["payload_bits"]
            total += record["payload_bits"]
        if total > size:
            raise CoreError(
                CONSERVATION_OVERFLOW,
                f"packet {packet_id!r} delivered {total} bits beyond size_bits {size}",
            )
        delivered[packet_id] = total
    for packet_id, records in failed_checks.items():
        size = by_packet[packet_id]["size_bits"]
        for record in records:
            if record["offset_bits"] + record["payload_bits"] > size:
                raise CoreError(
                    CONSERVATION_OVERFLOW,
                    f"packet {packet_id!r}: failed attempt {record['tx_id']!r} reserves airtime beyond size_bits {size}",
                )

    drop_map: dict[str, dict] = {}
    for order, drop in enumerate(drops):
        if not isinstance(drop, dict) or not {"packet_id", "drop_ns", "drop_reason"} <= set(drop):
            raise CoreError(RECORD_FIELD_MISSING, f"drops[{order}] must carry packet_id, drop_ns and drop_reason")
        packet_id = drop["packet_id"]
        if packet_id not in by_packet:
            raise CoreError(TX_PACKET_UNKNOWN, f"drop {order} references unknown packet {packet_id!r}")
        drop_ns = _require_int(drop, "drop_ns", f"drops[{order}]")
        if not isinstance(drop["drop_reason"], str) or not drop["drop_reason"]:
            raise CoreError(RECORD_FIELD_TYPE, f"drops[{order}].drop_reason must be a non-empty string")
        packet = by_packet[packet_id]
        if drop_ns < packet["arrival_ns"]:
            raise CoreError(DROP_TIME_INVALID, f"drop of {packet_id!r} at {drop_ns} precedes arrival {packet['arrival_ns']}")
        successful = success_times.get(packet_id, [])
        if delivered.get(packet_id, 0) == packet["size_bits"]:
            raise CoreError(DROP_AFTER_COMPLETION, f"packet {packet_id!r} is already delivered; a drop contradicts completion")
        if successful:
            last_end = max(record["end_ns"] for record in successful)
            if drop_ns < last_end:
                raise CoreError(
                    DROP_TIME_INVALID,
                    f"drop of {packet_id!r} at {drop_ns} precedes the last successful transmission end {last_end}",
                )
        if packet_id in drop_map:
            raise CoreError(RECORD_ID_INVALID, f"packet {packet_id!r} is dropped more than once")
        drop_map[packet_id] = drop

    outcomes: list[dict] = []
    per_packet: dict[str, dict] = {}
    for packet in packets:
        packet_id = packet["packet_id"]
        size = packet["size_bits"]
        bits = delivered.get(packet_id, 0)
        if packet_id in drop_map:
            drop = drop_map[packet_id]
            outcome = {
                "packet_id": packet_id,
                "ue_id": packet["ue_id"],
                "arrival_ns": packet["arrival_ns"],
                "size_bits": size,
                "deadline_ns": packet["deadline_ns"],
                "completion_ns": None,
                "drop_ns": drop["drop_ns"],
                "drop_reason": drop["drop_reason"],
                "remaining_bits": size - bits,
            }
            if outcome["remaining_bits"] <= 0:
                raise CoreError(
                    CONSERVATION_OVERFLOW,
                    f"drop of {packet_id!r} leaves no remaining bits ({outcome['remaining_bits']})",
                )
            status = "dropped"
        elif bits == size:
            completion = max(record["end_ns"] for record in success_times[packet_id])
            outcome = {
                "packet_id": packet_id,
                "ue_id": packet["ue_id"],
                "arrival_ns": packet["arrival_ns"],
                "size_bits": size,
                "deadline_ns": packet["deadline_ns"],
                "completion_ns": completion,
                "drop_ns": None,
                "drop_reason": None,
                "remaining_bits": 0,
            }
            status = "completed"
        else:
            outcome = {
                "packet_id": packet_id,
                "ue_id": packet["ue_id"],
                "arrival_ns": packet["arrival_ns"],
                "size_bits": size,
                "deadline_ns": packet["deadline_ns"],
                "completion_ns": None,
                "drop_ns": None,
                "drop_reason": None,
                "remaining_bits": size - bits,
            }
            status = "pending"
        outcomes.append(outcome)
        per_packet[packet_id] = {
            "ue_id": packet["ue_id"],
            "size_bits": size,
            "delivered_bits": bits,
            "remaining_bits": outcome["remaining_bits"],
            "dropped_bits": size - bits if packet_id in drop_map else 0,
            "status": status,
            "conservation_ok": bits + outcome["remaining_bits"] <= size and (status != "completed" or bits == size),
        }
    report = {
        "valid": True,
        "packet_count": len(packets),
        "tx_count": len(tx_records),
        "completed": sum(1 for outcome in outcomes if outcome["completion_ns"] is not None),
        "dropped": sum(1 for outcome in outcomes if outcome["drop_ns"] is not None),
        "pending": sum(1 for outcome in outcomes if outcome["completion_ns"] is None and outcome["drop_ns"] is None),
        "per_packet": per_packet,
        "delivered_bits_total": sum(entry["delivered_bits"] for entry in per_packet.values()),
    }
    return {"outcomes": outcomes, "report": report}


def check_resource_exclusivity(tx_records):
    """Successful transmissions of one UE must not share resources in time."""

    successful = [record for record in tx_records if record["success"]]
    by_ue: dict[str, list] = {}
    for record in successful:
        by_ue.setdefault(record["ue_id"], []).append(record)
    errors = []
    for ue_id, records in sorted(by_ue.items()):
        ordered = sorted(records, key=lambda item: (item["start_ns"], item["end_ns"], item["tx_id"]))
        for previous, current in zip(ordered, ordered[1:]):
            if current["start_ns"] < previous["end_ns"]:
                shared = set(previous["resource_ids"]) & set(current["resource_ids"])
                if shared:
                    errors.append(
                        {
                            "code": "RESOURCE_DOUBLE_BOOKED",
                            "ue_id": ue_id,
                            "resource_ids": sorted(shared),
                            "tx_ids": [previous["tx_id"], current["tx_id"]],
                            "start_ns": current["start_ns"],
                            "end_ns": min(previous["end_ns"], current["end_ns"]),
                            "detail": "successful transmissions of one UE reuse the same resource while overlapping",
                        }
                    )
    return {"valid": not errors, "errors": errors}


def require_resources_exclusive(report):
    if not report.get("valid"):
        raise CoreError(
            "RESOURCE_DOUBLE_BOOKED",
            f"{len(report['errors'])} resource double-booking finding(s); first: {report['errors'][0]['detail']}",
        )
    return report


def assert_mr_ready(intervals, ue_id, start_ns, end_ns):
    """Raise MR_NOT_READY unless MR covers [start,end) with a serviceable state.

    After a closed audit the covering interval must be unique; overlapping
    coverage is reported as an overlap modeling error, not silently used.
    """

    if end_ns <= start_ns:
        raise CoreError("WINDOW_NOT_POSITIVE", "service window must be positive")
    covering = [
        interval
        for interval in intervals
        if interval["ue_id"] == ue_id
        and interval["receiver_id"] == "MR"
        and interval["start_ns"] <= start_ns
        and interval["end_ns"] >= end_ns
    ]
    if len(covering) > 1:
        raise CoreError(
            "INTERVAL_OVERLAP",
            f"{len(covering)} MR intervals cover the service window: "
            + ", ".join(sorted(interval["interval_id"] for interval in covering)),
        )
    if not covering:
        raise CoreError(
            MR_NOT_READY,
            f"UE {ue_id!r} has no MR interval covering [{start_ns},{end_ns}); the MR is not ready to serve",
        )
    interval = covering[0]
    if interval["state"] not in SERVICEABLE_STATES:
        raise CoreError(
            MR_NOT_READY,
            f"MR interval {interval['interval_id']!r} has state {interval['state']!r}, "
            f"not serviceable ({sorted(SERVICEABLE_STATES)})",
        )
    return interval
