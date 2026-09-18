"""Paired baseline/case comparison with seed-level bootstrap."""

from __future__ import annotations

from typing import Any, Sequence

from .statistical import mean, paired_bootstrap_gain, ratio_of_sums


def _run_power(run: dict) -> float | None:
    if run.get("avg_power_unit") is not None:
        return float(run["avg_power_unit"])
    energy = run.get("energy_unit_ms")
    window_ns = run.get("window_ns")
    if energy is not None and window_ns:
        # energy_unit_ms / window_ms
        return float(energy) * 1_000_000.0 / float(window_ns)
    return None


def compare_paired(
    runs: Sequence[dict],
    baseline_id: str,
    bootstrap_seed: int,
    *,
    case_id: str | None = None,
    n_boot: int = 4000,
    confidence: float = 0.95,
) -> dict:
    """Compare case runs to a paired baseline.

    Each run dict must provide: seed, case (or role), baseline_id matching the
    caller expectation, traffic_sha256, and either avg_power_unit or
    (energy_unit_ms, window_ns).  Pairing requires identical traffic_sha256
    and the same observation window for every compared seed.

    Estimator: gain = 1 - sum(case_powers) / sum(baseline_powers).
    With equal seed weights this equals 1 - mean(case)/mean(baseline).
    It is NOT the mean of per-seed ratios (see oracle O05).
    """

    if not runs:
        return {
            "status": "EMPTY",
            "reason": "no runs supplied",
            "baseline_id": baseline_id,
            "case_id": case_id,
        }

    # Group by case label.
    by_case: dict[str, list[dict]] = {}
    for run in runs:
        label = run.get("case") or run.get("case_id") or run.get("profile_id")
        if label is None:
            raise ValueError("each run must carry case/case_id")
        by_case.setdefault(str(label), []).append(run)

    if baseline_id not in by_case:
        return {
            "status": "MISSING_BASELINE",
            "reason": f"baseline_id {baseline_id!r} not present in runs",
            "baseline_id": baseline_id,
            "case_id": case_id,
            "available_cases": sorted(by_case),
        }

    target_cases = [case_id] if case_id is not None else [c for c in by_case if c != baseline_id]
    if not target_cases:
        return {
            "status": "NO_CASE",
            "reason": "no non-baseline case to compare",
            "baseline_id": baseline_id,
        }

    results = []
    for case in sorted(target_cases):
        results.append(_compare_one(by_case[baseline_id], by_case[case], baseline_id, case, bootstrap_seed, n_boot, confidence))

    # Flatten when there is a single case so callers can read gain/n_pairs directly.
    if len(results) == 1:
        return results[0]
    return {
        "status": "OK",
        "baseline_id": baseline_id,
        "comparisons": results,
    }


def _compare_one(
    base_runs: list[dict],
    case_runs: list[dict],
    baseline_id: str,
    case_id: str,
    bootstrap_seed: int,
    n_boot: int,
    confidence: float,
) -> dict:
    base_by_seed = {int(r["seed"]): r for r in base_runs}
    case_by_seed = {int(r["seed"]): r for r in case_runs}
    shared = sorted(set(base_by_seed) & set(case_by_seed))
    missing_base = sorted(set(case_by_seed) - set(base_by_seed))
    missing_case = sorted(set(base_by_seed) - set(case_by_seed))

    pairing_errors: list[str] = []
    for seed in shared:
        b, c = base_by_seed[seed], case_by_seed[seed]
        if b.get("traffic_sha256") != c.get("traffic_sha256"):
            pairing_errors.append(f"seed {seed}: traffic_sha256 mismatch")
        if b.get("window_ns") is not None and c.get("window_ns") is not None:
            if b.get("window_ns") != c.get("window_ns"):
                pairing_errors.append(f"seed {seed}: observation window length mismatch")
            if (
                b.get("start_ns") is not None
                and c.get("start_ns") is not None
                and b.get("start_ns") != c.get("start_ns")
            ):
                pairing_errors.append(f"seed {seed}: observation window start mismatch")

    if pairing_errors:
        return {
            "status": "PAIRING_FAILED",
            "baseline_id": baseline_id,
            "case_id": case_id,
            "reason": "; ".join(pairing_errors),
            "gain": None,
            "n_pairs": len(shared),
            "seeds": shared,
            "estimator": "1_minus_ratio_of_mean_powers",
            "bootstrap": {"status": "NOT_COMPUTED", "reason": "pairing_failed", "ci_low": None, "ci_high": None},
        }

    if not shared:
        return {
            "status": "NO_SHARED_SEEDS",
            "baseline_id": baseline_id,
            "case_id": case_id,
            "reason": "no overlapping seeds",
            "gain": None,
            "n_pairs": 0,
            "missing_baseline_seeds": missing_base,
            "missing_case_seeds": missing_case,
        }

    base_powers: list[float] = []
    case_powers: list[float] = []
    for seed in shared:
        bp = _run_power(base_by_seed[seed])
        cp = _run_power(case_by_seed[seed])
        if bp is None or cp is None:
            return {
                "status": "MISSING_POWER",
                "baseline_id": baseline_id,
                "case_id": case_id,
                "reason": f"seed {seed} lacks avg_power_unit or energy/window",
                "gain": None,
                "n_pairs": len(shared),
            }
        base_powers.append(bp)
        case_powers.append(cp)

    base_sum = sum(base_powers)
    case_sum = sum(case_powers)
    if base_sum == 0.0:
        gain = None
        gain_reason = "baseline power sum is zero"
    else:
        gain = 1.0 - case_sum / base_sum
        gain_reason = None

    boot = paired_bootstrap_gain(
        base_powers,
        case_powers,
        bootstrap_seed=bootstrap_seed,
        n_boot=n_boot,
        confidence=confidence,
        estimator="1_minus_ratio_of_mean_powers",
    )

    # Mean of per-seed ratios (explicitly separate; often misleading).
    per_seed_ratios = []
    for bp, cp in zip(base_powers, case_powers):
        if bp != 0.0:
            per_seed_ratios.append(1.0 - cp / bp)

    # Declared total-energy estimator when windows differ across seeds is
    # rejected above; when windows are equal, ratio-of-sums equals mean ratio.
    windows = {base_by_seed[s].get("window_ns") for s in shared}
    equal_windows = len(windows) == 1 and None not in windows

    return {
        "status": "OK",
        "baseline_id": baseline_id,
        "case_id": case_id,
        "seeds": shared,
        "n_pairs": len(shared),
        "baseline_powers": base_powers,
        "case_powers": case_powers,
        "baseline_mean_power": mean(base_powers),
        "case_mean_power": mean(case_powers),
        "gain": gain,
        "gain_reason": gain_reason,
        "estimator": "1_minus_ratio_of_mean_powers",
        "estimator_note": "1 - sum(case)/sum(baseline); equal-weight mean-of-ratios is a different quantity",
        "mean_of_per_seed_ratios": mean(per_seed_ratios) if per_seed_ratios else None,
        "bootstrap": boot,
        "equal_observation_windows": equal_windows,
        "missing_baseline_seeds": missing_base,
        "missing_case_seeds": missing_case,
    }
