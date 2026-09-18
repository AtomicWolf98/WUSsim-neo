"""Run the clean Case 1-1..1-6 paired experiment matrix.

The output directory is replaced as one unit so repeated executions do not
accumulate Round-1/Round-2 style result trees.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import time
from pathlib import Path

from wus_next.contracts import load_profile
from wus_next.evidence import get_case, load_case_registry
from wus_next.integration.runner import (
    BASELINE_CASE,
    DEFAULT_SEEDS,
    run_one,
    validate_integration_inputs,
)
from wus_next.metrics import compare_paired, detection_metrics, summarize_multi_run, verify_run
from wus_next.policy6g.strategies import COMPANY_R1_2603659


ROOT = Path(__file__).resolve().parent
PROFILE_PATH = ROOT / "wus_next" / "profiles" / "sixg_case1_v1.json"
EVAL_CONFIG_PATH = ROOT / "configs" / "case1_company_eval_v1.json"
DEFAULT_OUT = ROOT / "results" / "case1_to_6"
CASES = ("1-1", "1-2", "1-3", "1-4", "1-5", "1-6")
TRAFFIC = ("FTP3", "IM")


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def comparison_record(run: dict) -> dict:
    fields = dict(run["summary"]["compare_fields"])
    fields.update(
        {
            "seed": run["seed"],
            "case": run["metadata"]["case_id"],
            "traffic_sha256": run["traffic_sha256"],
            "start_ns": run["metadata"]["effective_window"]["start_ns"],
            "window_ns": (
                run["metadata"]["effective_window"]["end_ns"]
                - run["metadata"]["effective_window"]["start_ns"]
            ),
        }
    )
    return fields


def aggregate(group: list[dict]) -> dict:
    summaries = [run["summary"] for run in group]
    multi = summarize_multi_run(summaries)
    trials = [trial for run in group for trial in run["trials"]]
    detection = detection_metrics(trials)
    goodputs = [float(run["summary"]["goodput"]["window_goodput_mbps"]) for run in group]
    return {
        "run_count": len(group),
        "mean_avg_power_unit": multi["mean_avg_power_unit"],
        "energy_unit_ms_sum": multi["energy_unit_ms_sum"],
        "packets": multi["packets_aggregate"],
        "detection": detection,
        "mean_window_goodput_mbps": sum(goodputs) / len(goodputs),
        "run_ids": [run["run_id"] for run in group],
    }


def safe_replace(out: Path) -> None:
    resolved = out.resolve()
    results_root = (ROOT / "results").resolve()
    if resolved == results_root or results_root not in resolved.parents:
        raise ValueError(f"refusing to replace path outside {results_root}: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def evaluation_options(config: dict, case_id: str) -> dict:
    """Translate the frozen, source-labelled evaluation file into run options."""

    timer = config["timer_and_monitoring"]
    measurement = config["measurement"]
    phase = config["calendar_phase"]
    energy = config["energy"]
    service = config["service"]
    statistics_config = config["statistics"]
    options = {
        "label": config["config_id"],
        "measurement_cycle_ns": int(round(float(measurement["cycle_ms"]) * 1_000_000)),
        "measurement_duration_ns": int(round(float(measurement["duration_ms"]) * 1_000_000)),
        "cdrx_offset_ns": int(round(float(phase["cdrx_offset_ms"]) * 1_000_000)),
        "wus_offset_ns": int(round(float(phase["wus_offset_ms"]) * 1_000_000)),
        "measurement_offset_ns": int(round(float(phase["measurement_offset_ms"]) * 1_000_000)),
        "dcp_gap_ns": int(round(float(timer["coupled_gap_ms"]) * 1_000_000)),
        "dcp_rx_ns": int(round(float(timer["dcp_rx_duration_ms"]) * 1_000_000)),
        "coupled_monitor_count": int(timer["coupled_monitor_occasions"]),
        "coupled_monitor_spacing_ns": int(
            round(
                float(timer["coupled_monitor_spacing_slots"])
                * float(timer["slot_duration_ms"])
                * 1_000_000
            )
        ),
        "sleep_state_model": str(energy["sleep_state_model"]),
        "mr_idle_power_unit": float(energy["mr_idle_power_unit"]),
        "mr_micro_sleep_power_unit": float(energy["mr_micro_sleep_power_unit"]),
        "mr_light_sleep_power_unit": float(energy["mr_light_sleep_power_unit"]),
        "mr_deep_sleep_power_unit": float(energy["mr_deep_sleep_power_unit"]),
        "mr_light_transition_energy_unit_ms": float(energy["mr_light_transition_energy_unit_ms"]),
        "mr_light_transition_time_ns": int(round(float(energy["mr_light_transition_time_ms"]) * 1_000_000)),
        "mr_deep_transition_energy_unit_ms": float(energy["mr_deep_transition_energy_unit_ms"]),
        "mr_deep_transition_time_ns": int(round(float(energy["mr_deep_transition_time_ms"]) * 1_000_000)),
        "lr_idle_power_unit": float(energy["lr_idle_power_unit"]),
        "lr_wus_power_unit": float(energy["wus_monitor_power_unit"]),
        "lr_dcp_power_unit": float(energy["dcp_monitor_power_unit"]),
        "mr_pdcch_only_power_unit": float(energy["pdcch_only_power_unit"]),
        "mr_pdsch_power_unit": float(energy["pdcch_pdsch_power_unit"]),
        "mr_measurement_power_unit": float(measurement["non_ee_power_unit"]),
        "ee_measurement_power_unit": float(measurement["ee_power_unit"]),
        "ee_transition_energy_per_edge_unit_ms": float(measurement["ee_transition_energy_per_edge_unit_ms"]),
        "energy_evidence_status": str(energy["evidence_status"]),
        "service_rate_bits_per_ms": int(service["service_rate_bits_per_ms"]),
        "warmup_cdrx_cycles": int(statistics_config["warmup_cdrx_cycles"]),
        "drain_pdb_multiples": int(statistics_config["drain_pdb_multiples"]),
    }
    if case_id in {"1-3", "1-4"}:
        options["gap_ns"] = int(round(float(timer["coupled_gap_ms"]) * 1_000_000))
        options["wus_rx_ns"] = int(round(float(timer["wus_rx_duration_ms"]) * 1_000_000))
    if case_id in {"1-5", "1-6"}:
        options.update(
            {
                "wus_period_ns": int(round(float(timer["independent_wus_period_ms"]) * 1_000_000)),
                "timer_ns": int(round(float(timer["wus_timer_ms"]) * 1_000_000)),
                "gap_ns": int(round(float(timer["independent_wake_delay_ms"]) * 1_000_000)),
                "wus_rx_ns": int(round(float(timer["wus_rx_duration_ms"]) * 1_000_000)),
            }
        )
    return options


def run_matrix(out: Path, replace: bool) -> dict:
    if out.exists() and any(out.iterdir()):
        if not replace:
            raise FileExistsError(f"{out} is non-empty; pass --replace")
        safe_replace(out)
    else:
        out.mkdir(parents=True, exist_ok=True)

    raw_dir = out / "raw"
    raw_dir.mkdir()
    profile = load_profile(PROFILE_PATH)
    eval_config = json.loads(EVAL_CONFIG_PATH.read_text(encoding="utf-8"))
    validation = validate_integration_inputs(PROFILE_PATH)
    registry = load_case_registry()
    started = time.time()
    runs: list[dict] = []

    for traffic_id in TRAFFIC:
        for case_id in CASES:
            for seed in DEFAULT_SEEDS:
                run = run_one(
                    profile,
                    registry,
                    case_id=case_id,
                    traffic_id=traffic_id,
                    seed=seed,
                    policy_id=COMPANY_R1_2603659,
                    options=evaluation_options(eval_config, case_id),
                    root=ROOT,
                )
                path = raw_dir / f"{run['run_id']}.json"
                path.write_text(json.dumps(run, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                runs.append(run)
                print(f"[{len(runs):02d}/96] {traffic_id} {case_id} seed={seed} PASS")

    rows = []
    groups = {}
    for traffic_id in TRAFFIC:
        traffic_runs = [run for run in runs if run["metadata"]["traffic_id"] == traffic_id]
        for case_id in CASES:
            group = [run for run in traffic_runs if run["metadata"]["case_id"] == case_id]
            agg = aggregate(group)
            comparison = None
            if case_id != BASELINE_CASE:
                comparison = compare_paired(
                    [comparison_record(run) for run in traffic_runs],
                    BASELINE_CASE,
                    bootstrap_seed=20260917,
                    case_id=case_id,
                    n_boot=4000,
                )
            case = get_case(case_id, registry)
            packets = agg["packets"]
            bootstrap = (comparison or {}).get("bootstrap") or {}
            row = {
                "traffic_id": traffic_id,
                "case_id": case_id,
                "trigger": case["trigger"],
                "serving_measurement": case["serving_measurement"],
                "run_count": agg["run_count"],
                "mean_avg_power_unit": agg["mean_avg_power_unit"],
                "gain_vs_case_1_1": None if comparison is None else comparison.get("gain"),
                "gain_ci95_low": bootstrap.get("ci_low"),
                "gain_ci95_high": bootstrap.get("ci_high"),
                "arrived_count": packets["arrived_count"],
                "delivered_count": packets["delivered_count"],
                "completion_rate_pooled": packets["completion_rate_pooled"],
                "pdb_satisfied_count": packets["pdb_satisfied_count"],
                "pdb_eligible_count": packets["pdb_eligible_count"],
                "pdb_ratio_pooled": packets["pdb_ratio_pooled"],
                "delay_mean_ms_pooled": packets["delay_mean_ms_pooled"],
                "delay_p90_ms_pooled": packets["delay_p90_ms_pooled"],
                "mean_window_goodput_mbps": agg["mean_window_goodput_mbps"],
                "mdr": agg["detection"]["mdr"]["point"],
                "far": agg["detection"]["far"]["point"],
                "fdr": agg["detection"]["fdr"]["point"],
            }
            rows.append(row)
            groups[f"{traffic_id}/{case_id}"] = {
                "case_definition": case,
                "aggregate": agg,
                "comparison_to_case_1_1": comparison,
            }

    with (out / "summary.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    verification = []
    for run in runs:
        check = verify_run(run, summary=run["summary"], window=run["metadata"]["effective_window"])
        verification.append({"run_id": run["run_id"], "status": check["status"], "mismatches": check["mismatches"]})
    verify_status = "PASS" if all(item["status"] == "PASS" for item in verification) else "FAIL"
    (out / "verification.json").write_text(
        json.dumps({"status": verify_status, "run_count": len(runs), "runs": verification}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out / "case_summary.json").write_text(json.dumps(groups, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "project": "WUSsim neo",
        "matrix": "Case 1-1..1-6 / FTP3+IM / 8 paired seeds / company-aligned connected-mode evaluation v1",
        "status": verify_status,
        "profile": str(PROFILE_PATH.relative_to(ROOT).as_posix()),
        "evaluation_config": str(EVAL_CONFIG_PATH.relative_to(ROOT).as_posix()),
        "evaluation_config_sha256": sha256_file(EVAL_CONFIG_PATH),
        "evaluation_config_snapshot": eval_config,
        "profile_validation": validation,
        "case_ids": list(CASES),
        "traffic_ids": list(TRAFFIC),
        "seeds": list(DEFAULT_SEEDS),
        "run_count": len(runs),
        "elapsed_seconds": time.time() - started,
        "claim_scope": "single-UE abstract relative-energy/latency numerical discussion",
        "not_claimed": ["PHY-calibrated BLER/coverage", "3GPP conformance", "absolute battery life", "network energy saving"],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    hashes = []
    for path in sorted(item for item in out.rglob("*") if item.is_file() and item.name != "SHA256SUMS.txt"):
        hashes.append(f"{sha256_file(path)}  {path.relative_to(out).as_posix()}")
    (out / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    manifest = run_matrix(args.out.resolve(), args.replace)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
