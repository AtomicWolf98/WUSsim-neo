"""Run convergence and uncertainty checks without retaining bulky raw traces.

The formal 8-seed matrix remains fully auditable under results/case1_to_6.
This companion run uses many independent seeds and stores only seed-level
metrics, paired comparisons and a manifest.  It is intended to detect unstable
headline gains and sensitivity to project assumptions.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import statistics
import time
from collections import defaultdict
from pathlib import Path

from run_cases_1_to_6 import CASES, EVAL_CONFIG_PATH, PROFILE_PATH, evaluation_options
from wus_next.contracts import load_profile
from wus_next.evidence import load_case_registry
from wus_next.integration.runner import run_one
from wus_next.metrics import compare_paired
from wus_next.policy6g.strategies import COMPANY_R1_2603659


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results" / "theory_audit"
TRAFFIC = ("FTP3", "IM")
NOMINAL_SEEDS = tuple(range(1001, 1065))
SENSITIVITY_SEEDS = tuple(range(2001, 2009))
FOCUS_CASES = ("1-1", "1-5", "1-6")

SENSITIVITY_SCENARIOS = {
    "service_rate_400_mbps": {"service_rate_bits_per_ms": 400_000},
    "service_rate_1600_mbps": {"service_rate_bits_per_ms": 1_600_000},
    "ideal_detection": {"detection_scenario": "ideal"},
    "impaired_detection": {"detection_scenario": "impaired"},
    "annex_e_wus_mo_4ms": {"wus_rx_ns": 4_000_000},
    "lr_wus_power_45": {"lr_wus_power_unit": 45.0},
    "independent_wake_delay_3ms": {"gap_ns": 3_000_000},
    "measurement_offset_80ms": {"measurement_offset_ns": 80_000_000},
    "wus_offset_2p5ms": {"wus_offset_ns": 2_500_000},
    "cdrx_offset_80ms": {"cdrx_offset_ns": 80_000_000},
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _seed_row(run: dict, scenario: str) -> dict:
    energy = run["summary"]["energy"]
    packets = run["summary"]["packets"]
    return {
        "scenario": scenario,
        "traffic_id": run["metadata"]["traffic_id"],
        "case_id": run["metadata"]["case_id"],
        "seed": run["seed"],
        "avg_power_unit": energy["avg_power_unit"],
        "energy_unit_ms": energy["energy_unit_ms"],
        "arrived_count": packets["arrived_count"],
        "delivered_count": packets["delivered_count"],
        "pdb_satisfied_count": packets["pdb_satisfied_count"],
        "pdb_eligible_count": packets["pdb_eligible_count"],
        "completion_rate": packets["completion_rate"],
        "pdb_ratio": packets["pdb_ratio"],
        "delay_mean_ms": packets["delay_mean_ms_delivered"],
        "delay_samples_json": json.dumps(packets["delivered_delays_ms"], separators=(",", ":")),
        "traffic_sha256": run["traffic_sha256"],
    }


def _comparison_row(run: dict) -> dict:
    row = dict(run["summary"]["compare_fields"])
    row.update(
        {
            "seed": run["seed"],
            "case": run["metadata"]["case_id"],
            "traffic_sha256": run["traffic_sha256"],
            "start_ns": run["metadata"]["effective_window"]["start_ns"],
            "window_ns": run["metadata"]["effective_window"]["end_ns"]
            - run["metadata"]["effective_window"]["start_ns"],
        }
    )
    return row


def _summarize(seed_rows: list[dict], comparisons: list[dict], cases: tuple[str, ...]) -> list[dict]:
    result: list[dict] = []
    by_group: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in seed_rows:
        by_group[(row["scenario"], row["traffic_id"], row["case_id"])].append(row)
    for (scenario, traffic_id, case_id), group in sorted(by_group.items()):
        powers = [float(row["avg_power_unit"]) for row in group]
        arrived = sum(int(row["arrived_count"]) for row in group)
        delivered = sum(int(row["delivered_count"]) for row in group)
        eligible = sum(int(row["pdb_eligible_count"]) for row in group)
        satisfied = sum(int(row["pdb_satisfied_count"]) for row in group)
        delays: list[float] = []
        for row in group:
            delays.extend(json.loads(row["delay_samples_json"]))
        paired = None
        if case_id != "1-1":
            block = [
                row
                for row in comparisons
                if row["scenario"] == scenario and row["traffic_id"] == traffic_id
            ]
            paired = compare_paired(
                block,
                "1-1",
                case_id=case_id,
                bootstrap_seed=20260917,
                n_boot=4000,
            )
        bootstrap = (paired or {}).get("bootstrap") or {}
        result.append(
            {
                "scenario": scenario,
                "traffic_id": traffic_id,
                "case_id": case_id,
                "seed_count": len(group),
                "mean_power_unit": statistics.mean(powers),
                "power_sd_across_seeds": statistics.stdev(powers) if len(powers) > 1 else 0.0,
                "gain_vs_1_1": None if paired is None else paired.get("gain"),
                "gain_ci95_low": bootstrap.get("ci_low"),
                "gain_ci95_high": bootstrap.get("ci_high"),
                "arrived_count": arrived,
                "delivered_count": delivered,
                "completion_rate_pooled": delivered / arrived if arrived else None,
                "pdb_satisfied_count": satisfied,
                "pdb_eligible_count": eligible,
                "pdb_ratio_pooled": satisfied / eligible if eligible else None,
                "delay_mean_ms_pooled": statistics.mean(delays) if delays else None,
                "delay_p90_ms_pooled": _quantile(delays, 0.9),
            }
        )
    expected = len({row["scenario"] for row in seed_rows}) * len(TRAFFIC) * len(cases)
    if len(result) != expected:
        raise RuntimeError(f"summary group mismatch: got {len(result)}, expected {expected}")
    return result


def _run_block(
    *,
    profile: dict,
    registry: dict,
    config: dict,
    scenario: str,
    overrides: dict,
    cases: tuple[str, ...],
    seeds: tuple[int, ...],
    progress: list[int],
    total: int,
) -> tuple[list[dict], list[dict]]:
    seed_rows: list[dict] = []
    comparisons: list[dict] = []
    for traffic_id in TRAFFIC:
        for case_id in cases:
            for seed in seeds:
                options = evaluation_options(config, case_id)
                options.update(overrides)
                options["label"] = f"theory_audit_{scenario}"
                run = run_one(
                    profile,
                    registry,
                    case_id=case_id,
                    traffic_id=traffic_id,
                    seed=seed,
                    policy_id=COMPANY_R1_2603659,
                    options=options,
                    root=ROOT,
                )
                seed_rows.append(_seed_row(run, scenario))
                record = _comparison_row(run)
                record.update({"scenario": scenario, "traffic_id": traffic_id})
                comparisons.append(record)
                progress[0] += 1
                if progress[0] == 1 or progress[0] % 24 == 0 or progress[0] == total:
                    print(f"[{progress[0]}/{total}] {scenario} {traffic_id} {case_id} seed={seed} PASS", flush=True)
    return seed_rows, comparisons


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    profile = load_profile(PROFILE_PATH)
    registry = load_case_registry()
    config = json.loads(EVAL_CONFIG_PATH.read_text(encoding="utf-8"))
    total = len(TRAFFIC) * len(CASES) * len(NOMINAL_SEEDS) + (
        len(SENSITIVITY_SCENARIOS) * len(TRAFFIC) * len(FOCUS_CASES) * len(SENSITIVITY_SEEDS)
    )
    progress = [0]
    started = time.time()

    nominal_rows, nominal_compare = _run_block(
        profile=profile,
        registry=registry,
        config=config,
        scenario="nominal",
        overrides={},
        cases=CASES,
        seeds=NOMINAL_SEEDS,
        progress=progress,
        total=total,
    )
    nominal_summary = _summarize(nominal_rows, nominal_compare, CASES)
    _write_csv(OUT / "nominal_seed_metrics.csv", nominal_rows)
    _write_csv(OUT / "nominal_summary.csv", nominal_summary)

    sensitivity_rows: list[dict] = []
    sensitivity_compare: list[dict] = []
    for scenario, overrides in SENSITIVITY_SCENARIOS.items():
        rows, records = _run_block(
            profile=profile,
            registry=registry,
            config=config,
            scenario=scenario,
            overrides=overrides,
            cases=FOCUS_CASES,
            seeds=SENSITIVITY_SEEDS,
            progress=progress,
            total=total,
        )
        sensitivity_rows.extend(rows)
        sensitivity_compare.extend(records)
    sensitivity_summary = _summarize(sensitivity_rows, sensitivity_compare, FOCUS_CASES)
    _write_csv(OUT / "sensitivity_seed_metrics.csv", sensitivity_rows)
    _write_csv(OUT / "sensitivity_summary.csv", sensitivity_summary)

    manifest = {
        "status": "PASS",
        "purpose": "convergence and parameter sensitivity audit; summary-only, no raw trace retention",
        "nominal_seed_count": len(NOMINAL_SEEDS),
        "sensitivity_seed_count": len(SENSITIVITY_SEEDS),
        "nominal_cases": list(CASES),
        "sensitivity_cases": list(FOCUS_CASES),
        "traffic_ids": list(TRAFFIC),
        "scenarios": SENSITIVITY_SCENARIOS,
        "run_count": progress[0],
        "expected_run_count": total,
        "elapsed_seconds": time.time() - started,
        "evaluation_config": str(EVAL_CONFIG_PATH.relative_to(ROOT).as_posix()),
        "evaluation_config_sha256": _sha256(EVAL_CONFIG_PATH),
        "profile": str(PROFILE_PATH.relative_to(ROOT).as_posix()),
        "profile_sha256": _sha256(PROFILE_PATH),
        "source_sha256": {
            relative: _sha256(ROOT / relative)
            for relative in (
                "run_theory_audit.py",
                "run_cases_1_to_6.py",
                "wus_next/integration/runner.py",
                "wus_next/policy6g/machine.py",
                "wus_next/policy6g/strategies.py",
            )
        },
        "limitations": [
            "single-UE abstract scheduler and service-rate model",
            "short fixed windows are combined over independent seeds",
            "no PHY BLER/channel/scheduler calibration",
            "sensitivity results isolate one changed assumption at a time",
            "the primary matrix uses explicitly aligned zero calendar phases",
        ],
    }
    if progress[0] != total:
        raise RuntimeError(f"run count mismatch: got {progress[0]}, expected {total}")
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    hashes = []
    for path in sorted(item for item in OUT.iterdir() if item.is_file() and item.name != "SHA256SUMS.txt"):
        hashes.append(f"{_sha256(path)}  {path.name}")
    (OUT / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
