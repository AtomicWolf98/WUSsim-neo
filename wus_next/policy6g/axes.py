"""Case 1 axis resolution for abstract 6G connected-mode WUS policy.

Axes follow RAN1#126 Chair Notes B4787 and the W00 frozen case registry.
This module does not invent Rel-19 multi-DRX procedures or decide deep sleep.
"""

from __future__ import annotations

from typing import Any

from ..evidence.case_registry import get_case, load_case_registry


# Coupling axis is explicit; never inferred from a free-form case string alone.
COUPLING_NONE = "none"
COUPLING_DCP = "dcp"
COUPLING_WUS_COUPLED = "wus_coupled"
COUPLING_WUS_INDEPENDENT = "wus_independent"

TRIGGER_CDRX = "C-DRX only"
TRIGGER_DCP = "C-DRX with DCP"
TRIGGER_WUS_COUPLED = "C-DRX with DL WUS"
TRIGGER_WUS_INDEPENDENT = "DL WUS"

_MEASUREMENT_AXIS = {"NON_EE", "EE"}
_CASE_ONE = frozenset({"1-1", "1-2", "1-3", "1-4", "1-5", "1-6"})


class CaseAxisError(ValueError):
    """Raised when a Case 1 axis combination is not registered or is illegal."""


def resolve_case_axes(case_id: str, registry: dict | None = None) -> dict:
    """Return explicit Case-1 axes for *case_id*.

    The returned mapping keeps trigger, measurement, coupling, and mechanism
    flags separate so strategy code never relies on a pile of case strings.
    Case number alone does not decide deep sleep; W01 owns wake feasibility.
    """

    if case_id not in _CASE_ONE:
        raise CaseAxisError(f"unsupported Case 1 id: {case_id!r}")
    try:
        row = get_case(case_id, registry or load_case_registry())
    except KeyError as exc:
        raise CaseAxisError(str(exc)) from exc

    trigger = row["trigger"]
    serving = row["serving_measurement"]
    neighbor = row["neighbor_measurement"]
    if neighbor != "NOT_APPLICABLE":
        raise CaseAxisError(
            f"case {case_id}: neighbor measurement {neighbor!r} is reserved for later tasks"
        )
    if serving not in _MEASUREMENT_AXIS:
        raise CaseAxisError(f"case {case_id}: invalid serving measurement {serving!r}")

    has_cdrx = trigger in {TRIGGER_CDRX, TRIGGER_DCP, TRIGGER_WUS_COUPLED}
    has_dcp = trigger == TRIGGER_DCP
    has_wus = trigger in {TRIGGER_WUS_COUPLED, TRIGGER_WUS_INDEPENDENT}

    if trigger == TRIGGER_CDRX:
        coupling = COUPLING_NONE
    elif trigger == TRIGGER_DCP:
        coupling = COUPLING_DCP
    elif trigger == TRIGGER_WUS_COUPLED:
        coupling = COUPLING_WUS_COUPLED
    elif trigger == TRIGGER_WUS_INDEPENDENT:
        coupling = COUPLING_WUS_INDEPENDENT
    else:
        raise CaseAxisError(f"case {case_id}: unknown trigger {trigger!r}")

    if case_id == "1-6" and has_dcp:
        raise CaseAxisError("case 1-6 must remain DL WUS; DCP is not permitted")
    if case_id == "1-5" and has_dcp:
        raise CaseAxisError("case 1-5 must remain independent DL WUS; DCP is not permitted")

    return {
        "case_id": case_id,
        "trigger": trigger,
        "serving_measurement": serving,
        "neighbor_measurement": neighbor,
        "coupling": coupling,
        "has_cdrx": has_cdrx,
        "has_dcp": has_dcp,
        "has_wus": has_wus,
        "uses_dcp_only": has_dcp and not has_wus,
        "is_coupled_wus": coupling == COUPLING_WUS_COUPLED,
        "is_independent_wus": coupling == COUPLING_WUS_INDEPENDENT,
        "meas_ee": serving == "EE",
        "source_id": row["source_id"],
        "locator": row["locator"],
        "evidence_status": row["evidence_status"],
    }


def case_ids() -> tuple[str, ...]:
    """Registered Case 1 ids in stable order."""

    return tuple(sorted(_CASE_ONE))
