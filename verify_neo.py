"""Fast structural and result verification for the clean neo project."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from wus_next.contracts import load_profile
from wus_next.metrics import verify_run


ROOT = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_theory_audit() -> dict[str, object]:
    result = ROOT / "results" / "theory_audit"
    if not result.exists():
        return {"status": "NOT_RUN"}

    required = {
        "manifest.json",
        "nominal_seed_metrics.csv",
        "nominal_summary.csv",
        "sensitivity_seed_metrics.csv",
        "sensitivity_summary.csv",
        "SHA256SUMS.txt",
    }
    missing = sorted(name for name in required if not (result / name).is_file())
    if missing:
        return {"status": "FAIL", "missing": missing}

    manifest = json.loads((result / "manifest.json").read_text(encoding="utf-8"))
    with (result / "nominal_seed_metrics.csv").open(encoding="utf-8-sig", newline="") as handle:
        nominal_seed_rows = list(csv.DictReader(handle))
    with (result / "nominal_summary.csv").open(encoding="utf-8-sig", newline="") as handle:
        nominal_summary_rows = list(csv.DictReader(handle))
    with (result / "sensitivity_seed_metrics.csv").open(encoding="utf-8-sig", newline="") as handle:
        sensitivity_seed_rows = list(csv.DictReader(handle))
    with (result / "sensitivity_summary.csv").open(encoding="utf-8-sig", newline="") as handle:
        sensitivity_summary_rows = list(csv.DictReader(handle))

    nominal_expected = (
        int(manifest["nominal_seed_count"])
        * len(manifest["traffic_ids"])
        * len(manifest["nominal_cases"])
    )
    sensitivity_expected = (
        int(manifest["sensitivity_seed_count"])
        * len(manifest["traffic_ids"])
        * len(manifest["sensitivity_cases"])
        * len(manifest["scenarios"])
    )
    expected_run_count = nominal_expected + sensitivity_expected
    row_counts_ok = all(
        (
            len(nominal_seed_rows) == nominal_expected,
            len(nominal_summary_rows) == len(manifest["traffic_ids"]) * len(manifest["nominal_cases"]),
            len(sensitivity_seed_rows) == sensitivity_expected,
            len(sensitivity_summary_rows)
            == len(manifest["traffic_ids"]) * len(manifest["sensitivity_cases"]) * len(manifest["scenarios"]),
            int(manifest["run_count"]) == expected_run_count,
            int(manifest["expected_run_count"]) == expected_run_count,
        )
    )
    config_ok = sha256(ROOT / manifest["evaluation_config"]) == manifest["evaluation_config_sha256"]
    profile_ok = sha256(ROOT / manifest["profile"]) == manifest["profile_sha256"]
    source_hashes_ok = all(
        (ROOT / relative).is_file() and sha256(ROOT / relative) == expected
        for relative, expected in manifest["source_sha256"].items()
    )
    checksum_ok = True
    for line in (result / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        expected, name = line.split(maxsplit=1)
        checksum_ok &= (result / name).is_file() and sha256(result / name) == expected

    ok = all(
        (
            manifest.get("status") == "PASS",
            row_counts_ok,
            config_ok,
            profile_ok,
            source_hashes_ok,
            checksum_ok,
        )
    )
    return {
        "status": "PASS" if ok else "FAIL",
        "run_count": manifest["run_count"],
        "nominal_seed_rows": len(nominal_seed_rows),
        "sensitivity_seed_rows": len(sensitivity_seed_rows),
        "row_counts_ok": row_counts_ok,
        "evaluation_config_hash_ok": config_ok,
        "profile_hash_ok": profile_ok,
        "source_hashes_match_manifest": source_hashes_ok,
        "result_checksums_ok": checksum_ok,
    }


def verify_gain_decomposition() -> dict[str, object]:
    result = ROOT / "results" / "gain_decomposition"
    if not result.exists():
        return {"status": "NOT_RUN"}
    required = {
        "counterfactual_seed_metrics.csv",
        "gain_decomposition.csv",
        "energy_component_breakdown_8seed.csv",
        "manifest.json",
        "SHA256SUMS.txt",
    }
    missing = sorted(name for name in required if not (result / name).is_file())
    if missing:
        return {"status": "FAIL", "missing": missing}
    manifest = json.loads((result / "manifest.json").read_text(encoding="utf-8"))
    with (result / "counterfactual_seed_metrics.csv").open(encoding="utf-8-sig", newline="") as handle:
        seed_rows = list(csv.DictReader(handle))
    with (result / "gain_decomposition.csv").open(encoding="utf-8-sig", newline="") as handle:
        decomposition = list(csv.DictReader(handle))
    with (result / "energy_component_breakdown_8seed.csv").open(encoding="utf-8-sig", newline="") as handle:
        components = list(csv.DictReader(handle))
    checksum_ok = True
    for line in (result / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        expected, name = line.split(maxsplit=1)
        checksum_ok &= (result / name).is_file() and sha256(result / name) == expected
    source_ok = all(
        (ROOT / relative).is_file() and sha256(ROOT / relative) == expected
        for relative, expected in manifest["source_sha256"].items()
    )
    input_ok = all(
        (
            sha256(ROOT / manifest["evaluation_config"]) == manifest["evaluation_config_sha256"],
            sha256(ROOT / manifest["nominal_seed_metrics"]) == manifest["nominal_seed_metrics_sha256"],
        )
    )
    seed_counts_ok = len(seed_rows) == 128 and all(
        len({int(row["seed"]) for row in seed_rows if row["traffic_id"] == traffic_id}) == 64
        for traffic_id in ("FTP3", "IM")
    )
    expected_steps = {
        "independent_wus_trigger_at_100ms_tail",
        "timer_100ms_to_8ms",
        "total_case_1_1_to_1_5",
        "non_ee_to_ee_measurement",
        "total_case_1_1_to_1_6",
    }
    rows_by_key = {(row["traffic_id"], row["step"]): row for row in decomposition}
    waterfall_ok = len(decomposition) == 10 and all(
        (traffic_id, step) in rows_by_key
        for traffic_id in ("FTP3", "IM")
        for step in expected_steps
    )
    if waterfall_ok:
        for traffic_id in ("FTP3", "IM"):
            value = lambda step: float(rows_by_key[(traffic_id, step)]["baseline_normalized_contribution_pp"])
            waterfall_ok &= abs(value("independent_wus_trigger_at_100ms_tail") + value("timer_100ms_to_8ms") - value("total_case_1_1_to_1_5")) < 1e-9
            waterfall_ok &= abs(value("total_case_1_1_to_1_5") + value("non_ee_to_ee_measurement") - value("total_case_1_1_to_1_6")) < 1e-9
    components_ok = len(components) == 6 and all(int(row["seed_count"]) == 8 for row in components)
    ok = all(
        (
            manifest.get("status") == "PASS",
            int(manifest.get("counterfactual_run_count", -1)) == 128,
            seed_counts_ok,
            waterfall_ok,
            components_ok,
            source_ok,
            input_ok,
            checksum_ok,
        )
    )
    return {
        "status": "PASS" if ok else "FAIL",
        "counterfactual_seed_rows": len(seed_rows),
        "decomposition_rows": len(decomposition),
        "component_rows": len(components),
        "seed_counts_ok": seed_counts_ok,
        "waterfall_closure_ok": waterfall_ok,
        "source_hashes_match_manifest": source_ok,
        "input_hashes_match_manifest": input_ok,
        "result_checksums_ok": checksum_ok,
    }


def main() -> int:
    required = [
        ROOT / "README.md",
        ROOT / "README_EN.md",
        ROOT / "requirements.txt",
        ROOT / "setup.bat",
        ROOT / "run_cases_1_to_6.bat",
        ROOT / "doc" / "00_DOCUMENT_INDEX_ZH.md",
        ROOT / "doc" / "03_SYSTEM_SIMULATION_MODEL_ZH.md",
        ROOT / "doc" / "10_RESULTS_GAIN_DECOMPOSITION_ZH.md",
        ROOT / "configs" / "case1_company_eval_v1.json",
        ROOT / "wus_next" / "profiles" / "sixg_case1_v1.json",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print(json.dumps({"status": "FAIL", "missing": missing}, ensure_ascii=False, indent=2))
        return 2
    load_profile(ROOT / "wus_next" / "profiles" / "sixg_case1_v1.json")

    result = ROOT / "results" / "case1_to_6"
    if not result.exists():
        print(json.dumps({"status": "PASS", "result_status": "NOT_RUN"}, ensure_ascii=False, indent=2))
        return 0
    runs = []
    for path in sorted((result / "raw").glob("*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        check = verify_run(run, summary=run["summary"], window=run["metadata"]["effective_window"])
        if check["status"] != "PASS":
            print(json.dumps({"status": "FAIL", "run": run["run_id"], "check": check}, ensure_ascii=False, indent=2))
            return 3
        runs.append(run)
    with (result / "summary.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    manifest = json.loads((result / "manifest.json").read_text(encoding="utf-8"))
    config_path = ROOT / manifest["evaluation_config"]
    config_hash = sha256(config_path)
    config_ok = config_hash == manifest["evaluation_config_sha256"]
    options_ok = all(run["metadata"]["options"].get("label") == "case1_company_eval_v1" for run in runs)
    source_hashes = manifest["profile_validation"].get("source_hashes", {})
    source_hashes_ok = all(
        (ROOT / relative).exists()
        and sha256(ROOT / relative) == expected
        for relative, expected in source_hashes.items()
    )
    measurement_once_ok = True
    tx_event_ok = True
    pdsch_duration_ok = True
    segmented_tx_contiguous_ok = True
    for run in runs:
        window = run["metadata"].get("simulation_window", run["metadata"]["effective_window"])
        cfg = run["metadata"]["policy_config"]
        cycle_ns = int(cfg["measurement_cycle_ns"])
        offset_ns = int(cfg.get("measurement_offset_ns", cfg.get("mo_offset_ns", 0))) % cycle_ns
        expected_measurements = sum(
            1
            for time_ns in range(offset_ns, int(window["end_ns"]), cycle_ns)
            if time_ns >= int(window["start_ns"])
        )
        measurement_actions = [
            action
            for action in run["actions"]
            if (action.get("payload") or {}).get("monitor_id")
            in {"MEASUREMENT_NON_EE", "MEASUREMENT_EE"}
        ]
        measurement_once_ok &= len(measurement_actions) == expected_measurements
        new_tx_events = [event for event in run["events"] if event["kind"] == "NEW_TRANSMISSION"]
        tx_ids = {str(tx["tx_id"]) for tx in run["tx"] if tx.get("success")}
        event_tx_ids = {
            str((event.get("payload") or {}).get("tx_id")) for event in new_tx_events
        }
        tx_event_ok &= len(new_tx_events) == len(tx_ids) and event_tx_ids == tx_ids
        tx_duration = sum(int(tx["end_ns"]) - int(tx["start_ns"]) for tx in run["tx"] if tx.get("success"))
        pdsch_duration = sum(
            int(interval["end_ns"]) - int(interval["start_ns"])
            for interval in run["intervals"]
            if interval["receiver_id"] == "MR" and interval["state"] == "PDSCH"
        )
        pdsch_duration_ok &= tx_duration == pdsch_duration
        packet_segments: dict[str, list[dict]] = {}
        for tx in run["tx"]:
            if tx.get("success"):
                packet_segments.setdefault(str(tx["packet_id"]), []).append(tx)
        for segments in packet_segments.values():
            segments.sort(key=lambda tx: (int(tx["start_ns"]), int(tx["end_ns"])))
            segmented_tx_contiguous_ok &= all(
                int(previous["end_ns"]) == int(current["start_ns"])
                for previous, current in zip(segments, segments[1:])
            )
    power = {(row["traffic_id"], row["case_id"]): float(row["mean_avg_power_unit"]) for row in rows}
    ordering_diagnostic = {
        traffic_id: sorted(
            ((case_id, power[(traffic_id, case_id)]) for case_id in ("1-1", "1-2", "1-3", "1-4", "1-5", "1-6")),
            key=lambda item: item[1],
        )
        for traffic_id in ("FTP3", "IM")
    }
    theory_audit = verify_theory_audit()
    gain_decomposition = verify_gain_decomposition()
    ok = all(
        (
            len(runs) == 96,
            len(rows) == 12,
            config_ok,
            options_ok,
            source_hashes_ok,
            measurement_once_ok,
            tx_event_ok,
            pdsch_duration_ok,
            segmented_tx_contiguous_ok,
            theory_audit["status"] in {"PASS", "NOT_RUN"},
            gain_decomposition["status"] in {"PASS", "NOT_RUN"},
        )
    )
    print(
        json.dumps(
            {
                "status": "PASS" if ok else "FAIL",
                "run_count": len(runs),
                "summary_rows": len(rows),
                "evaluation_config_hash_ok": config_ok,
                "raw_run_config_ok": options_ok,
                "source_hashes_match_manifest": source_hashes_ok,
                "one_measurement_action_per_cycle": measurement_once_ok,
                "new_transmission_event_per_tx": tx_event_ok,
                "pdsch_duration_matches_tx": pdsch_duration_ok,
                "segmented_packet_tx_is_contiguous": segmented_tx_contiguous_ok,
                "theory_audit": theory_audit,
                "gain_decomposition": gain_decomposition,
                "power_ordering_diagnostic_only": ordering_diagnostic,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
