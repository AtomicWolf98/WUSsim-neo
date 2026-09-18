"""Frame -> IP packet -> wireless TB layering with exact bit conservation.

TR 38.838 section 5.1.1 states that in the single-stream XR model "a
packet models the set of IP packets belong to the same video frame"
(B440/B441); the model does not define an IP packet size, an MTU, or a
transport-block size.  W02 therefore keeps the two lower layers as
explicit engineering assumptions registered in every traffic profile:

* ``ip_mtu_bytes``  - IP packet payload cap used to split a frame;
* ``tb_max_bits``   - transport-block payload cap used to split IP
  packets (the reviewed calibration value is 868584 information bits per
  10-symbol PDSCH slot, TR 38.840 section 8, B976).

No layer performs scheduling, resource allocation, or link adaptation.
The helpers are pure payload splitters: every layer conserves the payload
bits of the layer above, and the last chunk carries the remainder.
Headers and padding are explicitly *not* modelled and must not be inferred
from these lists.
"""

from __future__ import annotations


def split_payload(payload_bits: int, max_chunk_bits: int) -> list[int]:
    """Split ``payload_bits`` into chunks of at most ``max_chunk_bits``.

    The chunk sizes are positive integers summing exactly to
    ``payload_bits``; the final chunk carries the remainder and is never
    padded up to the cap.  A zero payload yields an empty list (there is
    no legitimate zero-bit chunk to send).
    """

    _check_nonnegative_int(payload_bits, "payload_bits")
    _check_positive_int(max_chunk_bits, "max_chunk_bits")
    if payload_bits == 0:
        return []
    full, remainder = divmod(payload_bits, max_chunk_bits)
    chunks = [max_chunk_bits] * full
    if remainder:
        chunks.append(remainder)
    return chunks


def frame_to_ip_packets(frame_size_bits: int, ip_mtu_bytes: int) -> list[int]:
    """Return IP packet payload sizes (bits) for one frame payload."""

    _check_positive_int(ip_mtu_bytes, "ip_mtu_bytes")
    return split_payload(frame_size_bits, ip_mtu_bytes * 8)


def ip_packets_to_tb(packet_sizes_bits: list[int], tb_max_bits: int) -> list[list[int]]:
    """Return one TB-size list per IP packet (no cross-packet packing)."""

    if not isinstance(packet_sizes_bits, list):
        raise ValueError("packet_sizes_bits must be a list of non-negative integers")
    return [split_payload(size, tb_max_bits) for size in packet_sizes_bits]


def layering_summary(payload_bits: int, ip_mtu_bytes: int, tb_max_bits: int) -> dict:
    """Return counts only; full chunk lists stay available via the helpers.

    Packet records embed this summary because a large frame can map to
    hundreds of IP packets.  The summary is still sufficient to verify
    conservation when combined with the payload size.
    """

    ip_packets = frame_to_ip_packets(payload_bits, ip_mtu_bytes)
    tb_lists = ip_packets_to_tb(ip_packets, tb_max_bits)
    tb_sizes = [size for group in tb_lists for size in group]
    return {
        "payload_bits": payload_bits,
        "ip_mtu_bytes": ip_mtu_bytes,
        "tb_max_bits": tb_max_bits,
        "ip_packet_count": len(ip_packets),
        "tb_count": len(tb_sizes),
        "ip_payload_bits_sum": int(sum(ip_packets)),
        "tb_payload_bits_sum": int(sum(tb_sizes)),
    }


def _check_nonnegative_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _check_positive_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


__all__ = [
    "frame_to_ip_packets",
    "ip_packets_to_tb",
    "layering_summary",
    "split_payload",
]
