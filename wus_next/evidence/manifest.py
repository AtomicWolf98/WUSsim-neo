"""Hash manifest verification for evidence and handoff artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(manifest_path: str | Path, root: str | Path | None = None) -> dict:
    """Verify every manifest entry and fail closed on missing/hash-changed files."""

    manifest_path = Path(manifest_path)
    root_path = Path(root) if root is not None else manifest_path.parent
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict) or manifest.get("manifest_version") != "1.0":
        raise ValueError("manifest_version 1.0 is required")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise ValueError("manifest.files must be a list")
    checked = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes"}:
            raise ValueError(f"manifest.files[{index}] has invalid shape")
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"manifest.files[{index}].path escapes root")
        target = root_path / relative
        if not target.is_file():
            raise ValueError(f"manifest.files[{index}].path missing: {relative}")
        actual_bytes = target.stat().st_size
        actual_hash = _sha256(target)
        if actual_bytes != entry["bytes"] or actual_hash.lower() != str(entry["sha256"]).lower():
            raise ValueError(f"manifest mismatch for {relative}")
        checked.append(str(relative))
    return {"valid": True, "checked": checked, "count": len(checked)}

