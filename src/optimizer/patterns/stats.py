"""
Exact tests used by pattern discovery: a binomial upper tail, a one-sided
Fisher exact tail, and Benjamini-Hochberg adjusted q-values. Standard
library only.
"""
from __future__ import annotations

import math
from typing import Sequence


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p), summed exactly with math.comb."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    q = 1.0 - p
    total = 0.0
    for x in range(k, n + 1):
        total += math.comb(n, x) * (p ** x) * (q ** (n - x))
    return min(1.0, max(0.0, total))


def fisher_one_sided(h_high: int, n_high: int, h_low: int, n_low: int) -> float:
    """One-sided Fisher exact tail that the high group's hit proportion exceeds
    the low group's: with K = h_high + h_low and N = n_high + n_low,
    sum for x from h_high to min(K, n_high) of
    C(K, x) * C(N - K, n_high - x) / C(N, n_high), exact via math.comb."""
    k_total, n_total = h_high + h_low, n_high + n_low
    numerator = sum(math.comb(k_total, x) * math.comb(n_total - k_total, n_high - x)
                    for x in range(h_high, min(k_total, n_high) + 1))
    return numerator / math.comb(n_total, n_high)


def median_of(values: Sequence[float]) -> float:
    """Median; the mean of the two middle values when the count is even."""
    ordered = sorted(values)
    m = len(ordered)
    if m == 0:
        raise ValueError("empty sequence")
    mid = m // 2
    return ordered[mid] if m % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def bh_qvalues(pvalues: Sequence[float]) -> list[float]:
    """Benjamini-Hochberg step-up adjusted p-values (q-values), in input order.

    q_(i) = min over j >= i of (m * p_(j) / j), capped at 1. A hypothesis is
    rejected at FDR level alpha exactly when its q-value is <= alpha.
    """
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    out = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        idx = order[rank - 1]
        running = min(running, pvalues[idx] * m / rank)
        out[idx] = min(1.0, running)
    return out


def nearest_rank(sorted_values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile of an ascending sequence (pct in 0-100)."""
    n = len(sorted_values)
    if n == 0:
        raise ValueError("empty sequence")
    rank = max(1, math.ceil(pct / 100.0 * n))
    return sorted_values[min(rank, n) - 1]


def circular_mean_deg(angles: Sequence[float]) -> float | None:
    if not angles:
        return None
    s = sum(math.sin(math.radians(a)) for a in angles)
    c = sum(math.cos(math.radians(a)) for a in angles)
    if abs(s) < 1e-12 and abs(c) < 1e-12:
        return None
    return round(math.degrees(math.atan2(s, c)) % 360.0, 1)
