"""W02 traffic, paging, and calibrated reference input generation.

Public interface frozen by DEVELOPMENT_CONTRACT_ZH.md section 4::

    generate_traffic(profile: dict, seed: int, end_ns: int) -> list[dict]  # Packet

Paging arrivals are a separate Event-producing entry
(``generate_paging_events``) because the contract Event record, not the
Packet record, describes a paging occurrence.  Both entries return fixed
JSON-compatible lists; they never expose a live generator handle to a
future-facing consumer.

This package only generates arrivals and their evidence/status metadata.
It does not implement WUS policy, sleep decisions, resource scheduling, or
final KPIs.
"""

from .generators import (
    ARRIVAL_ERROR_BOUND_NS,
    ARRIVAL_QUANTIZATION,
    bernoulli_equivalent_lambda_per_s,
    build_group_membership,
    generate_paging_bernoulli,
    generate_paging_events,
    generate_paging_poisson,
    generate_poisson_fixed_size,
    generate_traffic,
    generate_traffic_multi_ue,
    generate_voip_proxy,
    generate_xr_single_stream,
)
from .layers import frame_to_ip_packets, ip_packets_to_tb, layering_summary, split_payload
from .profile import (
    BLOCKED_SOURCES,
    MODEL_CLASSES,
    PACKET_MODEL_CLASSES,
    PAGING_MODEL_CLASSES,
    PROFILE_DIR,
    TRAFFIC_SCHEMA_VERSION,
    BlockedEvidenceError,
    TrafficProfileError,
    check_generation_allowed,
    list_registered_profiles,
    load_traffic_profile,
    require_acquired_source,
    traffic_profile_from_case,
    validate_traffic_profile,
    with_parameter,
    with_ue,
)
from .streams import (
    CANONICAL_STREAM_NAMES,
    derive_stream_seed,
    stream_rng,
    traffic_sha256,
)
from .truncated_normal import (
    sample_truncated_normal,
    sample_truncated_normal_batch,
    truncated_normal_moments,
)

__all__ = [
    "ARRIVAL_ERROR_BOUND_NS",
    "ARRIVAL_QUANTIZATION",
    "BLOCKED_SOURCES",
    "BlockedEvidenceError",
    "CANONICAL_STREAM_NAMES",
    "MODEL_CLASSES",
    "PACKET_MODEL_CLASSES",
    "PAGING_MODEL_CLASSES",
    "PROFILE_DIR",
    "TRAFFIC_SCHEMA_VERSION",
    "TrafficProfileError",
    "bernoulli_equivalent_lambda_per_s",
    "build_group_membership",
    "check_generation_allowed",
    "derive_stream_seed",
    "frame_to_ip_packets",
    "generate_paging_bernoulli",
    "generate_paging_events",
    "generate_paging_poisson",
    "generate_poisson_fixed_size",
    "generate_traffic",
    "generate_traffic_multi_ue",
    "generate_voip_proxy",
    "generate_xr_single_stream",
    "ip_packets_to_tb",
    "layering_summary",
    "list_registered_profiles",
    "load_traffic_profile",
    "require_acquired_source",
    "sample_truncated_normal",
    "sample_truncated_normal_batch",
    "split_payload",
    "stream_rng",
    "traffic_profile_from_case",
    "traffic_sha256",
    "truncated_normal_moments",
    "validate_traffic_profile",
    "with_parameter",
    "with_ue",
]
