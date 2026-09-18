"""JSON-compatible contract records and strict validation helpers."""

from .validator import (
    CONTRACT_VERSION,
    EVIDENCE_STATUSES,
    ValidationError,
    load_json,
    load_profile,
    validate_interval_set,
    validate_profile,
    validate_record,
    validate_run_output,
)
from .loader import load_record, load_run_output

__all__ = [
    "CONTRACT_VERSION",
    "EVIDENCE_STATUSES",
    "ValidationError",
    "load_json",
    "load_record",
    "load_profile",
    "load_run_output",
    "validate_interval_set",
    "validate_profile",
    "validate_record",
    "validate_run_output",
]
