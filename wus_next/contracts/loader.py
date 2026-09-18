"""Explicit file-backed loaders for the frozen contract boundary."""

from __future__ import annotations

from pathlib import Path

from .validator import load_json, load_profile, validate_record, validate_run_output


def load_record(kind: str, path: str | Path) -> dict:
    """Load one UTF-8 JSON record and validate it as *kind*."""

    return validate_record(kind, load_json(path))


def load_run_output(path: str | Path) -> dict:
    """Load one run-output envelope and validate all embedded records."""

    return validate_run_output(load_json(path))


__all__ = ["load_json", "load_profile", "load_record", "load_run_output"]

