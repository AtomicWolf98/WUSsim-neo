"""FIFO data service over one transmission opportunity.

Contract semantics (section 4, W01 task step 6):

* one opportunity owns exactly ``capacity_bits``; partial packets continue
  across opportunities and everything is counted in integer bits;
* ``success=False`` consumes the opportunity for the failed attempt and
  never removes payload bits: retransmission airtime is not delivery;
* a completed packet leaves the queue; an empty queue never fabricates a
  successful transmission;
* whether more than one packet may be served per opportunity is declared by
  the service profile carried in the opportunity (``service_profile`` with
  ``max_packets_per_opportunity``, or top-level ``max_packets``); the legacy
  default is a single head-of-queue packet;
* the input queue is never mutated: the returned queue is a fresh list that
  shares untouched element objects and replaces only served heads with
  copies.

Complexity: O(1) validation plus O(1) service per opportunity for the
touched packets, plus O(len(queue)) to assemble the returned list.  Deep
per-call queue revalidation is deliberately avoided; end-to-end queue and
bit conservation is enforced by ``wus_next.core.ledger.build_packet_outcomes``
(duplicate packet IDs, remaining <= size, deadline order, retransmission
reuse).  Stop conditions fail with reason codes, never by clamping.
"""

from __future__ import annotations

from .reasons import (
    SERVICE_NEGATIVE_REMAINING,
    SERVICE_OPPORTUNITY_INVALID,
    SERVICE_OPPORTUNITY_UNKNOWN_FIELD,
    SERVICE_QUEUE_ELEMENT_INVALID,
    SERVICE_ZERO_REMAINING,
    CoreError,
)

OPPORTUNITY_ALLOWED_FIELDS = frozenset(
    {"ue_id", "start_ns", "end_ns", "capacity_bits", "success", "attempt", "resource_ids", "service_profile"}
)
QUEUE_PACKET_FIELDS = ("packet_id", "ue_id", "flow_id", "arrival_ns", "size_bits", "deadline_ns", "traffic_profile_id")
QUEUE_ALLOWED_FIELDS = frozenset(
    {
        "packet_id",
        "ue_id",
        "flow_id",
        "arrival_ns",
        "size_bits",
        "deadline_ns",
        "traffic_profile_id",
        "frame_id",
        "metadata",
        "remaining_bits",
    }
)


def _validate_opportunity(opportunity):
    if not isinstance(opportunity, dict):
        raise CoreError(SERVICE_OPPORTUNITY_INVALID, "opportunity must be a JSON object")
    unknown = sorted(set(opportunity) - OPPORTUNITY_ALLOWED_FIELDS)
    if unknown:
        raise CoreError(SERVICE_OPPORTUNITY_UNKNOWN_FIELD, f"opportunity unknown field(s): {', '.join(unknown)}")
    for field in ("ue_id", "start_ns", "end_ns", "capacity_bits", "success", "attempt"):
        if field not in opportunity:
            raise CoreError(SERVICE_OPPORTUNITY_INVALID, f"opportunity missing required field {field!r}")
    ue_id = opportunity["ue_id"]
    if not isinstance(ue_id, str) or not ue_id or any(ch.isspace() for ch in ue_id):
        raise CoreError(SERVICE_OPPORTUNITY_INVALID, f"opportunity.ue_id must be a valid identifier, got {ue_id!r}")
    for field in ("start_ns", "end_ns", "capacity_bits", "attempt"):
        value = opportunity[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise CoreError(SERVICE_OPPORTUNITY_INVALID, f"opportunity.{field} must be an integer, got {type(value).__name__}")
    if opportunity["end_ns"] <= opportunity["start_ns"]:
        raise CoreError(SERVICE_OPPORTUNITY_INVALID, "opportunity end_ns must be after start_ns")
    if opportunity["capacity_bits"] < 0:
        raise CoreError(
            SERVICE_OPPORTUNITY_INVALID, f"opportunity.capacity_bits must not be negative ({opportunity['capacity_bits']})"
        )
    if not isinstance(opportunity["success"], bool):
        raise CoreError(SERVICE_OPPORTUNITY_INVALID, "opportunity.success must be a boolean")
    if opportunity["attempt"] < 1:
        raise CoreError(SERVICE_OPPORTUNITY_INVALID, f"opportunity.attempt must be >= 1 ({opportunity['attempt']})")
    resource_ids = opportunity.get("resource_ids", [])
    if not isinstance(resource_ids, list) or len(resource_ids) != len(set(resource_ids)):
        raise CoreError(SERVICE_OPPORTUNITY_INVALID, "opportunity.resource_ids must be a list without duplicates")
    profile = opportunity.get("service_profile", {})
    if not isinstance(profile, dict):
        raise CoreError(SERVICE_OPPORTUNITY_INVALID, "opportunity.service_profile must be an object")
    return opportunity


def _max_packets(opportunity) -> int:
    profile = opportunity.get("service_profile") or {}
    if "max_packets_per_opportunity" in profile:
        declared = profile["max_packets_per_opportunity"]
    else:
        declared = opportunity.get("max_packets", 1)
    if isinstance(declared, bool) or not isinstance(declared, int) or declared < 1:
        raise CoreError(
            SERVICE_OPPORTUNITY_INVALID, f"max_packets_per_opportunity must be an integer >= 1, got {declared!r}"
        )
    return declared


def _validate_served_element(element, index):
    """Full strict validation for the packets this opportunity touches.

    Untouched queue entries are not re-parsed on every call; their integrity
    is enforced end-to-end by the ledger when outcomes are built.
    """

    if not isinstance(element, dict):
        raise CoreError(SERVICE_QUEUE_ELEMENT_INVALID, f"queue[{index}] must be a JSON object")
    unknown = sorted(set(element) - QUEUE_ALLOWED_FIELDS)
    if unknown:
        raise CoreError(SERVICE_QUEUE_ELEMENT_INVALID, f"queue[{index}] unknown field(s): {', '.join(unknown)}; use metadata")
    for field in QUEUE_PACKET_FIELDS:
        if field not in element:
            raise CoreError(SERVICE_QUEUE_ELEMENT_INVALID, f"queue[{index}] missing Packet field {field!r}")
    for field in ("packet_id", "ue_id", "flow_id", "traffic_profile_id"):
        value = element[field]
        if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
            raise CoreError(SERVICE_QUEUE_ELEMENT_INVALID, f"queue[{index}].{field} must be a valid identifier")
    for field in ("arrival_ns", "size_bits", "deadline_ns", "remaining_bits"):
        value = element[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise CoreError(SERVICE_QUEUE_ELEMENT_INVALID, f"queue[{index}].{field} must be an integer")
    if element["deadline_ns"] < element["arrival_ns"]:
        raise CoreError(SERVICE_QUEUE_ELEMENT_INVALID, f"queue[{index}] deadline_ns precedes arrival_ns")
    if element["remaining_bits"] < 0:
        raise CoreError(SERVICE_NEGATIVE_REMAINING, f"queue[{index}] remaining_bits is negative ({element['remaining_bits']})")
    if element["remaining_bits"] > element["size_bits"]:
        raise CoreError(SERVICE_QUEUE_ELEMENT_INVALID, f"queue[{index}] remaining_bits exceeds size_bits")
    if element["remaining_bits"] == 0:
        raise CoreError(
            SERVICE_ZERO_REMAINING,
            f"queue[{index}] packet {element['packet_id']!r} has zero remaining bits; completed packets must not sit in the queue",
        )
    return element


def serve_fifo(queue, opportunity):
    """Serve queued packet bits over one opportunity; returns (queue, tx)."""

    opportunity = _validate_opportunity(opportunity)
    if not isinstance(queue, list):
        raise CoreError(SERVICE_QUEUE_ELEMENT_INVALID, "queue must be a list")
    capacity = opportunity["capacity_bits"]
    max_packets = _max_packets(opportunity)
    emitted: list[dict] = []
    if capacity == 0 or not queue:
        return list(queue), emitted
    budget = capacity
    head_index = 0
    count = len(queue)
    modified: dict[int, dict] = {}
    served = 0
    while budget > 0 and head_index < count and served < max_packets:
        head = queue[head_index]
        _validate_served_element(head, head_index)
        remaining = head["remaining_bits"]
        take = budget if budget < remaining else remaining
        offset = head["size_bits"] - remaining
        record = {
            "tx_id": _tx_id(opportunity, head, len(emitted)),
            "ue_id": opportunity["ue_id"],
            "packet_id": head["packet_id"],
            "segment_id": _segment_id(head, offset),
            "offset_bits": offset,
            "start_ns": opportunity["start_ns"],
            "end_ns": opportunity["end_ns"],
            "attempt": opportunity["attempt"],
            "payload_bits": take,
            "success": opportunity["success"],
            "resource_ids": list(opportunity.get("resource_ids", [])),
        }
        emitted.append(record)
        served += 1
        if not opportunity["success"]:
            break
        if take == remaining:
            head_index += 1
        else:
            updated = dict(head)
            updated["remaining_bits"] = remaining - take
            modified[head_index] = updated
        budget -= take
    out = []
    for index in range(head_index, count):
        element = queue[index]
        out.append(modified[index] if index in modified else element)
    return out, emitted


def _tx_id(opportunity, head, sequence) -> str:
    return (
        f"tx:{opportunity['ue_id']}:{head['packet_id']}"
        f":a{opportunity['attempt']}:{opportunity['start_ns']}:{sequence}"
    )


def _segment_id(head, offset) -> str:
    return f"{head['packet_id']}#seg{offset}"
