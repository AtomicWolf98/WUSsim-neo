"""Small, dependency-free JSON Schema catalogue for contract-v1.

The executable semantic checks live in :mod:`validator`.  These schemas are
also exported as JSON files so other tasks can inspect the field boundary
without importing implementation code.
"""

from __future__ import annotations

from copy import deepcopy


CONTRACT_VERSION = "1.0"

_ID = {"type": "string", "minLength": 1, "pattern": r"^[^\s]+$"}
_INT = {"type": "integer"}
_NONNEG_INT = {"type": "integer", "minimum": 0}
_NUM = {"type": "number"}
_HASH = {"type": "string", "pattern": r"^[0-9a-fA-F]{64}$"}


def _object(properties: dict, required: list[str], *, additional=False) -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": additional,
        "properties": properties,
        "required": required,
    }


SCHEMAS: dict[str, dict] = {
    "Packet": _object(
        {
            "packet_id": _ID,
            "ue_id": _ID,
            "flow_id": _ID,
            "arrival_ns": _NONNEG_INT,
            "size_bits": _NONNEG_INT,
            "deadline_ns": _NONNEG_INT,
            "traffic_profile_id": _ID,
            "frame_id": {"type": ["string", "null"]},
            "metadata": {"type": "object"},
        },
        ["packet_id", "ue_id", "flow_id", "arrival_ns", "size_bits", "deadline_ns", "traffic_profile_id"],
    ),
    "Event": _object(
        {
            "event_id": _ID,
            "time_ns": _NONNEG_INT,
            "kind": _ID,
            "ue_id": _ID,
            "payload": {"type": ["object", "array", "string", "number", "boolean", "null"]},
            "source_id": _ID,
            "metadata": {"type": "object"},
        },
        ["event_id", "time_ns", "kind", "ue_id", "payload", "source_id"],
    ),
    "RadioInterval": _object(
        {
            "interval_id": _ID,
            "ue_id": _ID,
            "receiver_id": {"type": "string", "enum": ["MR", "LR"]},
            "start_ns": _NONNEG_INT,
            "end_ns": _NONNEG_INT,
            "state": _ID,
            "power_unit": _NUM,
            "reason_event_id": _ID,
            "metadata": {"type": "object"},
        },
        ["interval_id", "ue_id", "receiver_id", "start_ns", "end_ns", "state", "power_unit", "reason_event_id"],
    ),
    "EnergyIncrement": _object(
        {
            "increment_id": _ID,
            "ue_id": _ID,
            "start_ns": {"type": ["integer", "null"], "minimum": 0},
            "end_ns": {"type": ["integer", "null"], "minimum": 0},
            "at_ns": {"type": ["integer", "null"], "minimum": 0},
            "energy_unit_ms": _NUM,
            "hardware_group": _ID,
            "source_event_ids": {"type": "array", "items": _ID},
            "policy_id": {"type": "string", "enum": ["shared_max", "serial", "independent_sum"]},
            "metadata": {"type": "object"},
        },
        ["increment_id", "ue_id", "start_ns", "end_ns", "energy_unit_ms", "hardware_group", "source_event_ids", "policy_id"],
    ),
    "TxRecord": _object(
        {
            "tx_id": _ID,
            "ue_id": _ID,
            "packet_id": _ID,
            "segment_id": _ID,
            "offset_bits": _NONNEG_INT,
            "start_ns": _NONNEG_INT,
            "end_ns": _NONNEG_INT,
            "attempt": {"type": "integer", "minimum": 1},
            "payload_bits": _NONNEG_INT,
            "success": {"type": "boolean"},
            "resource_ids": {"type": "array", "items": _ID},
            "metadata": {"type": "object"},
        },
        ["tx_id", "ue_id", "packet_id", "segment_id", "offset_bits", "start_ns", "end_ns", "attempt", "payload_bits", "success", "resource_ids"],
    ),
    "PacketOutcome": _object(
        {
            "packet_id": _ID,
            "ue_id": _ID,
            "arrival_ns": _NONNEG_INT,
            "size_bits": _NONNEG_INT,
            "deadline_ns": _NONNEG_INT,
            "completion_ns": {"type": ["integer", "null"], "minimum": 0},
            "drop_ns": {"type": ["integer", "null"], "minimum": 0},
            "drop_reason": {"type": ["string", "null"]},
            "remaining_bits": _NONNEG_INT,
            "metadata": {"type": "object"},
        },
        ["packet_id", "ue_id", "arrival_ns", "size_bits", "deadline_ns", "completion_ns", "drop_ns", "drop_reason", "remaining_bits"],
    ),
    "DetectionTrial": _object(
        {
            "trial_id": _ID,
            "ue_id": _ID,
            "mo_start_ns": _NONNEG_INT,
            "mo_end_ns": _NONNEG_INT,
            "condition": {"type": "string", "enum": ["H0", "H1", "H2"]},
            "outcome": {"type": "string", "enum": ["WAKE", "NO_WAKE", "SKIPPED"]},
            "source_profile_id": _ID,
            "metadata": {"type": "object"},
        },
        ["trial_id", "ue_id", "mo_start_ns", "mo_end_ns", "condition", "outcome", "source_profile_id"],
    ),
    "Action": _object(
        {
            "action_id": _ID,
            "time_ns": _NONNEG_INT,
            "ue_id": _ID,
            "kind": {"type": "string", "enum": ["REQUEST_MONITOR", "REQUEST_SLEEP", "START_TIMER", "STOP_TIMER"]},
            "payload": {"type": ["object", "array", "string", "number", "boolean", "null"]},
            "caused_by": _ID,
            "metadata": {"type": "object"},
        },
        ["action_id", "time_ns", "ue_id", "kind", "payload", "caused_by"],
    ),
    "RunOutput": _object(
        {
            "contract_version": {"type": "string", "const": CONTRACT_VERSION},
            "run_id": _ID,
            "seed": {"type": "integer", "minimum": 0},
            "experiment_id": _ID,
            "profile_id": _ID,
            "baseline_id": _ID,
            "traffic_sha256": _HASH,
            "measurement_calendar_sha256": _HASH,
            "model_hash": _HASH,
            "evidence_status": {"type": "string"},
            "packets": {"type": "array"},
            "events": {"type": "array"},
            "intervals": {"type": "array"},
            "increments": {"type": "array"},
            "tx": {"type": "array"},
            "outcomes": {"type": "array"},
            "trials": {"type": "array"},
            "actions": {"type": "array"},
            "summary": {"type": "object"},
            "metadata": {"type": "object"},
        },
        ["contract_version", "run_id", "seed", "experiment_id", "profile_id", "baseline_id", "traffic_sha256", "measurement_calendar_sha256", "model_hash", "evidence_status", "packets", "events", "intervals", "increments", "tx", "outcomes", "trials", "actions", "summary"],
    ),
}


def get_schema(kind: str) -> dict:
    """Return a defensive copy of the public schema for *kind*."""

    try:
        return deepcopy(SCHEMAS[kind])
    except KeyError as exc:
        raise KeyError(f"unknown contract schema: {kind}") from exc

