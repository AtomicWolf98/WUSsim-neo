"""Deterministic per-UE random stream derivation for W02 traffic models.

DEVELOPMENT_CONTRACT_ZH.md section 7 fixes the random split on
``(run_seed, ue_id, stream_name)`` with the canonical stream names
``traffic/channel/detection/other_wus`` and forbids Python's built-in
``hash``.  This module implements that split once so every consumer
(W02 traffic today, W01/W05/W08 later) derives the same stream for the
same inputs regardless of call order or the number of UEs in a run.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np

CANONICAL_STREAM_NAMES = ("traffic", "channel", "detection", "other_wus")
_SEED_NAMESPACE = "wus_next.contract_v1"
_SEED_MODULUS = 2**63


def derive_stream_seed(
    run_seed: int,
    ue_id: str,
    stream_name: str,
    *,
    namespace: str = _SEED_NAMESPACE,
) -> int:
    """Return a stable 63-bit seed for ``(run_seed, ue_id, stream_name)``.

    The derivation is a pure SHA-256 digest over a canonical text key, so
    it does not depend on dictionary order, call order, process state, or
    the number of other UEs.  Tests assert that changing any component or
    the call order changes / preserves the value as specified.
    """

    if isinstance(run_seed, bool) or not isinstance(run_seed, int) or run_seed < 0:
        raise ValueError("run_seed must be a non-negative integer")
    if not isinstance(ue_id, str) or not ue_id or any(character.isspace() for character in ue_id):
        raise ValueError("ue_id must be a non-empty string without whitespace")
    if not isinstance(stream_name, str) or not stream_name or any(character.isspace() for character in stream_name):
        raise ValueError("stream_name must be a non-empty string without whitespace")
    payload = f"{namespace}|{run_seed}|{ue_id}|{stream_name}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big") % _SEED_MODULUS


def stream_rng(run_seed: int, ue_id: str, stream_name: str) -> np.random.Generator:
    """Return the numpy generator bound to one deterministic stream."""

    return np.random.default_rng(np.random.SeedSequence(derive_stream_seed(run_seed, ue_id, stream_name)))


def traffic_sha256(packets: list[dict]) -> str:
    """Return the canonical SHA-256 of a generated Packet sequence.

    The digest covers the JSON-compatible records with sorted keys and no
    incidental formatting, so paired cases that share the same traffic
    inputs produce the same ``traffic_sha256`` required by the run
    envelope.
    """

    canonical = json.dumps(packets, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["CANONICAL_STREAM_NAMES", "derive_stream_seed", "stream_rng", "traffic_sha256"]
