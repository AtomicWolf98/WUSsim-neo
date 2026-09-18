"""Integer time and relative-energy conversions used by contract checks."""

from __future__ import annotations

from decimal import Decimal


NS_PER_MS = 1_000_000
LPWUS_OFFSET_UNIT_MS = Decimal("0.125")


def energy_unit_ms(power_unit: int | float, duration_ns: int) -> float:
    """Return relative energy for a positive integer-nanosecond interval."""

    if isinstance(duration_ns, bool) or not isinstance(duration_ns, int):
        raise TypeError("duration_ns must be an integer")
    if duration_ns <= 0:
        raise ValueError("duration_ns must be positive")
    if isinstance(power_unit, bool) or not isinstance(power_unit, (int, float)):
        raise TypeError("power_unit must be numeric")
    return float(Decimal(str(power_unit)) * Decimal(duration_ns) / Decimal(NS_PER_MS))


def lpwus_offset_to_ms(raw_offset: int) -> Decimal:
    """Convert TS 38.331 LP-WUS offset units to milliseconds exactly."""

    if isinstance(raw_offset, bool) or not isinstance(raw_offset, int):
        raise TypeError("raw_offset must be an integer")
    if not 41 <= raw_offset <= 592:
        raise ValueError("LP-WUS offset must be in the normative 41..592 range")
    return Decimal(raw_offset) * LPWUS_OFFSET_UNIT_MS


def ms_to_ns(value_ms: int | float | Decimal) -> int:
    """Convert an exact millisecond value to integer ns without rounding."""

    value = Decimal(str(value_ms))
    ns = value * Decimal(NS_PER_MS)
    if ns != ns.to_integral_value():
        raise ValueError("millisecond value cannot be represented as integer ns")
    return int(ns)

