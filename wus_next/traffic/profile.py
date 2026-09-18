"""Evidence-aware traffic profile registry for W02.

Every traffic parameter is a contract-style leaf
(``value/unit/source_id/locator/evidence_status/interpretation/valid_domain``)
so that a reader can trace each number to an acquired document, exactly as
W00 does for the shared profiles.  The registry is deliberately separate
from ``wus_next/profiles`` because W00 owns that directory; W09 can merge
or reference these profiles during integration.

Evidence rules enforced here:

* ``formal_eligible`` may only be ``true`` for ``NORMATIVE`` or
  ``MEETING_AGREEMENT`` parameters with no blocked source.  None of the
  W02 profiles qualifies today, so no W02 business model is claimed as a
  formal 3GPP conformance model.
* The formal AMR VoIP entry is listed but blocked: generation raises
  :class:`BlockedEvidenceError` until R1-070674 is acquired.
* Proxy models keep ``evidence_status="PROXY"`` and
  ``formal_eligible=false`` and must not be presented as formal.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from wus_next.contracts import EVIDENCE_STATUSES

TRAFFIC_SCHEMA_VERSION = "1.0"
PROFILE_DIR = Path(__file__).resolve().parent / "profiles"

MODEL_CLASSES = frozenset(
    {
        "poisson_fixed_size",
        "xr_single_stream",
        "voip_proxy_legacy",
        "voip_amr_formal",
        "paging_poisson_process",
        "paging_bernoulli_cycles",
    }
)
PACKET_MODEL_CLASSES = frozenset({"poisson_fixed_size", "xr_single_stream", "voip_proxy_legacy"})
PAGING_MODEL_CLASSES = frozenset({"paging_poisson_process", "paging_bernoulli_cycles"})
FORMAL_EVIDENCE_STATUSES = frozenset({"NORMATIVE", "MEETING_AGREEMENT"})

BLOCKED_SOURCES = {
    "MISSING-R1-070674": (
        "R1-070674 itself was not acquired; official URLs returned 526/403 and no repository copy "
        "exists in this project (SOURCE_INDEX_ZH.md section 4).  Formal AMR VoIP stays disabled."
    ),
    "R1-2603452": "moderator summary not acquired (526); not used as a formal source.",
}
MODEL_REQUIRED_SOURCES = {
    "voip_amr_formal": ("MISSING-R1-070674",),
}

_REQUIRED_PROFILE_FIELDS = {
    "traffic_schema_version",
    "traffic_id",
    "profile_version",
    "model_class",
    "ue_id",
    "evidence_status",
    "formal_eligible",
    "role",
    "parameters",
    "layer_assumptions",
    "sources",
}
_ALLOWED_PROFILE_FIELDS = _REQUIRED_PROFILE_FIELDS | {"flow_id", "blocked_reasons", "metadata"}
_LEAF_FIELDS = {"value", "unit", "source_id", "locator", "evidence_status", "interpretation", "valid_domain"}

_MODEL_REQUIRED_PARAMETERS = {
    "poisson_fixed_size": ("packet_size", "mean_interarrival", "pdb_ms", "arrival_quantization"),
    "xr_single_stream": (
        "data_rate_mbps",
        "frame_rate_fps",
        "frame_size_mean_bytes",
        "frame_size_std_ratio",
        "frame_size_std_bytes",
        "frame_size_min_ratio",
        "frame_size_min_bytes",
        "frame_size_max_ratio",
        "frame_size_max_bytes",
        "jitter_mean_ms",
        "jitter_std_ms",
        "jitter_range_ms",
        "pdb_ms",
        "success_rate_requirement",
        "arrival_offset_ms",
        "arrival_quantization",
    ),
    "voip_proxy_legacy": ("packet_size_bytes", "period_ms", "pdb_ms", "arrival_quantization"),
    "voip_amr_formal": (),
    "paging_poisson_process": ("lambda_per_s", "po_period_ms", "arrival_quantization"),
    "paging_bernoulli_cycles": ("period_ms", "p_per_po", "arrival_quantization"),
}
_MODEL_REQUIRED_LAYERS = {
    "poisson_fixed_size": ("ip_mtu_bytes", "tb_max_bits"),
    "xr_single_stream": ("ip_mtu_bytes", "tb_max_bits"),
    "voip_proxy_legacy": ("ip_mtu_bytes", "tb_max_bits"),
}


class TrafficProfileError(ValueError):
    """Raised for malformed or unsupported traffic profiles."""


class BlockedEvidenceError(TrafficProfileError):
    """Raised when a model needs a source that has not been acquired."""


def validate_traffic_profile(profile: dict, requested_mode: str | None = None) -> dict:
    """Validate one traffic profile and optional formal-mode request."""

    if not isinstance(profile, dict):
        raise TrafficProfileError("traffic profile must be a JSON object")
    unknown = sorted(set(profile) - _ALLOWED_PROFILE_FIELDS)
    if unknown:
        raise TrafficProfileError(f"unknown top-level field(s): {', '.join(unknown)}; use metadata for extensions")
    missing = sorted(_REQUIRED_PROFILE_FIELDS - set(profile))
    if missing:
        raise TrafficProfileError(f"missing required field(s): {', '.join(missing)}")
    if profile["traffic_schema_version"] != TRAFFIC_SCHEMA_VERSION:
        raise TrafficProfileError(f"traffic_schema_version must be {TRAFFIC_SCHEMA_VERSION}")
    _identifier(profile["traffic_id"], "traffic_id")
    _identifier(profile["ue_id"], "ue_id")
    _identifier(profile["profile_version"], "profile_version")
    _identifier(profile["role"], "role")
    if profile["model_class"] not in MODEL_CLASSES:
        raise TrafficProfileError(f"unknown model_class {profile['model_class']!r}")
    if profile["evidence_status"] not in EVIDENCE_STATUSES:
        raise TrafficProfileError(f"unknown evidence_status {profile['evidence_status']!r}")
    if not isinstance(profile["formal_eligible"], bool):
        raise TrafficProfileError("formal_eligible must be a boolean")
    if not isinstance(profile["sources"], list) or not profile["sources"]:
        raise TrafficProfileError("sources must list at least one source ID")
    for source_id in profile["sources"]:
        _identifier(source_id, "sources[]")
    if "blocked_reasons" in profile and not isinstance(profile["blocked_reasons"], list):
        raise TrafficProfileError("blocked_reasons must be a list")
    if "metadata" in profile and not isinstance(profile["metadata"], dict):
        raise TrafficProfileError("metadata must be an object")
    if "flow_id" in profile:
        _identifier(profile["flow_id"], "flow_id")

    _walk_parameter_tree(profile["parameters"], "parameters")
    _walk_parameter_tree(profile["layer_assumptions"], "layer_assumptions")

    for name in _MODEL_REQUIRED_PARAMETERS[profile["model_class"]]:
        if name not in profile["parameters"] or not _is_leaf(profile["parameters"][name]):
            raise TrafficProfileError(f"model {profile['model_class']!r} requires parameter leaf {name!r}")
    for name in _MODEL_REQUIRED_LAYERS.get(profile["model_class"], ()):
        if name not in profile["layer_assumptions"] or not _is_leaf(profile["layer_assumptions"][name]):
            raise TrafficProfileError(f"model {profile['model_class']!r} requires layer leaf {name!r}")

    if profile["formal_eligible"]:
        if profile["evidence_status"] not in FORMAL_EVIDENCE_STATUSES:
            raise TrafficProfileError(
                "formal_eligible=true requires NORMATIVE or MEETING_AGREEMENT evidence, "
                f"got {profile['evidence_status']}"
            )
        _assert_model_sources_available(profile)
    if profile["evidence_status"] == "PROXY" and profile["formal_eligible"]:
        raise TrafficProfileError("a PROXY traffic model must not be marked formal_eligible")

    if requested_mode == "formal":
        _assert_model_sources_available(profile)
        if not profile["formal_eligible"]:
            raise TrafficProfileError(
                f"formal mode rejected for {profile['traffic_id']}: {profile['evidence_status']} evidence, "
                "formal_eligible=false"
            )
    return profile


def check_generation_allowed(profile: dict) -> None:
    """Raise if the model's mandatory sources are not acquired.

    This is the gate that keeps ``formal_amr`` (and any future blocked
    model) listed but unusable until the original document is acquired.
    """

    validate_traffic_profile(profile)
    _assert_model_sources_available(profile)


def require_acquired_source(source_id: str) -> None:
    """Raise if *source_id* is a registered blocked source."""

    if source_id in BLOCKED_SOURCES:
        raise BlockedEvidenceError(f"source {source_id} is BLOCKED_EVIDENCE: {BLOCKED_SOURCES[source_id]}")


def load_traffic_profile(ref: str | Path, ue_id: str | None = None, requested_mode: str | None = None) -> dict:
    """Load a registered profile by ID or path, optionally re-bind the UE."""

    if not isinstance(ref, (str, Path)):
        raise TrafficProfileError("profile reference must be an ID or a path")
    path = Path(ref)
    if not path.exists():
        path = PROFILE_DIR / f"{ref}.json"
    if not path.exists():
        raise TrafficProfileError(f"traffic profile not found: {ref}")
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrafficProfileError(f"cannot load traffic profile {path}: {exc}") from exc
    validate_traffic_profile(profile, requested_mode=requested_mode)
    if ue_id is not None:
        _identifier(ue_id, "ue_id")
        profile = with_ue(profile, ue_id)
    return profile


def list_registered_profiles() -> list[str]:
    """Return the sorted traffic IDs of the bundled profile files."""

    ids = []
    for path in sorted(PROFILE_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        ids.append(payload["traffic_id"])
    return ids


def with_ue(profile: dict, ue_id: str) -> dict:
    """Return a validated copy of *profile* bound to another UE ID."""

    _identifier(ue_id, "ue_id")
    clone = copy.deepcopy(profile)
    clone["ue_id"] = ue_id
    return validate_traffic_profile(clone)


def with_parameter(
    profile: dict,
    path: str,
    value,
    *,
    evidence_status: str | None = None,
    unit: str | None | object = ...,
) -> dict:
    """Return a validated copy with one parameter leaf value replaced.

    ``source_id``, ``locator`` and ``interpretation`` are preserved because
    they describe the same parameter; callers that need a different source
    must register a dedicated profile file instead.  This helper exists for
    documented sensitivity copies and tests, not for silently fitting
    numbers.
    """

    clone = copy.deepcopy(profile)
    target = clone
    parts = path.split(".")
    for part in parts[:-1]:
        if part not in target or not isinstance(target[part], dict):
            raise TrafficProfileError(f"parameter path not found: {path}")
        target = target[part]
    leaf = target.get(parts[-1])
    if not _is_leaf(leaf):
        raise TrafficProfileError(f"parameter path is not a leaf: {path}")
    leaf["value"] = value
    if evidence_status is not None:
        if evidence_status not in EVIDENCE_STATUSES:
            raise TrafficProfileError(f"unknown evidence_status {evidence_status!r}")
        leaf["evidence_status"] = evidence_status
    if unit is not ...:
        leaf["unit"] = unit
    return validate_traffic_profile(clone)


def traffic_profile_from_case(case_profile: dict, traffic_id: str, ue_id: str | None = None) -> dict:
    """Build a registered base profile bound to a W00 case-profile traffic group.

    The bridge only copies *numeric* traffic leaves that W00 already
    registered (packet size and mean inter-arrival time) and keeps the
    registered base profile's evidence for any leaf the case profile does
    not provide.  It never invents parameters and never promotes the
    evidence status of the W00 source.
    """

    if traffic_id in ("FTP3", "IM"):
        source_id = "FTP3" if traffic_id == "FTP3" else "IM"
        base = load_traffic_profile(source_id)
        group = (case_profile.get("parameters", {}) or {}).get("traffic", {}).get(source_id, {})
        if "packet_size" in group:
            _copy_value(base, "parameters.packet_size", group["packet_size"])
        for key in ("iat", "iat_ms"):
            if key in group:
                _copy_value(base, "parameters.mean_interarrival", group[key])
                break
        if ue_id is not None:
            base = with_ue(base, ue_id)
        return validate_traffic_profile(base)
    if traffic_id == "VoIP":
        base = load_traffic_profile("voip_proxy_v1")
        if ue_id is not None:
            base = with_ue(base, ue_id)
        return validate_traffic_profile(base)
    raise TrafficProfileError(
        f"no registered W02 traffic model matches {traffic_id!r}; XR profiles are registered directly"
    )


def _copy_value(target_profile: dict, path: str, leaf: dict) -> None:
    if not _is_leaf(leaf):
        raise TrafficProfileError(f"case profile leaf {path} lacks source metadata")
    base_leaf = target_profile
    parts = path.split(".")
    for part in parts:
        base_leaf = base_leaf[part]
    base_leaf["value"] = leaf["value"]
    base_leaf["source_id"] = leaf["source_id"]
    base_leaf["locator"] = leaf["locator"]
    base_leaf["evidence_status"] = leaf["evidence_status"]
    base_leaf["interpretation"] = leaf["interpretation"]
    base_leaf["valid_domain"] = leaf["valid_domain"]


def _assert_model_sources_available(profile: dict) -> None:
    for source_id in MODEL_REQUIRED_SOURCES.get(profile["model_class"], ()):
        require_acquired_source(source_id)


def _walk_parameter_tree(value, path: str) -> None:
    if _is_leaf(value):
        if value["evidence_status"] not in EVIDENCE_STATUSES:
            raise TrafficProfileError(f"{path}.evidence_status: unknown status {value['evidence_status']!r}")
        if value["unit"] is not None and (not isinstance(value["unit"], str) or not value["unit"]):
            raise TrafficProfileError(f"{path}.unit must be a non-empty string or null")
        if not isinstance(value["valid_domain"], (dict, list, str)):
            raise TrafficProfileError(f"{path}.valid_domain must be JSON-compatible")
        return
    if not isinstance(value, dict):
        raise TrafficProfileError(f"{path} must be an object of parameter leaves")
    for key, child in value.items():
        _identifier(key, path)
        _walk_parameter_tree(child, f"{path}.{key}")


def _is_leaf(value) -> bool:
    return isinstance(value, dict) and set(value) == _LEAF_FIELDS


def _identifier(value, path: str) -> None:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise TrafficProfileError(f"{path} must be a non-empty identifier without whitespace")


__all__ = [
    "BLOCKED_SOURCES",
    "BlockedEvidenceError",
    "MODEL_CLASSES",
    "PACKET_MODEL_CLASSES",
    "PAGING_MODEL_CLASSES",
    "PROFILE_DIR",
    "TRAFFIC_SCHEMA_VERSION",
    "TrafficProfileError",
    "list_registered_profiles",
    "load_traffic_profile",
    "require_acquired_source",
    "traffic_profile_from_case",
    "validate_traffic_profile",
    "with_parameter",
    "with_ue",
]
