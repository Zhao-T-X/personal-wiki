"""Deterministic precision/recall/F1 primitives.

Pure arithmetic only - no I/O, no framework, no LLM. Empty denominators return
``0.0`` (not ``nan``) so that an event with nothing to measure can never poison
an aggregate with ``float('nan')``.
"""
from __future__ import annotations


def precision(tp: float, fp: float) -> float:
    """Fraction of predicted positives that are correct."""
    denominator = float(tp) + float(fp)
    if not denominator:
        return 0.0
    return float(tp) / denominator


def recall(tp: float, fn: float) -> float:
    """Fraction of expected positives that were found."""
    denominator = float(tp) + float(fn)
    if not denominator:
        return 0.0
    return float(tp) / denominator


def f1(p: float, r: float) -> float:
    """Harmonic mean of precision and recall."""
    total = float(p) + float(r)
    if not total:
        return 0.0
    return 2.0 * float(p) * float(r) / total
