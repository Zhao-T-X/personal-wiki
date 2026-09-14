"""Baseline management for the evaluation board.

A *baseline* is the set of aggregate metrics the suites currently produce; it is
the single source of truth for regression tests and the UI. This module is both a
CLI (``python -m app.evaluation.baseline``, which writes the three baseline
files) and a small library the API uses to read / pin baselines.

It deliberately does NOT persist runs to the database — baselines are pure
offline reference values, so they reuse only
:func:`app.evaluation.runners.evaluate_suite`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .runners import evaluate_suite

ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = ROOT / 'tests' / 'evaluation' / 'baselines'
SUITES = ('extraction', 'qa', 'retrieval')


def baseline_path(suite: str) -> Path:
    return BASELINE_DIR / f'{suite}.json'


def read_baseline(suite: str) -> dict | None:
    path = baseline_path(suite)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def read_all_baselines() -> dict:
    return {suite: read_baseline(suite) for suite in SUITES}


def pin_baseline(suite: str, metrics: dict) -> dict:
    if suite not in SUITES:
        raise ValueError(f'Unknown evaluation suite: {suite!r}')
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {'suite': suite, 'metrics': metrics}
    baseline_path(suite).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return payload


def main() -> int:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    for suite in SUITES:
        metrics = evaluate_suite(suite)
        pin_baseline(suite, metrics)
        print(f"baseline[{suite}] -> {baseline_path(suite)} ({len(metrics)} metrics)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
