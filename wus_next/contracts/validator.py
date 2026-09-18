"""Strict, dependency-free validator for contract-v1 records and profiles."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

from .schema import CONTRACT_VERSION, SCHEMAS
from .units import energy_unit_ms


EVIDENCE_STATUSES = frozenset(
    {
        "NORMATIVE",
        "MEETING_AGREEMENT",
        "COMPANY_PROPOSAL",
        "RESEARCH_REFERENCE",
        "ASSUMPTION",
        "PROXY",
        "UNRESOLVED",
        "TEST_ONLY",
    }
)
PROFILE_STATUSES = frozenset({"ENABLED", "EXPLORATORY", "BLOCKED_EVIDENCE", "LEGACY_REPLAY_ONLY"})
_ID_RE = re.compile(r"^[^\s]+$")
_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_JSON_SCALARS = (str, int, float, bool)


class ValidationError(ValueError):
    """Raised when a contract record or evidence-aware profile is invalid."""


def _fail(path: str, message: str) -> None:
    raise ValidationError(f"{path}: {message}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)))


def _require_mapping(record: Any, path: str) -> dict:
    if not isinstance(record, dict):
        _fail(path, "must be a JSON object")
    return record


def _check_unknown(record: dict, allowed: set[str], path: str) -> None:
    unknown = sorted(set(record) - allowed)
    if unknown:
        _fail(path, f"unknown field(s): {', '.join(unknown)}; put extensions under metadata")


def _require(record: dict, fields: Iterable[str], path: str) -> None:
    for field in fields:
        if field not in record:
            _fail(path, f"missing required field {field!r}")


def _id(value: Any, path: str) -> None:
    if not isinstance(value, str) or not value or not _ID_RE.fullmatch(value):
        _fail(path, "must be a non-empty identifier without whitespace")


def _nonnegative_int(value: Any, path: str) -> None:
    if not _is_int(value) or value < 0:
        _fail(path, "must be a non-negative integer")


def _positive_duration(start: Any, end: Any, path: str) -> None:
    _nonnegative_int(start, f"{path}.start_ns")
    _nonnegative_int(end, f"{path}.end_ns")
    if end <= start:
        _fail(path, "end_ns must be greater than start_ns for a positive interval")


def _unique_ids(values: Any, path: str) -> None:
    if not isinstance(values, list):
        _fail(path, "must be a list")
    for index, value in enumerate(values):
        _id(value, f"{path}[{index}]")
    if len(values) != len(set(values)):
        _fail(path, "must not contain duplicate IDs")


def _validate_packet(record: dict) -> None:
    allowed = set(SCHEMAS["Packet"]["properties"])
    _check_unknown(record, allowed, "Packet")
    _require(record, SCHEMAS["Packet"]["required"], "Packet")
    for field in ("packet_id", "ue_id", "flow_id", "traffic_profile_id"):
        _id(record[field], f"Packet.{field}")
    _nonnegative_int(record["arrival_ns"], "Packet.arrival_ns")
    _nonnegative_int(record["size_bits"], "Packet.size_bits")
    _nonnegative_int(record["deadline_ns"], "Packet.deadline_ns")
    if record["deadline_ns"] < record["arrival_ns"]:
        _fail("Packet.deadline_ns", "must be >= arrival_ns")
    if "frame_id" in record and record["frame_id"] is not None:
        _id(record["frame_id"], "Packet.frame_id")
    if "metadata" in record and not isinstance(record["metadata"], dict):
        _fail("Packet.metadata", "must be an object")


def _validate_event(record: dict) -> None:
    allowed = set(SCHEMAS["Event"]["properties"])
    _check_unknown(record, allowed, "Event")
    _require(record, SCHEMAS["Event"]["required"], "Event")
    _id(record["event_id"], "Event.event_id")
    _nonnegative_int(record["time_ns"], "Event.time_ns")
    _id(record["kind"], "Event.kind")
    _id(record["ue_id"], "Event.ue_id")
    _id(record["source_id"], "Event.source_id")
    if "metadata" in record and not isinstance(record["metadata"], dict):
        _fail("Event.metadata", "must be an object")


def _validate_radio_interval(record: dict) -> None:
    allowed = set(SCHEMAS["RadioInterval"]["properties"])
    _check_unknown(record, allowed, "RadioInterval")
    _require(record, SCHEMAS["RadioInterval"]["required"], "RadioInterval")
    for field in ("interval_id", "ue_id", "reason_event_id"):
        _id(record[field], f"RadioInterval.{field}")
    if record["receiver_id"] not in {"MR", "LR"}:
        _fail("RadioInterval.receiver_id", "must be MR or LR")
    _positive_duration(record["start_ns"], record["end_ns"], "RadioInterval")
    _id(record["state"], "RadioInterval.state")
    allowed_states = {"PDCCH", "PDSCH", "MEASUREMENT", "RESYNC", "RAMP", "micro", "light", "deep", "ultra_deep"}
    if record["state"] not in allowed_states:
        _fail("RadioInterval.state", f"unknown state {record['state']!r}")
    if not _is_number(record["power_unit"]) or record["power_unit"] < 0:
        _fail("RadioInterval.power_unit", "must be a finite non-negative relative power")
    if "metadata" in record and not isinstance(record["metadata"], dict):
        _fail("RadioInterval.metadata", "must be an object")


def _validate_energy_increment(record: dict) -> None:
    allowed = set(SCHEMAS["EnergyIncrement"]["properties"])
    _check_unknown(record, allowed, "EnergyIncrement")
    _require(record, SCHEMAS["EnergyIncrement"]["required"], "EnergyIncrement")
    _id(record["increment_id"], "EnergyIncrement.increment_id")
    _id(record["ue_id"], "EnergyIncrement.ue_id")
    start, end = record["start_ns"], record["end_ns"]
    if start is None or end is None:
        if record.get("at_ns") is None:
            _fail("EnergyIncrement", "interval or at_ns is required; instantaneous edges need at_ns")
        _nonnegative_int(record["at_ns"], "EnergyIncrement.at_ns")
        if start is not None or end is not None:
            _fail("EnergyIncrement", "start_ns and end_ns must both be null for an at_ns edge")
    else:
        _positive_duration(start, end, "EnergyIncrement")
        if record.get("at_ns") is not None:
            _fail("EnergyIncrement.at_ns", "must be null for an interval increment")
    if not _is_number(record["energy_unit_ms"]) or record["energy_unit_ms"] < 0:
        _fail("EnergyIncrement.energy_unit_ms", "must be a finite non-negative value")
    _id(record["hardware_group"], "EnergyIncrement.hardware_group")
    _unique_ids(record["source_event_ids"], "EnergyIncrement.source_event_ids")
    if record["policy_id"] not in {"shared_max", "serial", "independent_sum"}:
        _fail("EnergyIncrement.policy_id", "unknown EE sharing policy")
    if "metadata" in record and not isinstance(record["metadata"], dict):
        _fail("EnergyIncrement.metadata", "must be an object")


def _validate_tx(record: dict) -> None:
    allowed = set(SCHEMAS["TxRecord"]["properties"])
    _check_unknown(record, allowed, "TxRecord")
    _require(record, SCHEMAS["TxRecord"]["required"], "TxRecord")
    for field in ("tx_id", "ue_id", "packet_id", "segment_id"):
        _id(record[field], f"TxRecord.{field}")
    _nonnegative_int(record["offset_bits"], "TxRecord.offset_bits")
    _positive_duration(record["start_ns"], record["end_ns"], "TxRecord")
    if not _is_int(record["attempt"]) or record["attempt"] < 1:
        _fail("TxRecord.attempt", "must be a positive integer")
    _nonnegative_int(record["payload_bits"], "TxRecord.payload_bits")
    _unique_ids(record["resource_ids"], "TxRecord.resource_ids")
    if "metadata" in record and not isinstance(record["metadata"], dict):
        _fail("TxRecord.metadata", "must be an object")


def _validate_outcome(record: dict) -> None:
    allowed = set(SCHEMAS["PacketOutcome"]["properties"])
    _check_unknown(record, allowed, "PacketOutcome")
    _require(record, SCHEMAS["PacketOutcome"]["required"], "PacketOutcome")
    for field in ("packet_id", "ue_id"):
        _id(record[field], f"PacketOutcome.{field}")
    for field in ("arrival_ns", "size_bits", "deadline_ns", "remaining_bits"):
        _nonnegative_int(record[field], f"PacketOutcome.{field}")
    if record["deadline_ns"] < record["arrival_ns"]:
        _fail("PacketOutcome.deadline_ns", "must be >= arrival_ns")
    completion, drop = record["completion_ns"], record["drop_ns"]
    if completion is not None:
        _nonnegative_int(completion, "PacketOutcome.completion_ns")
    if drop is not None:
        _nonnegative_int(drop, "PacketOutcome.drop_ns")
    if completion is not None and drop is not None:
        _fail("PacketOutcome", "completion_ns and drop_ns are mutually exclusive")
    if completion is not None and completion < record["arrival_ns"]:
        _fail("PacketOutcome.completion_ns", "must be >= arrival_ns")
    if drop is not None and drop < record["arrival_ns"]:
        _fail("PacketOutcome.drop_ns", "must be >= arrival_ns")
    if completion is not None and record["remaining_bits"] != 0:
        _fail("PacketOutcome.remaining_bits", "must be 0 for a completed packet")
    if completion is None and record["remaining_bits"] == 0:
        _fail("PacketOutcome.remaining_bits", "pending/drop outcome must retain positive remaining bits")
    if drop is None and record["drop_reason"] is not None:
        _fail("PacketOutcome.drop_reason", "must be null unless drop_ns is set")
    if drop is not None and (not isinstance(record["drop_reason"], str) or not record["drop_reason"]):
        _fail("PacketOutcome.drop_reason", "must explain a drop")
    if "metadata" in record and not isinstance(record["metadata"], dict):
        _fail("PacketOutcome.metadata", "must be an object")


def _validate_trial(record: dict) -> None:
    allowed = set(SCHEMAS["DetectionTrial"]["properties"])
    _check_unknown(record, allowed, "DetectionTrial")
    _require(record, SCHEMAS["DetectionTrial"]["required"], "DetectionTrial")
    for field in ("trial_id", "ue_id", "source_profile_id"):
        _id(record[field], f"DetectionTrial.{field}")
    _positive_duration(record["mo_start_ns"], record["mo_end_ns"], "DetectionTrial")
    if record["condition"] not in {"H0", "H1", "H2"}:
        _fail("DetectionTrial.condition", "must be H0, H1 or H2")
    if record["outcome"] not in {"WAKE", "NO_WAKE", "SKIPPED"}:
        _fail("DetectionTrial.outcome", "must be WAKE, NO_WAKE or SKIPPED")
    if "metadata" in record and not isinstance(record["metadata"], dict):
        _fail("DetectionTrial.metadata", "must be an object")


def _validate_action(record: dict) -> None:
    allowed = set(SCHEMAS["Action"]["properties"])
    _check_unknown(record, allowed, "Action")
    _require(record, SCHEMAS["Action"]["required"], "Action")
    for field in ("action_id", "ue_id", "caused_by"):
        _id(record[field], f"Action.{field}")
    _nonnegative_int(record["time_ns"], "Action.time_ns")
    if record["kind"] not in {"REQUEST_MONITOR", "REQUEST_SLEEP", "START_TIMER", "STOP_TIMER"}:
        _fail("Action.kind", "unknown action kind")
    if "metadata" in record and not isinstance(record["metadata"], dict):
        _fail("Action.metadata", "must be an object")


_RECORD_VALIDATORS = {
    "Packet": _validate_packet,
    "Event": _validate_event,
    "RadioInterval": _validate_radio_interval,
    "EnergyIncrement": _validate_energy_increment,
    "TxRecord": _validate_tx,
    "PacketOutcome": _validate_outcome,
    "DetectionTrial": _validate_trial,
    "Action": _validate_action,
}


def validate_record(kind: str, record: dict) -> dict:
    """Validate and return a JSON-compatible record without mutating it."""

    if kind not in _RECORD_VALIDATORS:
        _fail("kind", f"unknown record kind {kind!r}")
    record = _require_mapping(record, kind)
    _RECORD_VALIDATORS[kind](record)
    return record


def validate_interval_set(intervals: list[dict], start_ns: int | None = None, end_ns: int | None = None) -> dict:
    """Check MR/LR interval non-overlap and optional exact window coverage.

    This is a foundational negative check for W01.  It does not select a
    protocol priority or invent missing intervals.
    """

    if not isinstance(intervals, list):
        _fail("intervals", "must be a list")
    grouped: dict[tuple[str, str], list[dict]] = {}
    ids: set[str] = set()
    for index, interval in enumerate(intervals):
        validate_record("RadioInterval", interval)
        if interval["interval_id"] in ids:
            _fail(f"intervals[{index}].interval_id", "duplicate interval ID")
        ids.add(interval["interval_id"])
        grouped.setdefault((interval["ue_id"], interval["receiver_id"]), []).append(interval)
    for key, values in grouped.items():
        values.sort(key=lambda item: (item["start_ns"], item["end_ns"], item["interval_id"]))
        for previous, current in zip(values, values[1:]):
            if current["start_ns"] < previous["end_ns"]:
                _fail("intervals", f"overlap for {key}: {previous['interval_id']} and {current['interval_id']}")
        if start_ns is not None or end_ns is not None:
            if start_ns is None or end_ns is None or not _is_int(start_ns) or not _is_int(end_ns) or end_ns <= start_ns:
                _fail("window", "start_ns/end_ns must form a positive integer interval")
            cursor = start_ns
            for item in values:
                if item["start_ns"] != cursor:
                    _fail("intervals", f"coverage hole or out-of-window interval for {key} at {cursor}")
                cursor = item["end_ns"]
            if cursor != end_ns:
                _fail("intervals", f"coverage hole or tail for {key}: ended at {cursor}, expected {end_ns}")
    return {"valid": True, "groups": len(grouped), "interval_count": len(intervals)}


def _validate_parameter_leaf(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        _fail(path, "parameter leaf must be an object with source metadata")
    required = {"value", "unit", "source_id", "locator", "evidence_status", "interpretation", "valid_domain"}
    _check_unknown(value, required, path)
    _require(value, required, path)
    _id(value["source_id"], f"{path}.source_id")
    if not isinstance(value["locator"], str) or not value["locator"]:
        _fail(f"{path}.locator", "must be a non-empty source locator")
    if value["evidence_status"] not in EVIDENCE_STATUSES:
        _fail(f"{path}.evidence_status", "unknown evidence status")
    if value["unit"] is not None and (not isinstance(value["unit"], str) or not value["unit"]):
        _fail(f"{path}.unit", "must be a non-empty unit string or null")
    if not isinstance(value["interpretation"], str) or not value["interpretation"]:
        _fail(f"{path}.interpretation", "must explain the parameter meaning")
    if not isinstance(value["valid_domain"], (dict, list, str)):
        _fail(f"{path}.valid_domain", "must be JSON-compatible domain metadata")


def _walk_parameter_tree(value: Any, path: str) -> None:
    if isinstance(value, dict) and set(value) >= {"value", "unit", "source_id", "locator", "evidence_status", "interpretation", "valid_domain"}:
        _validate_parameter_leaf(value, path)
        return
    if not isinstance(value, dict):
        _fail(path, "parameter groups must be objects")
    for key, child in value.items():
        _id(key, f"{path}.{key}")
        _walk_parameter_tree(child, f"{path}.{key}")


def validate_profile(profile: dict, requested_mode: str | None = None) -> dict:
    """Validate a profile and reject unsupported formal/publication modes."""

    profile = _require_mapping(profile, "profile")
    required = {"contract_version", "profile_id", "profile_version", "profile_status", "purpose", "parameters", "sources", "capabilities"}
    allowed = required | {"case_ids", "traffic_profiles", "blocked_reasons", "publication", "metadata"}
    _check_unknown(profile, allowed, "profile")
    _require(profile, required, "profile")
    if profile["contract_version"] != CONTRACT_VERSION:
        _fail("profile.contract_version", f"expected {CONTRACT_VERSION}")
    _id(profile["profile_id"], "profile.profile_id")
    _id(profile["profile_version"], "profile.profile_version")
    if profile["profile_status"] not in PROFILE_STATUSES:
        _fail("profile.profile_status", "unknown profile status")
    if not isinstance(profile["purpose"], str) or not profile["purpose"]:
        _fail("profile.purpose", "must state the permitted use")
    _walk_parameter_tree(profile["parameters"], "profile.parameters")
    if not isinstance(profile["sources"], list) or not profile["sources"]:
        _fail("profile.sources", "must list at least one source ID")
    _unique_ids(profile["sources"], "profile.sources")
    if not isinstance(profile["capabilities"], dict):
        _fail("profile.capabilities", "must be an object")
    if "case_ids" in profile:
        _unique_ids(profile["case_ids"], "profile.case_ids")
    if "blocked_reasons" in profile and not isinstance(profile["blocked_reasons"], list):
        _fail("profile.blocked_reasons", "must be a list")
    if "traffic_profiles" in profile:
        if not isinstance(profile["traffic_profiles"], dict):
            _fail("profile.traffic_profiles", "must be an object")
        for traffic_id, traffic in profile["traffic_profiles"].items():
            _id(traffic_id, f"profile.traffic_profiles.{traffic_id}")
            if not isinstance(traffic, dict):
                _fail(f"profile.traffic_profiles.{traffic_id}", "must be an object")
            if "evidence_status" not in traffic:
                _fail(f"profile.traffic_profiles.{traffic_id}", "must expose evidence_status")
            if traffic["evidence_status"] not in EVIDENCE_STATUSES:
                _fail(f"profile.traffic_profiles.{traffic_id}.evidence_status", "unknown evidence status")
            if "formal_eligible" not in traffic or not isinstance(traffic["formal_eligible"], bool):
                _fail(f"profile.traffic_profiles.{traffic_id}.formal_eligible", "must be an explicit boolean")
    if requested_mode == "formal":
        if profile["profile_status"] != "ENABLED":
            _fail("profile.profile_status", "formal mode requires an ENABLED profile")
        for traffic_id, traffic in profile.get("traffic_profiles", {}).items():
            if not traffic["formal_eligible"]:
                _fail(f"profile.traffic_profiles.{traffic_id}", "formal mode is disabled for this traffic profile")
        publication = profile.get("publication", {})
        if publication.get("formal_eligible") is not True:
            _fail("profile.publication.formal_eligible", "formal mode requires explicit publication eligibility")
    return profile


def validate_run_output(output: dict) -> dict:
    """Validate a run envelope and every embedded public record."""

    output = _require_mapping(output, "RunOutput")
    allowed = set(SCHEMAS["RunOutput"]["properties"])
    _check_unknown(output, allowed, "RunOutput")
    _require(output, SCHEMAS["RunOutput"]["required"], "RunOutput")
    if output["contract_version"] != CONTRACT_VERSION:
        _fail("RunOutput.contract_version", f"expected {CONTRACT_VERSION}")
    for field in ("run_id", "experiment_id", "profile_id", "baseline_id"):
        _id(output[field], f"RunOutput.{field}")
    if not _is_int(output["seed"]) or output["seed"] < 0:
        _fail("RunOutput.seed", "must be a non-negative integer")
    for field in ("traffic_sha256", "measurement_calendar_sha256", "model_hash"):
        if not isinstance(output[field], str) or not _HASH_RE.fullmatch(output[field]):
            _fail(f"RunOutput.{field}", "must be a 64-character SHA-256 hex string")
    if output["evidence_status"] not in EVIDENCE_STATUSES:
        _fail("RunOutput.evidence_status", "unknown evidence status")
    list_kinds = {"packets": "Packet", "events": "Event", "intervals": "RadioInterval", "increments": "EnergyIncrement", "tx": "TxRecord", "outcomes": "PacketOutcome", "trials": "DetectionTrial", "actions": "Action"}
    for field, kind in list_kinds.items():
        if not isinstance(output[field], list):
            _fail(f"RunOutput.{field}", "must be a list")
        for index, record in enumerate(output[field]):
            try:
                validate_record(kind, record)
            except ValidationError as exc:
                _fail(f"RunOutput.{field}[{index}]", str(exc))
    identity_fields = {
        "packets": "packet_id",
        "events": "event_id",
        "intervals": "interval_id",
        "increments": "increment_id",
        "tx": "tx_id",
        "outcomes": "packet_id",
        "trials": "trial_id",
        "actions": "action_id",
    }
    for field, identity in identity_fields.items():
        values = [record[identity] for record in output[field]]
        if len(values) != len(set(values)):
            _fail(f"RunOutput.{field}", f"duplicate {identity}; IDs must be unique within a run")
    return output


def load_json(path: str | Path) -> Any:
    """Load UTF-8 JSON without losing nulls, large integers, or Chinese text."""

    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load JSON {path}: {exc}") from exc


def load_profile(path: str | Path, requested_mode: str | None = None) -> dict:
    profile = load_json(path)
    return validate_profile(profile, requested_mode=requested_mode)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
