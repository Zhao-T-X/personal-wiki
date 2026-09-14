"""Acceptance tests for the evaluation board backend (Phase 7).

Covers POST /api/eval/run (all three suites), GET /api/eval/runs,
GET /api/eval/runs/{id}, and GET /api/eval/baseline. An autouse fixture keeps the
on-disk baselines current so the baseline endpoints have data to serve.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client():
    # conftest already pointed DATABASE_PATH/SETTINGS_PATH at an isolated temp DB.
    import app.db as db
    db.init_db()
    import app.main as main
    from fastapi.testclient import TestClient
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _ensure_baselines():
    # Keep the on-disk baselines current so the baseline endpoints have data.
    from app.evaluation import baseline
    baseline.main()


def test_run_all_three_suites(client):
    for suite in ('extraction', 'qa', 'retrieval'):
        resp = client.post('/api/eval/run', json={'suite': suite})
        assert resp.status_code == 200, (suite, resp.text)
        data = resp.json()
        assert data['suite'] == suite
        assert data['run_id']
        assert data['created_at']
        assert data['summary']
        assert 'case_details' in data['report']


def test_run_invalid_suite_returns_422(client):
    resp = client.post('/api/eval/run', json={'suite': 'bogus'})
    assert resp.status_code == 422
    # A request with no suite fails pydantic validation (also 422).
    assert client.post('/api/eval/run', json={}).status_code == 422


def test_list_and_get_runs(client):
    resp = client.post('/api/eval/run', json={'suite': 'extraction'})
    assert resp.status_code == 200
    run_id = resp.json()['run_id']

    listing = client.get('/api/eval/runs').json()
    assert any(item['run_id'] == run_id for item in listing)
    listed = next(item for item in listing if item['run_id'] == run_id)
    assert listed['suite'] == 'extraction'
    # The list view must NOT carry the full report.
    assert 'summary' in listed and 'report' not in listed

    detail = client.get(f'/api/eval/runs/{run_id}').json()
    assert detail['run_id'] == run_id
    assert 'report' in detail
    assert detail['summary'] == resp.json()['summary']

    assert client.get('/api/eval/runs/does-not-exist').status_code == 404


def test_runs_list_limit(client):
    client.post('/api/eval/run', json={'suite': 'extraction'})
    client.post('/api/eval/run', json={'suite': 'qa'})
    listing = client.get('/api/eval/runs?limit=1').json()
    assert len(listing) <= 1


def test_baseline_endpoint(client):
    allb = client.get('/api/eval/baseline').json()
    assert set(allb['suites'].keys()) >= {'extraction', 'qa', 'retrieval'}
    for suite in ('extraction', 'qa', 'retrieval'):
        single = client.get(f'/api/eval/baseline?suite={suite}').json()
        assert single['suite'] == suite
        assert single['metrics']


def test_baseline_pin(client):
    resp = client.post('/api/eval/run', json={'suite': 'qa'})
    assert resp.status_code == 200
    run_id = resp.json()['run_id']

    pin = client.post('/api/eval/baseline/pin', json={'suite': 'qa', 'run_id': run_id})
    assert pin.status_code == 200
    pinned = pin.json()
    assert pinned['suite'] == 'qa'
    assert pinned['metrics'] == resp.json()['summary']

    # The pinned baseline is now served by the baseline endpoint.
    served = client.get('/api/eval/baseline?suite=qa').json()
    assert served['metrics'] == resp.json()['summary']

    # Pinning a run of the wrong suite is rejected.
    bad = client.post('/api/eval/baseline/pin', json={'suite': 'qa', 'run_id': 'nope'})
    assert bad.status_code == 404
