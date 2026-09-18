"""Correct truncated-Gaussian sampling and exact truncated moments.

TR 38.838 v17.0.0 section 5.1.1 models XR frame size and frame-arrival
jitter as *truncated* Gaussian random variables; the mean and standard
deviation apply to the distribution *before* truncation (B443, B453).

Clipping a normal draw (``min(max(normal(), low), high)``) is not the
truncated distribution: it moves the probability mass of both tails onto
the boundaries, producing boundary point masses that the standard does
not define.  This module therefore uses rejection sampling against the
untruncated normal and provides the exact truncated moments so tests can
verify the sampler against the analytical distribution instead of the
pre-truncation parameters alone.
"""

from __future__ import annotations

import math

import numpy as np

_SQRT_TWO = math.sqrt(2.0)
_SQRT_TWO_PI = math.sqrt(2.0 * math.pi)


def normal_pdf(x: float) -> float:
    """Standard normal density."""

    return math.exp(-0.5 * x * x) / _SQRT_TWO_PI


def normal_cdf(x: float) -> float:
    """Standard normal CDF via ``erf``; exact to double precision."""

    return 0.5 * (1.0 + math.erf(x / _SQRT_TWO))


def truncated_normal_moments(mu: float, sigma: float, low: float, high: float) -> dict:
    """Return exact mean/std of ``N(mu, sigma)`` truncated to ``[low, high]``.

    Also reports the untruncated tail mass that a ``clip`` implementation
    would dump onto the boundaries, so reports can quantify the
    difference between clipping and correct truncation.
    """

    sigma = _positive(sigma, "sigma")
    low = _finite(low, "low")
    high = _finite(high, "high")
    if not low < high:
        raise ValueError("truncation range must satisfy low < high")
    alpha = (low - mu) / sigma
    beta = (high - mu) / sigma
    z_mass = normal_cdf(beta) - normal_cdf(alpha)
    if z_mass <= 0.0:
        raise ValueError("truncation range has zero probability under the untruncated normal")
    phi_alpha = normal_pdf(alpha)
    phi_beta = normal_pdf(beta)
    mean = mu + sigma * (phi_alpha - phi_beta) / z_mass
    variance = sigma * sigma * (
        1.0
        + (alpha * phi_alpha - beta * phi_beta) / z_mass
        - ((phi_alpha - phi_beta) / z_mass) ** 2
    )
    return {
        "mean": mean,
        "std": math.sqrt(max(variance, 0.0)),
        "alpha": alpha,
        "beta": beta,
        "truncated_mass": z_mass,
        "clip_mass_at_low": normal_cdf(alpha),
        "clip_mass_at_high": 1.0 - normal_cdf(beta),
    }


def sample_truncated_normal(
    rng: np.random.Generator,
    mu: float,
    sigma: float,
    low: float,
    high: float,
    *,
    max_attempts: int = 100_000,
) -> float:
    """Draw one sample from ``N(mu, sigma)`` truncated to ``[low, high]``.

    Rejection sampling guarantees the exact truncated distribution: the
    boundary has zero probability and no mass is piled onto ``low`` or
    ``high``.
    """

    sigma = _positive(sigma, "sigma")
    low = _finite(low, "low")
    high = _finite(high, "high")
    if not low < high:
        raise ValueError("truncation range must satisfy low < high")
    for _ in range(max_attempts):
        draw = float(rng.normal(mu, sigma))
        if low <= draw <= high:
            return draw
    raise RuntimeError(
        "truncated-normal rejection sampler exceeded max_attempts; "
        "the truncation range is too far into the tail"
    )


def sample_truncated_normal_batch(
    rng: np.random.Generator,
    mu: float,
    sigma: float,
    low: float,
    high: float,
    count: int,
) -> np.ndarray:
    """Vectorised rejection sampling for distribution studies."""

    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("count must be a non-negative integer")
    accepted: list[np.ndarray] = []
    remaining = count
    while remaining > 0:
        draws = rng.normal(mu, sigma, size=max(remaining * 2, 64))
        keep = draws[(draws >= low) & (draws <= high)]
        if keep.size == 0:
            raise RuntimeError("truncated-normal batch sampler made no progress; check the truncation range")
        take = keep[:remaining]
        accepted.append(take)
        remaining -= take.size
    return np.concatenate(accepted) if accepted else np.empty(0, dtype=float)


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _positive(value: float, name: str) -> float:
    value = _finite(value, name)
    if value <= 0.0:
        raise ValueError(f"{name} must be positive")
    return value


__all__ = [
    "normal_cdf",
    "normal_pdf",
    "sample_truncated_normal",
    "sample_truncated_normal_batch",
    "truncated_normal_moments",
]
