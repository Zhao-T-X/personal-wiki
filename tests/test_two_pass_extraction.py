"""Task §12: two-pass extraction.

Extraction is split so that a simple chunk never pays for a full analysis:

    Document -> Chunk -> Pass 1: Lightweight Detection -> Pass 2: Targeted Extraction

Pass 1 only asks *which* knowledge kinds a batch holds; Pass 2 runs (and is
scoped) only for the kinds Pass 1 named. These tests never call a real model:
both passes are monkeypatched on ``app.llm`` so the orchestration is exercised
in isolation.
"""
from __future__ import annotations

import asyncio

import app.llm as llm
from app.agents.extraction_agent import Detection, wanted_kinds

ALL_KINDS = ('entities', 'claims', 'events', 'ideas', 'questions')

_CHUNKS = [
    {'id': 'chunk-1', 'content': 'RAG improves Accuracy. RAG v1 was released.'},
]


def _full_extraction() -> dict:
    """A schema-valid result that names every one of the five kinds.

    ``normalize_extraction`` accepts it as-is, so it doubles as a way to prove
    which kinds survive the Pass 2 restriction.
    """
    return {
        'entities': [{'name': 'RAG', 'types': ['Concept']}],
        'claims': [{
            'subject': 'RAG', 'predicate': 'improves', 'object': 'Accuracy',
            'claim_type': 'factual', 'polarity': 'positive', 'modality': 'asserted',
            'content': 'RAG improves Accuracy.', 'context': {}, 'confidence': 0.9,
            'source_chunk': 'chunk-1', 'evidence_quote': 'RAG improves Accuracy.',
        }],
        'events': [{
            'event_type': 'release', 'description': 'RAG v1 was released',
            'participants': ['RAG'], 'time': {'start': None, 'end': None, 'precision': 'unknown'},
            'location': None, 'status': 'completed', 'confidence': 0.8,
            'source_chunk': 'chunk-1', 'evidence_quote': 'RAG v1 was released.',
        }],
        'ideas': [{
            'content': 'Add a reranking stage', 'status': 'candidate', 'confidence': 0.6,
            'source_chunk': 'chunk-1', 'evidence_quote': 'Add a reranking stage.',
        }],
        'questions': [{
            'content': 'Does RAG scale?', 'question_type': 'research', 'status': 'open',
            'confidence': 0.5, 'source_chunk': 'chunk-1', 'evidence_quote': 'Does RAG scale?',
        }],
    }


def _detection(**flags) -> dict:
    return {kind: bool(flags.get(kind, False)) for kind in ALL_KINDS}


# --------------------------------------------------------------------------- #
# wanted_kinds
# --------------------------------------------------------------------------- #

def test_wanted_kinds_returns_only_the_true_flags():
    assert wanted_kinds(_detection(entities=True, claims=True)) == {'entities', 'claims'}
    assert wanted_kinds(_detection()) == set()          # everything False
    assert wanted_kinds({}) == set()                    # missing keys
    assert wanted_kinds(None) == set()                  # detection unavailable
    # The Pydantic Detection the agent returns is understood too.
    assert wanted_kinds(Detection(ideas=True)) == {'ideas'}


# --------------------------------------------------------------------------- #
# Pass 1 gates Pass 2
# --------------------------------------------------------------------------- #

def test_all_false_detection_skips_pass2(monkeypatch):
    """Nothing named -> the extraction agent is never called, result is empty."""
    extract_calls: list[str] = []

    async def fake_detect(payload):
        return _detection()                             # all False

    async def fake_extract(payload):
        extract_calls.append(payload)
        return _full_extraction()

    monkeypatch.setattr(llm, 'detect_structured', fake_detect)
    monkeypatch.setattr(llm, 'extract_structured', fake_extract)

    out = asyncio.run(llm.extract(_CHUNKS))

    assert extract_calls == []                          # Pass 2 never ran
    assert out == {kind: [] for kind in ALL_KINDS}


def test_only_named_kinds_survive(monkeypatch):
    """Only entities+claims named -> the other three arrays come back empty."""
    seen: list[str] = []

    async def fake_detect(payload):
        return _detection(entities=True, claims=True)

    async def fake_extract(payload):
        seen.append(payload)
        # Pass 2 pretends it over-extracted all five kinds; the orchestrator
        # must clear the ones Pass 1 did not ask for.
        return _full_extraction()

    monkeypatch.setattr(llm, 'detect_structured', fake_detect)
    monkeypatch.setattr(llm, 'extract_structured', fake_extract)

    out = asyncio.run(llm.extract(_CHUNKS))

    assert set(out) == set(ALL_KINDS)                   # schema stays complete
    assert [e['name'] for e in out['entities']] == ['RAG']
    assert len(out['claims']) == 1
    assert out['events'] == []
    assert out['ideas'] == []
    assert out['questions'] == []
    # Pass 2 was told which kinds to extract.
    assert 'PASS 2 SCOPE' in seen[0]
    assert 'entities' in seen[0] and 'claims' in seen[0]


# --------------------------------------------------------------------------- #
# Pass 1 failure must not lose the batch
# --------------------------------------------------------------------------- #

def test_detection_failure_degrades_to_single_pass(monkeypatch):
    """A broken detection model falls back to extracting everything."""
    seen: list[str] = []

    async def failing_detect(payload):
        raise RuntimeError('detection model unavailable')

    async def fake_extract(payload):
        seen.append(payload)
        return _full_extraction()

    monkeypatch.setattr(llm, 'detect_structured', failing_detect)
    monkeypatch.setattr(llm, 'extract_structured', fake_extract)

    out = asyncio.run(llm.extract(_CHUNKS))

    assert len(seen) == 1                               # Pass 2 still ran
    assert 'PASS 2 SCOPE' not in seen[0]                # no narrowing applied
    # Nothing was dropped: every kind the extractor returned survives.
    assert out['entities'] and out['claims']
    assert out['events'] and out['ideas'] and out['questions']


# --------------------------------------------------------------------------- #
# runlog integration: detection is its own step
# --------------------------------------------------------------------------- #

def _reload_db(tmp_path):
    import importlib
    import os

    os.environ['DATABASE_PATH'] = str(tmp_path / 'wiki.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config
    import app.db
    import app.runlog
    importlib.reload(app.config)
    importlib.reload(app.db)
    importlib.reload(app.runlog)
    importlib.reload(llm)                               # rebind runlog helpers
    app.db.init_db()
    return app.db


def test_detect_batch_is_recorded_before_extract_batch(tmp_path, monkeypatch):
    db = _reload_db(tmp_path)
    from app.runlog import record_run

    async def fake_detect(payload):
        return _detection(entities=True)

    async def fake_extract(payload):
        return {'entities': [{'name': 'RAG', 'types': ['Concept']}],
                'claims': [], 'events': [], 'ideas': [], 'questions': []}

    monkeypatch.setattr(llm, 'detect_structured', fake_detect)
    monkeypatch.setattr(llm, 'extract_structured', fake_extract)

    async def main():
        async with record_run('extract', agent_role='extractor'):
            return await llm.extract(_CHUNKS)

    asyncio.run(main())

    conn = db.connect()
    names = [r['name'] for r in conn.execute(
        'SELECT name FROM llm_run_steps ORDER BY step_index')]
    conn.close()
    assert names == ['detect_batch', 'extract_batch']


def test_empty_detection_records_only_the_detect_step(tmp_path, monkeypatch):
    db = _reload_db(tmp_path)
    from app.runlog import record_run

    async def fake_detect(payload):
        return _detection()

    async def fake_extract(payload):                     # pragma: no cover
        raise AssertionError('Pass 2 must not run for an empty detection')

    monkeypatch.setattr(llm, 'detect_structured', fake_detect)
    monkeypatch.setattr(llm, 'extract_structured', fake_extract)

    async def main():
        async with record_run('extract', agent_role='extractor'):
            return await llm.extract(_CHUNKS)

    asyncio.run(main())

    conn = db.connect()
    names = [r['name'] for r in conn.execute(
        'SELECT name FROM llm_run_steps ORDER BY step_index')]
    conn.close()
    assert names == ['detect_batch']
