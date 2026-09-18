"""Explicit timer strategy profiles for Case 1 policy.

Three product-facing entries are required by W03:

* ``legacy_replay`` — every-slot IAT refresh matching the reviewed baseline.
* ``same_timer`` — attribution profile; same timers across coupled/independent.
* ``optimized_timer`` — strategy profile; shorter WUS/post-data timers.

The optional ``company_r1_2603659`` entry only copies company Table-12 numbers
into an explicit configuration knob. It is not a default matrix profile and is
not a standard target.
"""

from __future__ import annotations

from typing import Any

from .axes import (
    COUPLING_DCP,
    COUPLING_NONE,
    COUPLING_WUS_COUPLED,
    COUPLING_WUS_INDEPENDENT,
    CaseAxisError,
)


LEGACY_REPLAY = "legacy_replay"
SAME_TIMER = "same_timer"
OPTIMIZED_TIMER = "optimized_timer"
COMPANY_R1_2603659 = "company_r1_2603659"

STRATEGY_IDS = (
    LEGACY_REPLAY,
    SAME_TIMER,
    OPTIMIZED_TIMER,
    COMPANY_R1_2603659,
)

# IAT refresh policy for non-legacy strategies: only new transmissions restart.
IAT_REFRESH_NEW_TX = "new_transmission"
# Legacy: every active slot refreshes the inactivity timer.
IAT_REFRESH_LEGACY_SLOT = "legacy_every_slot"
IAT_REFRESH_OFF = "off"

NS_PER_MS = 1_000_000


class StrategyError(ValueError):
    """Raised when a strategy profile is unknown or misapplied."""


def _ms(value: float | int | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise StrategyError(f"expected finite ms number, got {value!r}")
    return int(round(float(value) * NS_PER_MS))


def _require_ms(value: float | int, name: str) -> int:
    out = _ms(value)
    if out is None or out <= 0:
        raise StrategyError(f"{name} must be positive ms, got {value!r}")
    return out


def _traffic_cdrx(profile: dict, traffic_profile: str) -> tuple[int, int, int]:
    """Return (cycle_ms, on_ms, iat_ms) from the loaded profile traffic block."""

    traffic = profile.get("parameters", {}).get("traffic", {})
    entry = traffic.get(traffic_profile)
    if not isinstance(entry, dict):
        raise StrategyError(f"missing traffic block for {traffic_profile!r}")
    cdrx = entry.get("cdrx")
    if not isinstance(cdrx, dict) or "value" not in cdrx:
        raise StrategyError(f"missing traffic.{traffic_profile}.cdrx.value")
    raw = cdrx["value"]
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise StrategyError(f"cdrx tuple must be [cycle, on, iat], got {raw!r}")
    cycle, on, iat = raw
    return float(cycle), float(on), float(iat)


def _measurement(profile: dict) -> dict:
    meas = profile.get("parameters", {}).get("measurement", {})
    cycle = meas.get("cycle", {}).get("value", 20)
    duration = meas.get("duration", {}).get("value", 0.5)
    rx_duration = meas.get("rx_duration", {}).get("value", 0.5)
    gap = meas.get("gap", {}).get("value", 5)
    return {
        "measurement_cycle_ms": float(cycle),
        "measurement_duration_ms": float(duration),
        "wus_rx_duration_ms": float(rx_duration),
        "gap_ms": float(gap),
    }


def resolve_strategy_config(
    profile: dict,
    axes: dict,
    policy_id: str,
    *,
    traffic_profile: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict:
    """Build a JSON-compatible timer configuration for one strategy × case.

    Does not compute energy or KPI. Company numbers stay behind an explicit
    ``company_r1_2603659`` id and never leak into default matrix strategies.
    """

    if policy_id not in STRATEGY_IDS:
        raise StrategyError(f"unknown policy_id {policy_id!r}; expected one of {STRATEGY_IDS}")

    traffic_name = traffic_profile or profile.get("default_traffic_profile") or "FTP3"
    cycle_ms, on_ms, iat_ms = _traffic_cdrx(profile, traffic_name)
    meas = _measurement(profile)
    gap_ms = meas["gap_ms"]
    rx_ms = meas["wus_rx_duration_ms"]
    mcycle_ms = meas["measurement_cycle_ms"]
    mdur_ms = meas["measurement_duration_ms"]

    coupling = axes["coupling"]
    cfg: dict[str, Any] = {
        "policy_id": policy_id,
        "case_id": axes["case_id"],
        "traffic_profile": traffic_name,
        "coupling": coupling,
        "has_cdrx": axes["has_cdrx"],
        "has_dcp": axes["has_dcp"],
        "has_wus": axes["has_wus"],
        "meas_ee": axes["meas_ee"],
        "grid_ns": 500_000,
    }

    if policy_id == LEGACY_REPLAY:
        cfg.update(
            {
                "iat_refresh_mode": IAT_REFRESH_LEGACY_SLOT,
                "cdrx_cycle_ns": _require_ms(cycle_ms, "cdrx cycle"),
                "on_duration_ns": _require_ms(on_ms, "on duration"),
                "iat_ns": _require_ms(iat_ms, "iat"),
                "dcp_gap_ns": _ms(2),
                "dcp_rx_ns": _ms(rx_ms),
                "wus_period_ns": _require_ms(cycle_ms, "wus period"),
                "wus_rx_ns": _ms(rx_ms),
                "gap_ns": _ms(gap_ms),
                # Independent tail follows legacy IAT.
                "wus_timer_ns": _require_ms(iat_ms, "legacy wus timer"),
                "post_data_timer_ns": _require_ms(iat_ms, "legacy post-data timer"),
                "measurement_cycle_ns": _ms(mcycle_ms),
                "measurement_duration_ns": _ms(mdur_ms),
                "mo_offset_ns": 0,
                "cdrx_offset_ns": 0,
                "wus_offset_ns": 0,
                "measurement_offset_ns": 0,
                "coupled_skip_fallback": True,
                "company_abstraction_note": "legacy skip fallback and every-slot IAT refresh",
            }
        )
    elif policy_id == SAME_TIMER:
        # Attribution: same IAT/on across mechanisms so deltas are attributable.
        cfg.update(
            {
                "iat_refresh_mode": IAT_REFRESH_NEW_TX,
                "cdrx_cycle_ns": _require_ms(cycle_ms, "cdrx cycle"),
                "on_duration_ns": _require_ms(on_ms, "on duration"),
                "iat_ns": _require_ms(iat_ms, "iat"),
                "dcp_gap_ns": _ms(gap_ms),
                "dcp_rx_ns": _ms(rx_ms),
                "wus_period_ns": _require_ms(cycle_ms, "wus period"),
                "wus_rx_ns": _ms(rx_ms),
                "gap_ns": _ms(gap_ms),
                "wus_timer_ns": _require_ms(iat_ms, "same-timer wus timer"),
                "post_data_timer_ns": _require_ms(iat_ms, "same-timer post-data timer"),
                "measurement_cycle_ns": _ms(mcycle_ms),
                "measurement_duration_ns": _ms(mdur_ms),
                "mo_offset_ns": 0,
                "cdrx_offset_ns": 0,
                "wus_offset_ns": 0,
                "measurement_offset_ns": 0,
                "coupled_skip_fallback": True,
                "company_abstraction_note": "same-timer attribution; skip fallback is company abstraction",
            }
        )
    elif policy_id == OPTIMIZED_TIMER:
        # Strategy: shorter WUS/post-data timers; C-DRX IAT only for coupled cases.
        optimized_iat = 25 if coupling in {COUPLING_WUS_COUPLED, COUPLING_DCP, COUPLING_NONE} else 8
        cfg.update(
            {
                "iat_refresh_mode": IAT_REFRESH_NEW_TX,
                "cdrx_cycle_ns": _require_ms(cycle_ms, "cdrx cycle"),
                "on_duration_ns": _require_ms(on_ms, "on duration"),
                "iat_ns": _require_ms(optimized_iat, "optimized iat"),
                "dcp_gap_ns": _ms(gap_ms),
                "dcp_rx_ns": _ms(rx_ms),
                "wus_period_ns": _require_ms(cycle_ms, "wus period"),
                "wus_rx_ns": _ms(rx_ms),
                "gap_ns": _ms(gap_ms),
                "wus_timer_ns": 8 * NS_PER_MS,
                "post_data_timer_ns": 8 * NS_PER_MS,
                "measurement_cycle_ns": _ms(mcycle_ms),
                "measurement_duration_ns": _ms(mdur_ms),
                "mo_offset_ns": 0,
                "cdrx_offset_ns": 0,
                "wus_offset_ns": 0,
                "measurement_offset_ns": 0,
                "coupled_skip_fallback": False,
                "company_abstraction_note": "optimized short timers; no silent deep claim",
            }
        )
    else:  # COMPANY_R1_2603659
        company = profile.get("parameters", {})
        wus = company.get("wus", {})
        timer_ms = wus.get("timer_ms", {}).get("value", 8)
        gap_like = wus.get("cdrx_offset", {}).get("value", 3)
        light_ms = wus.get("wake_delay", {}).get("value", {}).get("light_ms", 3)
        deep_ms = wus.get("wake_delay", {}).get("value", {}).get("deep_ms", 10)
        # Independent company MO period is 10 slots; SCS must be supplied by
        # the consumer. Default abstract 30 kHz slot => 5 ms when unspecified.
        period_slots = wus.get("period_slots", {}).get("value", 10)
        slot_ms = float(company.get("time", {}).get("company_slot_ms", {}).get("value", 0.5))
        mo_period_ms = float(period_slots) * slot_ms
        cfg.update(
            {
                "iat_refresh_mode": IAT_REFRESH_NEW_TX,
                "cdrx_cycle_ns": _require_ms(cycle_ms, "cdrx cycle"),
                "on_duration_ns": _require_ms(on_ms, "on duration"),
                "iat_ns": _require_ms(iat_ms, "iat"),
                "dcp_gap_ns": _ms(gap_like),
                "dcp_rx_ns": _ms(rx_ms),
                "wus_period_ns": _require_ms(mo_period_ms if coupling == COUPLING_WUS_INDEPENDENT else cycle_ms, "company wus period"),
                "wus_rx_ns": _ms(rx_ms),
                "gap_ns": _ms(gap_like),
                "wus_timer_ns": _require_ms(timer_ms, "company wus timer"),
                "post_data_timer_ns": _require_ms(timer_ms, "company post-data timer"),
                "measurement_cycle_ns": _ms(mcycle_ms),
                "measurement_duration_ns": _ms(mdur_ms),
                "mo_offset_ns": 0,
                "cdrx_offset_ns": 0,
                "wus_offset_ns": 0,
                "measurement_offset_ns": 0,
                "coupled_skip_fallback": True,
                "company_light_wake_delay_ns": _ms(light_ms),
                "company_deep_wake_delay_ns": _ms(deep_ms),
                "company_abstraction_note": (
                    "R1-2603659 Table12 configuration comparison only; "
                    "wake delays are capability hints for W01, not deep guarantees"
                ),
            }
        )

    # Independent WUS has no periodic C-DRX on-duration from the cycle grid.
    if coupling == COUPLING_WUS_INDEPENDENT:
        cfg["use_cdrx_on_grid"] = False
        cfg["use_wus_timer"] = True
        cfg["use_post_data_timer"] = True
    elif coupling == COUPLING_WUS_COUPLED:
        cfg["use_cdrx_on_grid"] = True
        cfg["use_wus_timer"] = False
        cfg["use_post_data_timer"] = False
    elif coupling == COUPLING_DCP:
        cfg["use_cdrx_on_grid"] = True
        cfg["use_wus_timer"] = False
        cfg["use_post_data_timer"] = False
    else:
        cfg["use_cdrx_on_grid"] = True
        cfg["use_wus_timer"] = False
        cfg["use_post_data_timer"] = False

    if overrides:
        if not isinstance(overrides, dict):
            raise StrategyError("strategy overrides must be an object")
        for key, value in overrides.items():
            if not isinstance(key, str) or not key:
                raise StrategyError(f"invalid override key {key!r}")
            cfg[key] = value

    # Validate positive durations where required.
    for key in (
        "cdrx_cycle_ns",
        "on_duration_ns",
        "wus_period_ns",
        "measurement_cycle_ns",
        "measurement_duration_ns",
    ):
        value = cfg.get(key)
        if not isinstance(value, int) or value <= 0:
            raise StrategyError(f"{key} must be a positive integer ns, got {value!r}")
    for key in ("iat_ns", "wus_timer_ns", "post_data_timer_ns", "gap_ns", "wus_rx_ns", "dcp_gap_ns", "dcp_rx_ns"):
        value = cfg.get(key)
        if not isinstance(value, int) or value < 0:
            raise StrategyError(f"{key} must be a non-negative integer ns, got {value!r}")

    if coupling == COUPLING_WUS_COUPLED and cfg["wus_rx_ns"] + cfg["gap_ns"] >= cfg["cdrx_cycle_ns"]:
        raise StrategyError("coupled WUS rx+gap must fit inside the DRX cycle")
    if coupling == COUPLING_DCP and cfg["dcp_rx_ns"] + cfg["dcp_gap_ns"] >= cfg["cdrx_cycle_ns"]:
        raise StrategyError("DCP rx+gap must fit inside the DRX cycle")

    cfg["evidence_status"] = "ASSUMPTION"
    cfg["deep_sleep_claim"] = False
    return cfg
