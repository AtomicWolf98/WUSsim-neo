"""Deterministic traffic and paging stream generators (W02).

All generators are pure functions of ``(profile, seed, end_ns)``:

* no module-global random state, no wall clock, no network;
* arrivals are emitted in integer nanoseconds on ``[0, end_ns)`` and every
  arrival whose quantized time falls inside the window is retained;
* regenerating with a larger ``end_ns`` extends the same stream, so a
  windowed caller never loses a late-window arrival;
* the returned objects are plain JSON-compatible Packet/Event dicts (the
  contract records), not live generators, so downstream modules receive a
  fixed stream that cannot leak future arrivals to a policy.

Continuous processes are evaluated in double precision and quantized to
the nearest integer nanosecond with round-half-up; the absolute
quantization error is at most 0.5 ns per emitted arrival and is recorded
in each record's ``metadata``.  Arrivals are never snapped onto a DRX,
measurement, or WUS grid.
"""

from __future__ import annotations

import math
from pathlib import Path

from .layers import layering_summary
from .profile import (
    PAGING_MODEL_CLASSES,
    TrafficProfileError,
    check_generation_allowed,
    load_traffic_profile,
    validate_traffic_profile,
    with_ue,
)
from .streams import stream_rng
from .truncated_normal import sample_truncated_normal

ARRIVAL_QUANTIZATION = "round_half_up_nearest_ns"
ARRIVAL_ERROR_BOUND_NS = 0.5


def generate_traffic(profile, seed: int, end_ns: int) -> list[dict]:
    """Formal W02 entry point: return Packet records for one UE stream."""

    spec = _coerce_profile(profile)
    check_generation_allowed(spec)
    if spec["model_class"] in PAGING_MODEL_CLASSES:
        raise TrafficProfileError(
            f"{spec['traffic_id']} is a paging profile; use generate_paging_events for Event records"
        )
    _check_seed(seed)
    _check_end(end_ns)
    if spec["model_class"] == "poisson_fixed_size":
        return generate_poisson_fixed_size(spec, seed, end_ns)
    if spec["model_class"] == "xr_single_stream":
        return generate_xr_single_stream(spec, seed, end_ns)
    if spec["model_class"] == "voip_proxy_legacy":
        return generate_voip_proxy(spec, seed, end_ns)
    raise TrafficProfileError(f"model_class {spec['model_class']!r} does not produce Packets")


def generate_paging_events(profile, seed: int, end_ns: int) -> list[dict]:
    """Return Event records for one UE paging stream."""

    spec = _coerce_profile(profile)
    check_generation_allowed(spec)
    if spec["model_class"] not in PAGING_MODEL_CLASSES:
        raise TrafficProfileError(
            f"{spec['traffic_id']} is not a paging profile; use generate_traffic for Packet records"
        )
    _check_seed(seed)
    _check_end(end_ns)
    if spec["model_class"] == "paging_poisson_process":
        return generate_paging_poisson(spec, seed, end_ns)
    return generate_paging_bernoulli(spec, seed, end_ns)


def generate_traffic_multi_ue(profile, seed: int, end_ns: int, ue_ids: list[str]) -> dict[str, list[dict]]:
    """Generate one independent fixed stream per UE ID."""

    streams: dict[str, list[dict]] = {}
    for ue_id in ue_ids:
        streams[ue_id] = generate_traffic(with_ue(profile, ue_id), seed, end_ns)
    return streams


def generate_poisson_fixed_size(spec: dict, seed: int, end_ns: int) -> list[dict]:
    """FTP model 3 style Poisson arrivals of a fixed-size packet."""

    parameters = spec["parameters"]
    size_bytes = _positive_number(parameters["packet_size"]["value"], "packet_size")
    mean_iat_ms = _positive_number(parameters["mean_interarrival"]["value"], "mean_interarrival")
    pdb_ms = _nonnegative_number(parameters["pdb_ms"]["value"], "pdb_ms")
    size_bits = _bytes_to_bits(size_bytes)
    rng = stream_rng(seed, spec["ue_id"], "traffic")
    mean_iat_ns = mean_iat_ms * 1e6
    packets: list[dict] = []
    cumulative_ns = 0.0
    limit_ns = end_ns - ARRIVAL_ERROR_BOUND_NS
    index = 0
    while True:
        cumulative_ns += float(rng.exponential(mean_iat_ns))
        if cumulative_ns >= limit_ns:
            break
        arrival_ns = _quantize(cumulative_ns)
        if arrival_ns >= end_ns:
            break
        packets.append(
            _packet_record(
                spec,
                index=index,
                arrival_ns=arrival_ns,
                size_bits=size_bits,
                deadline_ns=arrival_ns + _ms_to_ns(pdb_ms),
                frame_id=None,
                metadata={
                    "model_class": spec["model_class"],
                    "size_bytes": size_bytes,
                    "mean_interarrival_ms": mean_iat_ms,
                    "pdb_ms": pdb_ms,
                    "arrival_quantization": ARRIVAL_QUANTIZATION,
                    "arrival_error_bound_ns": ARRIVAL_ERROR_BOUND_NS,
                    **_layer_metadata(spec, size_bits),
                },
            )
        )
        index += 1
    return packets


def generate_xr_single_stream(spec: dict, seed: int, end_ns: int) -> list[dict]:
    """TR 38.838 single-stream DL video model for one UE.

    Frame ``k`` arrives at ``offset + k/F*1000 + J`` ms where ``J`` is a
    truncated-Gaussian jitter; the frame size is an independent truncated
    Gaussian.  Both truncations use rejection sampling on the
    untruncated normal (see :mod:`wus_next.traffic.truncated_normal`).
    """

    parameters = spec["parameters"]
    rate_mbps = _positive_number(parameters["data_rate_mbps"]["value"], "data_rate_mbps")
    fps = _positive_number(parameters["frame_rate_fps"]["value"], "frame_rate_fps")
    mean_bytes = _positive_number(parameters["frame_size_mean_bytes"]["value"], "frame_size_mean_bytes")
    std_ratio = _positive_number(parameters["frame_size_std_ratio"]["value"], "frame_size_std_ratio")
    std_bytes = _positive_number(parameters["frame_size_std_bytes"]["value"], "frame_size_std_bytes")
    min_ratio = _positive_number(parameters["frame_size_min_ratio"]["value"], "frame_size_min_ratio")
    min_bytes = _positive_number(parameters["frame_size_min_bytes"]["value"], "frame_size_min_bytes")
    max_ratio = _positive_number(parameters["frame_size_max_ratio"]["value"], "frame_size_max_ratio")
    max_bytes = _positive_number(parameters["frame_size_max_bytes"]["value"], "frame_size_max_bytes")
    jitter_mean_ms = _finite_number(parameters["jitter_mean_ms"]["value"], "jitter_mean_ms")
    jitter_std_ms = _positive_number(parameters["jitter_std_ms"]["value"], "jitter_std_ms")
    jitter_range_ms = parameters["jitter_range_ms"]["value"]
    pdb_ms = _nonnegative_number(parameters["pdb_ms"]["value"], "pdb_ms")
    success_rate = _probability(parameters["success_rate_requirement"]["value"], "success_rate_requirement")
    offset_ms = _nonnegative_number(parameters["arrival_offset_ms"]["value"], "arrival_offset_ms")

    _check_xr_consistency(
        rate_mbps,
        fps,
        mean_bytes,
        std_ratio,
        std_bytes,
        min_ratio,
        min_bytes,
        max_ratio,
        max_bytes,
    )
    if not isinstance(jitter_range_ms, list) or len(jitter_range_ms) != 2:
        raise TrafficProfileError("jitter_range_ms must be a two-element list")
    jitter_low_ms = _finite_number(jitter_range_ms[0], "jitter_range_ms[0]")
    jitter_high_ms = _finite_number(jitter_range_ms[1], "jitter_range_ms[1]")
    if not jitter_low_ms < jitter_high_ms:
        raise TrafficProfileError("jitter_range_ms must satisfy low < high")
    if not jitter_low_ms <= jitter_mean_ms <= jitter_high_ms:
        raise TrafficProfileError("jitter mean must lie inside the truncation range")
    period_ms = 1000.0 / fps
    if period_ms <= (jitter_high_ms - jitter_low_ms):
        raise TrafficProfileError(
            "frame spacing must exceed the jitter span so frame arrivals stay in order "
            "(TR 38.838 section 5.1.1.2)"
        )

    rng = stream_rng(seed, spec["ue_id"], "traffic")
    period_ns = period_ms * 1e6
    offset_ns = offset_ms * 1e6
    jitter_low_ns = jitter_low_ms * 1e6
    limit_ns = end_ns - ARRIVAL_ERROR_BOUND_NS
    packets: list[dict] = []
    frame_index = 1
    while True:
        base_ns = offset_ns + frame_index * period_ns
        if base_ns + jitter_low_ns >= limit_ns:
            break
        frame_size_bytes = sample_truncated_normal(rng, mean_bytes, std_bytes, min_bytes, max_bytes)
        jitter_ms = sample_truncated_normal(rng, jitter_mean_ms, jitter_std_ms, jitter_low_ms, jitter_high_ms)
        arrival_ns = _quantize(base_ns + jitter_ms * 1e6)
        if 0 <= arrival_ns < end_ns:
            size_bits = _round_half_up(frame_size_bytes * 8.0)
            frame_id = f"{spec['ue_id']}.{spec['traffic_id']}.f{frame_index:08d}"
            packets.append(
                _packet_record(
                    spec,
                    index=frame_index - 1,
                    arrival_ns=arrival_ns,
                    size_bits=size_bits,
                    deadline_ns=arrival_ns + _ms_to_ns(pdb_ms),
                    frame_id=frame_id,
                    metadata={
                        "model_class": spec["model_class"],
                        "frame_index": frame_index,
                        "frame_period_ns": _round_half_up(period_ns),
                        "frame_size_continuous_bytes": round(frame_size_bytes, 6),
                        "jitter_ns": arrival_ns - _round_half_up(base_ns),
                        "jitter_ms": round(jitter_ms, 9),
                        "requested_rate_mbps": rate_mbps,
                        "fps": fps,
                        "pdb_ms": pdb_ms,
                        "packet_success_rate_requirement": success_rate,
                        "frame_size_mean_bytes_pre_truncation": mean_bytes,
                        "frame_size_std_bytes_pre_truncation": std_bytes,
                        "frame_size_truncation_bytes": [min_bytes, max_bytes],
                        "arrival_quantization": ARRIVAL_QUANTIZATION,
                        "arrival_error_bound_ns": ARRIVAL_ERROR_BOUND_NS,
                        **_layer_metadata(spec, size_bits),
                    },
                )
            )
        frame_index += 1
    return packets


def generate_voip_proxy(spec: dict, seed: int, end_ns: int) -> list[dict]:
    """Proxy periodic VoIP traffic; not a formal AMR model.

    The voice-activity (talk/silence) state machine of R1-070674 is not
    implemented because the original document is unavailable; this proxy
    is a fixed-period packet source kept only for the legacy comparison.
    It contains no random component, so it does not consume a stream.
    """

    parameters = spec["parameters"]
    size_bytes = _positive_number(parameters["packet_size_bytes"]["value"], "packet_size_bytes")
    period_ms = _positive_number(parameters["period_ms"]["value"], "period_ms")
    pdb_ms = _nonnegative_number(parameters["pdb_ms"]["value"], "pdb_ms")
    size_bits = _bytes_to_bits(size_bytes)
    period_ns = _ms_to_ns(period_ms)
    packets: list[dict] = []
    index = 0
    arrival_ns = 0
    while arrival_ns < end_ns:
        packets.append(
            _packet_record(
                spec,
                index=index,
                arrival_ns=arrival_ns,
                size_bits=size_bits,
                deadline_ns=arrival_ns + _ms_to_ns(pdb_ms),
                frame_id=None,
                metadata={
                    "model_class": spec["model_class"],
                    "proxy": True,
                    "talk_silence_model": "disabled_until_R1-070674",
                    "size_bytes": size_bytes,
                    "period_ms": period_ms,
                    "pdb_ms": pdb_ms,
                    "arrival_quantization": ARRIVAL_QUANTIZATION,
                    "arrival_error_bound_ns": 0.0,
                    **_layer_metadata(spec, size_bits),
                },
            )
        )
        index += 1
        arrival_ns += period_ns
    return packets


def generate_paging_poisson(spec: dict, seed: int, end_ns: int) -> list[dict]:
    """Continuous-time Poisson paging arrivals for one UE."""

    parameters = spec["parameters"]
    lambda_per_s = _nonnegative_number(parameters["lambda_per_s"]["value"], "lambda_per_s")
    po_period_ms = parameters["po_period_ms"]["value"]
    if isinstance(po_period_ms, bool) or not isinstance(po_period_ms, (int, float)) or po_period_ms <= 0:
        raise TrafficProfileError("po_period_ms must be a positive number")
    events: list[dict] = []
    if lambda_per_s == 0.0:
        return events
    rng = stream_rng(seed, spec["ue_id"], "traffic")
    mean_iat_ns = 1e9 / lambda_per_s
    cumulative_ns = 0.0
    limit_ns = end_ns - ARRIVAL_ERROR_BOUND_NS
    po_period_ns = _ms_to_ns(po_period_ms)
    index = 0
    while True:
        cumulative_ns += float(rng.exponential(mean_iat_ns))
        if cumulative_ns >= limit_ns:
            break
        arrival_ns = _quantize(cumulative_ns)
        if arrival_ns >= end_ns:
            break
        events.append(
            _paging_event_record(
                spec,
                index=index,
                time_ns=arrival_ns,
                metadata={
                    "model_class": spec["model_class"],
                    "lambda_per_s": lambda_per_s,
                    "po_period_ms": po_period_ms,
                    "po_index": arrival_ns // po_period_ns,
                    "arrival_quantization": ARRIVAL_QUANTIZATION,
                    "arrival_error_bound_ns": ARRIVAL_ERROR_BOUND_NS,
                },
            )
        )
        index += 1
    return events


def generate_paging_bernoulli(spec: dict, seed: int, end_ns: int) -> list[dict]:
    """Legacy per-PO Bernoulli paging control model.

    Each PO period independently triggers at most one paging event, placed
    at the legacy convention of the cycle start.  This is an engineering
    control model, not the continuous-time process, and it is never
    implicitly equated with the Poisson profile.
    """

    parameters = spec["parameters"]
    period_ms = _positive_number(parameters["period_ms"]["value"], "period_ms")
    p_per_po = parameters["p_per_po"]["value"]
    if isinstance(p_per_po, bool) or not isinstance(p_per_po, (int, float)):
        raise TrafficProfileError("p_per_po must be a number")
    if not 0.0 <= float(p_per_po) < 1.0:
        raise TrafficProfileError("p_per_po must satisfy 0 <= p < 1; p >= 1 is not a probability")
    p_per_po = float(p_per_po)
    events: list[dict] = []
    if p_per_po == 0.0:
        return events
    period_ns = _ms_to_ns(period_ms)
    n_cycles = math.ceil(end_ns / period_ns) if end_ns else 0
    rng = stream_rng(seed, spec["ue_id"], "traffic")
    for cycle in range(n_cycles):
        if float(rng.random()) < p_per_po:
            events.append(
                _paging_event_record(
                    spec,
                    index=cycle,
                    time_ns=cycle * period_ns,
                    metadata={
                        "model_class": spec["model_class"],
                        "p_per_po": p_per_po,
                        "period_ms": period_ms,
                        "po_index": cycle,
                        "legacy_convention": "event placed at the start of its PO cycle",
                        "arrival_quantization": ARRIVAL_QUANTIZATION,
                        "arrival_error_bound_ns": 0.0,
                    },
                )
            )
    return events


def build_group_membership(ue_ids: list[str], group_size: int) -> dict[str, list[str]]:
    """Map UE IDs to paging groups as an ID membership table only.

    W02 does not implement group triggering or group-level paging
    decisions; W06/W08 consume this table.  Every UE appears exactly
    once and groups are filled in the given order.
    """

    if isinstance(group_size, bool) or not isinstance(group_size, int) or group_size < 1:
        raise TrafficProfileError("group_size must be a positive integer")
    if not isinstance(ue_ids, list) or not ue_ids:
        raise TrafficProfileError("ue_ids must be a non-empty list")
    seen: set[str] = set()
    membership: dict[str, list[str]] = {}
    for index, ue_id in enumerate(ue_ids):
        if not isinstance(ue_id, str) or not ue_id or any(character.isspace() for character in ue_id):
            raise TrafficProfileError("ue_ids must be non-empty strings without whitespace")
        if ue_id in seen:
            raise TrafficProfileError(f"duplicate ue_id {ue_id!r}")
        seen.add(ue_id)
        membership.setdefault(f"g{index // group_size:04d}", []).append(ue_id)
    return membership


def bernoulli_equivalent_lambda_per_s(p_per_po: float, period_ms: float) -> float:
    """Explicit mapping ``lambda = -ln(1-p)/T`` for p expressed per PO.

    Recorded for controlled comparison only: a Bernoulli-per-PO process is
    not a Poisson process, so the two profiles remain separate and their
    parameters are never converted implicitly.
    """

    p = _probability(p_per_po, "p_per_po")
    period_s = _positive_number(period_ms, "period_ms") / 1000.0
    if p == 0.0:
        return 0.0
    return -math.log(1.0 - p) / period_s


def _coerce_profile(profile) -> dict:
    if isinstance(profile, dict):
        return validate_traffic_profile(profile)
    if isinstance(profile, (str, Path)):
        return load_traffic_profile(profile)
    raise TrafficProfileError("profile must be a traffic profile dict, ID, or path")


def _packet_record(spec, *, index, arrival_ns, size_bits, deadline_ns, frame_id, metadata) -> dict:
    return {
        "packet_id": f"{spec['ue_id']}.{spec['traffic_id']}.p{index:08d}",
        "ue_id": spec["ue_id"],
        "flow_id": spec.get("flow_id") or f"{spec['traffic_id']}.{spec['ue_id']}.dl",
        "arrival_ns": arrival_ns,
        "size_bits": size_bits,
        "deadline_ns": deadline_ns,
        "traffic_profile_id": spec["traffic_id"],
        "frame_id": frame_id,
        "metadata": metadata,
    }


def _paging_event_record(spec, *, index, time_ns, metadata) -> dict:
    return {
        "event_id": f"{spec['ue_id']}.{spec['traffic_id']}.e{index:08d}",
        "time_ns": time_ns,
        "kind": "PAGING_ARRIVAL",
        "ue_id": spec["ue_id"],
        "payload": {"arrival_index": index},
        "source_id": spec["traffic_id"],
        "metadata": metadata,
    }


def _layer_metadata(spec, size_bits: int) -> dict:
    ip_mtu_bytes = int(spec["layer_assumptions"]["ip_mtu_bytes"]["value"])
    tb_max_bits = int(spec["layer_assumptions"]["tb_max_bits"]["value"])
    summary = layering_summary(size_bits, ip_mtu_bytes, tb_max_bits)
    return {
        "ip_mtu_bytes": summary["ip_mtu_bytes"],
        "tb_max_bits": summary["tb_max_bits"],
        "ip_packet_count": summary["ip_packet_count"],
        "tb_count": summary["tb_count"],
        "ip_payload_bits_sum": summary["ip_payload_bits_sum"],
        "tb_payload_bits_sum": summary["tb_payload_bits_sum"],
    }


def _check_xr_consistency(
    rate_mbps,
    fps,
    mean_bytes,
    std_ratio,
    std_bytes,
    min_ratio,
    min_bytes,
    max_ratio,
    max_bytes,
) -> None:
    expected_mean = rate_mbps * 1e6 / fps / 8.0
    if not math.isclose(mean_bytes, expected_mean, rel_tol=1e-9, abs_tol=1e-9):
        raise TrafficProfileError(
            f"frame_size_mean_bytes={mean_bytes} is inconsistent with R*1e6/F/8={expected_mean}"
        )
    for name, ratio, value in (
        ("frame_size_std_bytes", std_ratio, std_bytes),
        ("frame_size_min_bytes", min_ratio, min_bytes),
        ("frame_size_max_bytes", max_ratio, max_bytes),
    ):
        expected = ratio * mean_bytes
        if not math.isclose(value, expected, rel_tol=1e-6, abs_tol=1e-6):
            raise TrafficProfileError(f"{name}={value} is inconsistent with ratio*mean={expected}")
    if not min_bytes < max_bytes:
        raise TrafficProfileError("frame size truncation range must satisfy min < max")
    if not min_bytes <= mean_bytes <= max_bytes:
        raise TrafficProfileError("pre-truncation mean must lie inside the frame size truncation range")


def _check_end(end_ns: int) -> None:
    if isinstance(end_ns, bool) or not isinstance(end_ns, int) or end_ns < 0:
        raise TrafficProfileError("end_ns must be a non-negative integer nanosecond bound")


def _check_seed(seed: int) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise TrafficProfileError("seed must be a non-negative integer")


def _quantize(value_ns: float) -> int:
    return int(math.floor(value_ns + 0.5))


def _round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def _bytes_to_bits(size_bytes: float) -> int:
    return _round_half_up(size_bytes * 8.0)


def _ms_to_ns(value_ms: float) -> int:
    return _round_half_up(value_ms * 1e6)


def _finite_number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise TrafficProfileError(f"{name} must be a finite number")
    return float(value)


def _positive_number(value, name: str) -> float:
    value = _finite_number(value, name)
    if value <= 0.0:
        raise TrafficProfileError(f"{name} must be positive")
    return value


def _nonnegative_number(value, name: str) -> float:
    value = _finite_number(value, name)
    if value < 0.0:
        raise TrafficProfileError(f"{name} must be non-negative")
    return value


def _probability(value, name: str) -> float:
    value = _finite_number(value, name)
    if not 0.0 <= value <= 1.0:
        raise TrafficProfileError(f"{name} must be a probability in [0, 1]")
    return value


__all__ = [
    "ARRIVAL_ERROR_BOUND_NS",
    "ARRIVAL_QUANTIZATION",
    "bernoulli_equivalent_lambda_per_s",
    "build_group_membership",
    "generate_paging_bernoulli",
    "generate_paging_events",
    "generate_paging_poisson",
    "generate_poisson_fixed_size",
    "generate_traffic",
    "generate_traffic_multi_ue",
    "generate_voip_proxy",
    "generate_xr_single_stream",
]
