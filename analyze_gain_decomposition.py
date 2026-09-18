"""Quantify where the Case 1-5/1-6 gain comes from.

The headline cases combine several mechanisms.  This analysis inserts one
paired counterfactual between Case 1-1 and Case 1-5: independent WUS keeps the
same 100 ms post-transmission activity tail as the baseline.  The resulting
waterfall separates trigger topology, the 100 -> 8 ms timer change, and the
non-EE -> EE measurement change without changing the traffic realizations.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import shutil
import statistics
from collections import defaultdict
from pathlib import Path

from run_cases_1_to_6 import EVAL_CONFIG_PATH, PROFILE_PATH, evaluation_options
from wus_next.contracts import load_profile
from wus_next.evidence import load_case_registry
from wus_next.integration.runner import run_one
from wus_next.policy6g.strategies import COMPANY_R1_2603659


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results" / "gain_decomposition"
NOMINAL_PATH = ROOT / "results" / "theory_audit" / "nominal_seed_metrics.csv"
RAW_DIR = ROOT / "results" / "case1_to_6" / "raw"
SEEDS = tuple(range(1001, 1065))
TRAFFIC = ("FTP3", "IM")
RAW_CASES = ("1-1", "1-5", "1-6")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ratio_gain(reference: list[float], candidate: list[float]) -> float:
    return 1.0 - sum(candidate) / sum(reference)


def _paired_bootstrap(reference: list[float], candidate: list[float], seed: int) -> tuple[float, float]:
    if len(reference) != len(candidate) or not reference:
        raise ValueError("paired bootstrap needs two non-empty equal-length arrays")
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(4000):
        indexes = [rng.randrange(len(reference)) for _ in reference]
        estimates.append(
            _ratio_gain(
                [reference[index] for index in indexes],
                [candidate[index] for index in indexes],
            )
        )
    estimates.sort()
    return estimates[int(0.025 * (len(estimates) - 1))], estimates[int(0.975 * (len(estimates) - 1))]


def _read_nominal() -> tuple[dict[tuple[str, str, int], float], dict[tuple[str, int], str]]:
    if not NOMINAL_PATH.is_file():
        raise FileNotFoundError("run run_theory_audit.bat before gain decomposition")
    with NOMINAL_PATH.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    powers = {
        (row["traffic_id"], row["case_id"], int(row["seed"])): float(row["avg_power_unit"])
        for row in rows
    }
    traffic_hashes: dict[tuple[str, int], str] = {}
    for row in rows:
        key = (row["traffic_id"], int(row["seed"]))
        value = row["traffic_sha256"]
        if key in traffic_hashes and traffic_hashes[key] != value:
            raise RuntimeError(f"nominal traffic hash mismatch for {key}")
        traffic_hashes[key] = value
    expected = {(traffic, case_id, seed) for traffic in TRAFFIC for case_id in RAW_CASES for seed in SEEDS}
    missing = sorted(expected - set(powers))
    if missing:
        raise RuntimeError(f"nominal seed matrix is incomplete; first missing key: {missing[0]}")
    return powers, traffic_hashes


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _energy_component_rows() -> list[dict]:
    """Create an eight-seed forensic ledger from the retained raw runs."""

    grouped: dict[tuple[str, str], list[dict[str, float]]] = defaultdict(list)
    for path in sorted(RAW_DIR.glob("*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        traffic = str(run["metadata"]["traffic_id"])
        case_id = str(run["metadata"]["case_id"])
        if case_id not in RAW_CASES:
            continue
        window = run["metadata"]["effective_window"]
        start_ns, end_ns = int(window["start_ns"]), int(window["end_ns"])
        window_ms = (end_ns - start_ns) / 1_000_000
        components: dict[str, float] = defaultdict(float)
        for interval in run["intervals"]:
            clipped_start = max(start_ns, int(interval["start_ns"]))
            clipped_end = min(end_ns, int(interval["end_ns"]))
            if clipped_end <= clipped_start:
                continue
            key = f"interval:{interval['receiver_id']}:{interval['state']}"
            components[key] += float(interval["power_unit"]) * (clipped_end - clipped_start) / 1_000_000 / window_ms
        for group, energy in run["summary"]["energy"].get("energy_by_hardware_group", {}).items():
            components[f"increment:{group}"] += float(energy) / window_ms
        components["total"] = float(run["summary"]["energy"]["avg_power_unit"])
        grouped[(traffic, case_id)].append(components)

    keys = sorted({key for runs in grouped.values() for components in runs for key in components})
    rows: list[dict] = []
    for (traffic, case_id), runs in sorted(grouped.items()):
        row: dict[str, object] = {"traffic_id": traffic, "case_id": case_id, "seed_count": len(runs)}
        for key in keys:
            row[key] = statistics.mean(components.get(key, 0.0) for components in runs)
        rows.append(row)
    if len(rows) != len(TRAFFIC) * len(RAW_CASES) or any(int(row["seed_count"]) != 8 for row in rows):
        raise RuntimeError("retained raw matrix does not contain 2 traffic types x 3 cases x 8 seeds")
    return rows


def main() -> int:
    nominal, nominal_traffic_hashes = _read_nominal()
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    profile = load_profile(PROFILE_PATH)
    registry = load_case_registry()
    config = json.loads(EVAL_CONFIG_PATH.read_text(encoding="utf-8"))
    counterfactual: dict[tuple[str, int], float] = {}
    seed_rows: list[dict] = []
    total = len(TRAFFIC) * len(SEEDS)
    completed = 0
    for traffic in TRAFFIC:
        for seed in SEEDS:
            options = evaluation_options(config, "1-5")
            options["label"] = "gain_decomposition_independent_wus_timer_100ms"
            options["timer_ns"] = 100_000_000
            run = run_one(
                profile,
                registry,
                case_id="1-5",
                traffic_id=traffic,
                seed=seed,
                policy_id=COMPANY_R1_2603659,
                options=options,
                root=ROOT,
            )
            if run["traffic_sha256"] != nominal_traffic_hashes[(traffic, seed)]:
                raise RuntimeError(f"counterfactual traffic mismatch for {traffic}/{seed}")
            power = float(run["summary"]["energy"]["avg_power_unit"])
            counterfactual[(traffic, seed)] = power
            seed_rows.append(
                {
                    "traffic_id": traffic,
                    "seed": seed,
                    "avg_power_unit": power,
                    "traffic_sha256": run["traffic_sha256"],
                }
            )
            completed += 1
            if completed == 1 or completed % 16 == 0 or completed == total:
                print(f"[{completed}/{total}] {traffic} seed={seed} PASS", flush=True)
    _write_csv(OUT / "counterfactual_seed_metrics.csv", seed_rows)

    decomposition: list[dict] = []
    for traffic in TRAFFIC:
        p11 = [nominal[(traffic, "1-1", seed)] for seed in SEEDS]
        pcf = [counterfactual[(traffic, seed)] for seed in SEEDS]
        p15 = [nominal[(traffic, "1-5", seed)] for seed in SEEDS]
        p16 = [nominal[(traffic, "1-6", seed)] for seed in SEEDS]
        steps = (
            ("independent_wus_trigger_at_100ms_tail", p11, pcf),
            ("timer_100ms_to_8ms", pcf, p15),
            ("total_case_1_1_to_1_5", p11, p15),
            ("non_ee_to_ee_measurement", p15, p16),
            ("total_case_1_1_to_1_6", p11, p16),
        )
        baseline_energy = sum(p11)
        for index, (name, reference, candidate) in enumerate(steps):
            ci_low, ci_high = _paired_bootstrap(reference, candidate, 20260918 + index)
            decomposition.append(
                {
                    "traffic_id": traffic,
                    "step": name,
                    "from_mean_power_unit": statistics.mean(reference),
                    "to_mean_power_unit": statistics.mean(candidate),
                    "step_relative_reduction": _ratio_gain(reference, candidate),
                    "step_ci95_low": ci_low,
                    "step_ci95_high": ci_high,
                    "baseline_normalized_contribution_pp": 100 * (sum(reference) - sum(candidate)) / baseline_energy,
                }
            )
    _write_csv(OUT / "gain_decomposition.csv", decomposition)
    _write_csv(OUT / "energy_component_breakdown_8seed.csv", _energy_component_rows())

    source_files = (
        "analyze_gain_decomposition.py",
        "run_cases_1_to_6.py",
        "wus_next/integration/runner.py",
        "wus_next/policy6g/machine.py",
        "wus_next/policy6g/strategies.py",
    )
    manifest = {
        "status": "PASS",
        "method": "paired 64-seed sequential counterfactual decomposition",
        "path": [
            "Case 1-1",
            "independent WUS with the same 100 ms post-transmission tail",
            "Case 1-5 with the proposed 8 ms tail",
            "Case 1-6 with EE measurement",
        ],
        "interpretation_warning": (
            "Sequential contributions depend on the selected path because trigger, timer and measurement costs interact. "
            "Negative or above-100-percentage-point contributions are valid offsets; only the total endpoint gain is path invariant."
        ),
        "nominal_seed_count": len(SEEDS),
        "counterfactual_run_count": len(seed_rows),
        "traffic_ids": list(TRAFFIC),
        "evaluation_config": str(EVAL_CONFIG_PATH.relative_to(ROOT).as_posix()),
        "evaluation_config_sha256": _sha256(EVAL_CONFIG_PATH),
        "nominal_seed_metrics": str(NOMINAL_PATH.relative_to(ROOT).as_posix()),
        "nominal_seed_metrics_sha256": _sha256(NOMINAL_PATH),
        "source_sha256": {name: _sha256(ROOT / name) for name in source_files},
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    checksum_lines = []
    for path in sorted(item for item in OUT.iterdir() if item.is_file() and item.name != "SHA256SUMS.txt"):
        checksum_lines.append(f"{_sha256(path)}  {path.name}")
    (OUT / "SHA256SUMS.txt").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
