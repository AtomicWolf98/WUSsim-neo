"""Fixed observation-window energy integration.

Radio intervals are clipped to [start_ns, end_ns). Energy increments are
clipped proportionally when they span the boundary. Instantaneous at_ns
edges contribute only if the edge falls inside the window (half-open:
at_ns == end_ns is excluded). Drain-phase energy is never folded into
the fixed-window average power.
"""

from __future__ import annotations


NS_PER_MS = 1_000_000


def _clip_interval(start_ns: int, end_ns: int, win_start: int, win_end: int) -> tuple[int, int] | None:
    if end_ns <= win_start or start_ns >= win_end:
        return None
    return max(start_ns, win_start), min(end_ns, win_end)


def integrate_window_energy(
    intervals: list[dict],
    increments: list[dict],
    *,
    start_ns: int,
    end_ns: int,
    receiver_id: str | None = None,
) -> dict:
    """Return energy totals for the fixed window.

    receiver_id optionally restricts RadioInterval accounting to MR or LR.
    Energy increments are hardware energy and are always included when they
    overlap the window (they do not occupy MR timeline).
    """

    if not isinstance(start_ns, int) or not isinstance(end_ns, int) or end_ns <= start_ns:
        raise ValueError("observation window must satisfy end_ns > start_ns")

    window_ns = end_ns - start_ns
    interval_energy = 0.0
    by_state: dict[str, float] = {}
    by_receiver: dict[str, float] = {}
    clipped_intervals = 0
    for interval in intervals:
        if receiver_id is not None and interval.get("receiver_id") != receiver_id:
            continue
        clipped = _clip_interval(int(interval["start_ns"]), int(interval["end_ns"]), start_ns, end_ns)
        if clipped is None:
            continue
        c_start, c_end = clipped
        duration_ns = c_end - c_start
        power = float(interval.get("power_unit", 0.0))
        energy = power * duration_ns / NS_PER_MS
        interval_energy += energy
        state = str(interval.get("state", "unknown"))
        by_state[state] = by_state.get(state, 0.0) + energy
        recv = str(interval.get("receiver_id", "unknown"))
        by_receiver[recv] = by_receiver.get(recv, 0.0) + energy
        if c_start != int(interval["start_ns"]) or c_end != int(interval["end_ns"]):
            clipped_intervals += 1

    increment_energy = 0.0
    by_hardware: dict[str, float] = {}
    clipped_increments = 0
    for inc in increments:
        at_ns = inc.get("at_ns")
        start = inc.get("start_ns")
        end = inc.get("end_ns")
        value = float(inc.get("energy_unit_ms", 0.0))
        if start is not None and end is not None:
            clipped = _clip_interval(int(start), int(end), start_ns, end_ns)
            if clipped is None:
                continue
            c_start, c_end = clipped
            full = int(end) - int(start)
            if full <= 0:
                continue
            share = value * (c_end - c_start) / full
            if c_start != int(start) or c_end != int(end):
                clipped_increments += 1
        elif at_ns is not None:
            if start_ns <= int(at_ns) < end_ns:
                share = value
            else:
                continue
        else:
            continue
        increment_energy += share
        group = str(inc.get("hardware_group", "unknown"))
        by_hardware[group] = by_hardware.get(group, 0.0) + share

    total = interval_energy + increment_energy
    # energy_unit_ms is already power * duration_ms; average power =
    # energy / window_ms = energy * 1e6 / window_ns.
    window_ms = window_ns / NS_PER_MS
    avg_power = total / window_ms if window_ms > 0 else 0.0
    return {
        "start_ns": start_ns,
        "end_ns": end_ns,
        "window_ns": window_ns,
        "interval_energy_unit_ms": interval_energy,
        "increment_energy_unit_ms": increment_energy,
        "energy_unit_ms": total,
        "avg_power_unit": avg_power,
        "energy_by_state": by_state,
        "energy_by_receiver": by_receiver,
        "energy_by_hardware_group": by_hardware,
        "clipped_interval_count": clipped_intervals,
        "clipped_increment_count": clipped_increments,
    }


def split_warmup_drain_windows(
    *,
    start_ns: int,
    end_ns: int,
    warmup_end_ns: int | None = None,
    drain_end_ns: int | None = None,
) -> dict:
    """Describe fixed window vs warmup backlog boundary vs drain observation."""

    if end_ns <= start_ns:
        raise ValueError("end_ns must be > start_ns")
    warm_end = start_ns if warmup_end_ns is None else int(warmup_end_ns)
    if warm_end < start_ns or warm_end > end_ns:
        raise ValueError("warmup_end_ns must lie inside [start_ns, end_ns]")
    drain_end = end_ns if drain_end_ns is None else int(drain_end_ns)
    if drain_end < end_ns:
        raise ValueError("drain_end_ns must be >= end_ns")
    return {
        "observation": {"start_ns": start_ns, "end_ns": end_ns, "window_ns": end_ns - start_ns},
        "warmup": {"start_ns": start_ns, "end_ns": warm_end, "backlog_boundary_ns": warm_end},
        "drain": {
            "start_ns": end_ns,
            "end_ns": drain_end,
            "window_ns": drain_end - end_ns,
            "energy_excluded_from_power": True,
        },
    }
