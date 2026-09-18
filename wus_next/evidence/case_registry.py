"""Chair-Notes case registry with explicit mechanism and measurement axes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts.validator import ValidationError, _id, load_json


CASE_REGISTRY_VERSION = "1.0"
_TRIGGERS = {"C-DRX only", "C-DRX with DCP", "C-DRX with DL WUS", "DL WUS"}
_MEASUREMENTS = {"NON_EE", "EE", "NOT_APPLICABLE"}


def _registry_path() -> Path:
    return Path(__file__).with_name("case_registry.json")


def validate_case_registry(registry: dict) -> dict:
    if not isinstance(registry, dict):
        raise ValidationError("case_registry: must be an object")
    if registry.get("registry_version") != CASE_REGISTRY_VERSION:
        raise ValidationError("case_registry.registry_version: expected 1.0")
    cases = registry.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValidationError("case_registry.cases: must be a non-empty list")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        path = f"case_registry.cases[{index}]"
        if not isinstance(case, dict):
            raise ValidationError(f"{path}: must be an object")
        allowed = {"case_id", "case_group", "trigger", "serving_measurement", "neighbor_measurement", "source_id", "locator", "evidence_status", "implementation_status", "notes"}
        unknown = sorted(set(case) - allowed)
        if unknown:
            raise ValidationError(f"{path}: unknown fields {unknown}")
        required = allowed - {"notes"}
        missing = sorted(required - set(case))
        if missing:
            raise ValidationError(f"{path}: missing fields {missing}")
        _id(case["case_id"], f"{path}.case_id")
        if case["case_id"] in seen:
            raise ValidationError(f"{path}.case_id: duplicate case ID")
        seen.add(case["case_id"])
        if case["trigger"] not in _TRIGGERS:
            raise ValidationError(f"{path}.trigger: unknown trigger {case['trigger']!r}")
        for field in ("serving_measurement", "neighbor_measurement"):
            if case[field] not in _MEASUREMENTS:
                raise ValidationError(f"{path}.{field}: unknown measurement axis")
        _id(case["source_id"], f"{path}.source_id")
        if not isinstance(case["locator"], str) or not case["locator"]:
            raise ValidationError(f"{path}.locator: must be non-empty")
        if case["evidence_status"] not in {"MEETING_AGREEMENT", "COMPANY_PROPOSAL", "RESEARCH_REFERENCE", "UNRESOLVED"}:
            raise ValidationError(f"{path}.evidence_status: invalid status")
        if case["implementation_status"] not in {"REGISTERED", "ENABLED_ABSTRACT", "RESERVED", "BLOCKED_EVIDENCE"}:
            raise ValidationError(f"{path}.implementation_status: invalid status")
        if case["case_id"] == "1-6" and case["trigger"] != "DL WUS":
            raise ValidationError("case_registry.1-6: must remain DL WUS; DCP is not permitted")
    if not {"1-1", "1-2", "1-3", "1-4", "1-5", "1-6"}.issubset(seen):
        raise ValidationError("case_registry: Case 1-1..1-6 are required")
    return registry


def load_case_registry(path: str | Path | None = None) -> dict:
    registry = load_json(path or _registry_path())
    return validate_case_registry(registry)


def get_case(case_id: str, registry: dict | None = None) -> dict:
    registry = validate_case_registry(registry or load_case_registry())
    for case in registry["cases"]:
        if case["case_id"] == case_id:
            return case.copy()
    raise KeyError(f"unknown case_id: {case_id}")

