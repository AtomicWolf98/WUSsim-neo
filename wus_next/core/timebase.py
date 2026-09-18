"""Integer-nanosecond time utilities for the W01 service core.

Contract rules implemented here:

* all times are integers in nanoseconds, intervals are ``[start_ns, end_ns)``;
* booleans and floats are rejected as times and durations so that ms/ns unit
  confusion cannot silently reorder events (an unconverted ``1.5`` ms value
  must fail instead of silently becoming 1 ns);
* the axis is the full integer line: analysis windows may use negative bounds
  so that window-edge clipping can be verified around zero; the public record
  layer (``wus_next.contracts``) separately enforces non-negative records.
"""

from __future__ import annotations

from decimal import Decimal

from .reasons import (
    CoreError,
    DURATION_NOT_POSITIVE,
    NEGATIVE_POWER,
    POWER_NOT_NUMERIC,
    TIME_NEGATIVE,
    TIME_NOT_INTEGER,
    WINDOW_NOT_POSITIVE,
)

NS_PER_MS = 1_000_000
NS_PER_US = 1_000
NS_PER_S = 1_000_000_000


def require_int_ns(value, what: str) -> int:
    """Return *value* when it is a non-bool integer, otherwise fail."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise CoreError(
            TIME_NOT_INTEGER,
            f"{what} must be an integer nanosecond count, got {type(value).__name__} {value!r}; convert ms with ms_to_ns() first",
        )
    return value


def require_nonnegative_ns(value, what: str) -> int:
    value = require_int_ns(value, what)
    if value < 0:
        raise CoreError(TIME_NEGATIVE, f"{what} is negative ({value} ns)")
    return value


def require_positive_duration_ns(value, what: str) -> int:
    value = require_int_ns(value, what)
    if value <= 0:
        raise CoreError(DURATION_NOT_POSITIVE, f"{what} must be positive, got {value} ns")
    return value


def require_window_ns(start_ns, end_ns) -> tuple[int, int]:
    """Validate an analysis window; the end must be strictly after the start."""

    start = require_int_ns(start_ns, "window.start_ns")
    end = require_int_ns(end_ns, "window.end_ns")
    if end <= start:
        raise CoreError(WINDOW_NOT_POSITIVE, f"window [{start_ns}, {end_ns}) has no positive length")
    return start, end


def clip_interval(start_ns: int, end_ns: int, window_start_ns: int, window_end_ns: int) -> tuple[int, int] | None:
    """Intersect ``[start, end)`` with the window; ``None`` when empty."""

    start = max(start_ns, window_start_ns)
    end = min(end_ns, window_end_ns)
    if end <= start:
        return None
    return start, end


def overlap_interval(a_start: int, a_end: int, b_start: int, b_end: int) -> tuple[int, int] | None:
    """Overlap of two half-open intervals; ``None`` when disjoint or adjacent."""

    start = max(a_start, b_start)
    end = min(a_end, b_end)
    if end <= start:
        return None
    return start, end


def energy_unit_ms(power_unit, duration_ns: int) -> float:
    """Exact relative energy ``power_unit * duration_ns / 1e6`` (unit: ms)."""

    duration = require_positive_duration_ns(duration_ns, "energy duration")
    if isinstance(power_unit, bool) or not isinstance(power_unit, (int, float)):
        raise CoreError(POWER_NOT_NUMERIC, f"power_unit must be numeric, got {type(power_unit).__name__}")
    if isinstance(power_unit, float) and power_unit < 0 or isinstance(power_unit, int) and power_unit < 0:
        raise CoreError(NEGATIVE_POWER, f"power_unit is negative ({power_unit})")
    return float(Decimal(str(power_unit)) * Decimal(duration) / Decimal(NS_PER_MS))


def ms_to_ns(value_ms) -> int:
    """Convert an exact millisecond value to integer ns without rounding."""

    if isinstance(value_ms, bool) or not isinstance(value_ms, (int, float, Decimal, str)):
        raise CoreError(TIME_NOT_INTEGER, f"millisecond value must be numeric, got {type(value_ms).__name__}")
    ns = Decimal(str(value_ms)) * Decimal(NS_PER_MS)
    if ns != ns.to_integral_value():
        raise CoreError(TIME_NOT_INTEGER, f"millisecond value {value_ms} cannot be represented as integer ns")
    return int(ns)


def ns_to_ms_exact(duration_ns: int) -> Decimal:
    """Return a duration as an exact Decimal number of milliseconds."""

    require_int_ns(duration_ns, "duration")
    return Decimal(duration_ns) / Decimal(NS_PER_MS)
