"""Fixed-sample confidence intervals and paired bootstrap helpers.

Pure standard library.  Estimates never invent coverage for empty samples:
callers must return ``null`` + reason when n == 0.  Zero observed errors
never imply probability zero; Clopper-Pearson and the one-sided rule-of-three
bound remain strictly positive for finite n.
"""

from __future__ import annotations

import math
import random
from typing import Sequence


Z_975 = 1.959963984540054  # Phi^{-1}(0.975)


def _as_int(k: int, n: int) -> tuple[int, int]:
    if isinstance(k, bool) or not isinstance(k, int):
        raise TypeError("k must be int")
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("n must be int")
    if n < 0 or k < 0 or k > n:
        raise ValueError(f"require 0 <= k <= n, got k={k}, n={n}")
    return k, n


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""

    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0 if k < n else 1.0
    # Sum from 0..k with log-space safety for moderate n used in detection trials.
    total = 0.0
    log_choose = 0.0  # ln C(n, 0)
    log_p = math.log(p)
    log_q = math.log1p(-p)
    for i in range(0, k + 1):
        if i > 0:
            log_choose += math.log(n - i + 1) - math.log(i)
        total += math.exp(log_choose + i * log_p + (n - i) * log_q)
        if total >= 1.0:
            return 1.0
    return min(1.0, max(0.0, total))


def clopper_pearson(k: int, n: int, confidence: float = 0.95) -> tuple[float | None, float | None]:
    """Two-sided Clopper-Pearson interval.

    Returns (None, None) when n == 0.  Zero successes keep lower bound 0;
    zero failures keep upper bound strictly below 1 for finite n.
    """

    k, n = _as_int(k, n)
    if n == 0:
        return None, None
    if not (0.0 < confidence < 1.0):
        raise ValueError("confidence must be in (0,1)")
    alpha = 1.0 - confidence
    if k == 0:
        lower = 0.0
    else:
        lower = _bisect_binom_tail(k, n, alpha / 2.0, lower=True)
    if k == n:
        upper = 1.0
    else:
        upper = _bisect_binom_tail(k, n, alpha / 2.0, lower=False)
    return lower, upper


def _bisect_binom_tail(k: int, n: int, tail_prob: float, *, lower: bool, iterations: int = 80) -> float:
    """Invert binomial CDF for Clopper-Pearson bounds by bisection."""

    lo, hi = 0.0, 1.0
    for _ in range(iterations):
        mid = 0.5 * (lo + hi)
        if lower:
            # Want largest p with P(X >= k | p) >= tail? CP lower solves
            # P(X >= k | p_L) = alpha/2  <=> 1 - CDF(k-1; p_L) = alpha/2
            tail = 1.0 - _binom_cdf(k - 1, n, mid)
            if tail > tail_prob:
                lo = mid
            else:
                hi = mid
        else:
            # CP upper solves P(X <= k | p_U) = alpha/2.
            # CDF(k; n, p) decreases in p: if CDF(mid) > tail_prob, raise p.
            cdf = _binom_cdf(k, n, mid)
            if cdf > tail_prob:
                lo = mid
            else:
                hi = mid
    return 0.5 * (lo + hi)


def wilson(k: int, n: int, confidence: float = 0.95) -> tuple[float | None, float | None]:
    """Wilson score interval for a binomial proportion."""

    k, n = _as_int(k, n)
    if n == 0:
        return None, None
    if not (0.0 < confidence < 1.0):
        raise ValueError("confidence must be in (0,1)")
    # Only z for 95% is tabulated; other levels use a rational approximation.
    if abs(confidence - 0.95) < 1e-12:
        z = Z_975
    else:
        # Acklam-style inverse via simple bisection on normal CDF.
        z = _inv_norm(0.5 + confidence / 2.0)
    phat = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(phat * (1.0 - phat) / n + z2 / (4.0 * n * n))
    if k == 0:
        low = 0.0
    else:
        low = max(0.0, center - half)
    if k == n:
        high = 1.0
    else:
        high = min(1.0, center + half)
    return low, high


def _inv_norm(p: float) -> float:
    """Inverse standard normal CDF (Acklam approximation + refinement)."""

    if not (0.0 < p < 1.0):
        raise ValueError("p must be in (0,1)")
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    p_low = 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p > 1.0 - p_low:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)


def one_sided_error_upper(n: int, alpha: float = 0.05) -> float | None:
    """Exact one-sided upper bound when zero errors are observed in n trials.

    For k = 0 the (1-alpha) upper bound is 1 - alpha**(1/n).  This is strictly
    positive for every finite n; never return 0 for a zero-error sample.
    """

    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        return None
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha must be in (0,1)")
    return 1.0 - alpha ** (1.0 / n)


def proportion_point(k: int, n: int) -> float | None:
    """MLE point estimate k/n, or None when n == 0."""

    k, n = _as_int(k, n)
    if n == 0:
        return None
    return k / n


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation percentile on [0,100]; None for empty input."""

    if not values:
        return None
    if not (0.0 <= q <= 100.0):
        raise ValueError("q must be in [0,100]")
    data = sorted(float(v) for v in values)
    if len(data) == 1:
        return data[0]
    pos = (q / 100.0) * (len(data) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return data[lo]
    weight = pos - lo
    return data[lo] * (1.0 - weight) + data[hi] * weight


def mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(float(v) for v in values) / len(values)


def ratio_of_sums(numer: Sequence[float], denom: Sequence[float]) -> float | None:
    """sum(numer)/sum(denom); None if denominator sum is 0 or lengths differ."""

    if len(numer) != len(denom):
        raise ValueError("paired sequences must have equal length")
    if not numer:
        return None
    dsum = sum(float(v) for v in denom)
    if dsum == 0.0:
        return None
    return sum(float(v) for v in numer) / dsum


def paired_bootstrap_gain(
    baseline_powers: Sequence[float],
    case_powers: Sequence[float],
    *,
    bootstrap_seed: int,
    n_boot: int = 4000,
    confidence: float = 0.95,
    estimator: str = "1_minus_ratio_of_mean_powers",
) -> dict:
    """Seed-level paired bootstrap for power-saving gain.

    Resamples the same seed index from baseline and case.  Returns
    ``ci_low``/``ci_high`` as None when fewer than 2 paired seeds exist.
    """

    if len(baseline_powers) != len(case_powers):
        raise ValueError("baseline and case must be aligned by seed index")
    n = len(baseline_powers)
    point = None
    if n > 0:
        bmean = sum(float(v) for v in baseline_powers) / n
        cmean = sum(float(v) for v in case_powers) / n
        if bmean != 0.0:
            point = 1.0 - cmean / bmean
    result = {
        "estimator": estimator,
        "n_pairs": n,
        "n_boot": n_boot if n >= 2 else 0,
        "bootstrap_seed": bootstrap_seed,
        "confidence": confidence,
        "gain": point,
        "ci_low": None,
        "ci_high": None,
        "ci_method": "paired seed percentile bootstrap" if n >= 2 else "NOT_COMPUTED_SINGLE_SEED",
        "reason": None if n >= 2 else "fewer_than_2_paired_seeds",
    }
    if n < 2:
        return result
    rng = random.Random(bootstrap_seed)
    draws: list[float] = []
    b = [float(v) for v in baseline_powers]
    c = [float(v) for v in case_powers]
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        bmean = sum(b[i] for i in idx) / n
        cmean = sum(c[i] for i in idx) / n
        if bmean == 0.0:
            continue
        draws.append(1.0 - cmean / bmean)
    if not draws:
        result["reason"] = "bootstrap_degenerate_zero_baseline"
        return result
    alpha = 1.0 - confidence
    result["ci_low"] = percentile(draws, 100.0 * alpha / 2.0)
    result["ci_high"] = percentile(draws, 100.0 * (1.0 - alpha / 2.0))
    result["n_valid_draws"] = len(draws)
    return result


def detection_interval(k: int, n: int, method: str = "wilson", confidence: float = 0.95) -> dict:
    """Fixed-sample binomial interval for a detection error rate."""

    if method not in {"wilson", "clopper_pearson"}:
        raise ValueError(f"unknown CI method {method!r}")
    point = proportion_point(k, n)
    if n == 0:
        return {
            "point": None,
            "ci_low": None,
            "ci_high": None,
            "method": method,
            "confidence": confidence,
            "n": 0,
            "k": k,
            "status": "NO_TRIALS",
            "reason": "n=0; no confidence interval",
            "zero_error_one_sided_upper_95": None,
        }
    if method == "wilson":
        low, high = wilson(k, n, confidence)
    else:
        low, high = clopper_pearson(k, n, confidence)
    zero_upper = one_sided_error_upper(n) if k == 0 else None
    status = "OK"
    reason = None
    if zero_upper is not None:
        status = "ZERO_ERRORS"
        reason = "observed zero errors; probability is not zero; see one-sided upper bound"
    return {
        "point": point,
        "ci_low": low,
        "ci_high": high,
        "method": method,
        "confidence": confidence,
        "n": n,
        "k": k,
        "status": status,
        "reason": reason,
        "zero_error_one_sided_upper_95": zero_upper,
    }
