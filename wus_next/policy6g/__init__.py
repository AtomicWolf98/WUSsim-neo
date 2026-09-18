"""6G Case-1 connected-mode WUS/DCP/DRX policy for W03.

Public contract interfaces:

* :func:`initial_state`
* :func:`step`

The policy emits only Action records. Energy integration, traffic generation,
PHY detection sampling, and official KPIs belong to other modules.
"""

from __future__ import annotations

from .axes import (
    COUPLING_DCP,
    COUPLING_NONE,
    COUPLING_WUS_COUPLED,
    COUPLING_WUS_INDEPENDENT,
    CaseAxisError,
    case_ids,
    resolve_case_axes,
)
from .machine import (
    EVT_DETECTION_RESULT,
    EVT_MEASUREMENT_WINDOW,
    EVT_MO_START,
    EVT_NEW_TRANSMISSION,
    EVT_PACKET_ARRIVAL,
    EVT_TICK,
    EVT_TIMER_EXPIRY,
    PolicyError,
    action_kinds,
    initial_state,
    step,
    summarize_counts,
)
from .strategies import (
    COMPANY_R1_2603659,
    LEGACY_REPLAY,
    OPTIMIZED_TIMER,
    SAME_TIMER,
    STRATEGY_IDS,
    StrategyError,
    resolve_strategy_config,
)


__all__ = [
    "COMPANY_R1_2603659",
    "COUPLING_DCP",
    "COUPLING_NONE",
    "COUPLING_WUS_COUPLED",
    "COUPLING_WUS_INDEPENDENT",
    "EVT_DETECTION_RESULT",
    "EVT_MEASUREMENT_WINDOW",
    "EVT_MO_START",
    "EVT_NEW_TRANSMISSION",
    "EVT_PACKET_ARRIVAL",
    "EVT_TICK",
    "EVT_TIMER_EXPIRY",
    "CaseAxisError",
    "LEGACY_REPLAY",
    "OPTIMIZED_TIMER",
    "PolicyError",
    "SAME_TIMER",
    "STRATEGY_IDS",
    "StrategyError",
    "action_kinds",
    "case_ids",
    "initial_state",
    "resolve_case_axes",
    "resolve_strategy_config",
    "step",
    "summarize_counts",
]
