# W00 evidence registries

- `case_registry.json` is the row-level transcription of Chair Notes B4787 and B4791. The three axes are separate `trigger`, `serving_measurement`, and `neighbor_measurement` fields.
- `parameter_source_map.json` records parameter value, unit, source ID, locator, evidence status, interpretation, and valid domain. `ACQUIRED_NOT_FULLY_REVIEWED` is a source acquisition state, not a parameter evidence state.
- `compatibility_map.json` freezes the selected specification releases and lists dependencies/open items. A `PARTIALLY_VERIFIED` or `UNRESOLVED` row must not be promoted to formal behavior by a consumer.
- `manifest.py` verifies SHA-256/byte-count manifests and fails closed on a changed or missing file.

