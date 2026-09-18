"""Foundational public package for the WUS simulator upgrade.

W00 owns the data contract and evidence-aware profile loading.  Simulation
modules are deliberately not imported here; importing this package must be
side-effect free and must not start an experiment or access the network.
"""

from .contracts.validator import (
    CONTRACT_VERSION,
    ValidationError,
    load_json,
    load_profile,
    validate_profile,
    validate_record,
)
from .contracts.loader import load_record

__all__ = [
    "CONTRACT_VERSION",
    "ValidationError",
    "load_json",
    "load_record",
    "load_profile",
    "validate_profile",
    "validate_record",
]
