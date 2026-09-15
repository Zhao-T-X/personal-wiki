"""Predicate hygiene: legacy predicates move, every fact stays where it was.

The property under test is not "the script runs". It is that a vocabulary fix can
be applied to a live knowledge base **without touching a single fact** — and that the
run happens against the database the operator named, never one the process inferred.
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_RELOAD = (
    'app.repositories.base',
    'app.repositories.document_repo',
    'app.repositories.entity_repo',
    'app.repositories.claim_repo',
    'app.repositories.relation_repo',
    'app.repositories.evidence_repo',
    'app.repositories.event_repo',
    'app.repositories.idea_repo',
    'app.repositories.question_repo',
    'app.repositories.research_repo',
    'app.repositories.run_repo',
    'app.repositories.catalog_repo',
    'app.repositories.operation_repo',
    'app.resolution',
    'app.knowledge',
    'app.claim_relations',
    'app.service',
)


# --- the plan is pure, and refuses to touch valid knowledge -------------------

def test_plan_ignores_predicates_the_registry_already_declares():
    """A migration that could rewrite valid knowledge would be a knowledge edit."""
    from app.domain.predicate_migration import plan_migrations

    assert plan_migrations({'has_ceo': 3, 'uses': 10, 'improves': 1}) == []


def test_plan_resolves_the_alias_and_separates_the_temporal_signal():
    """`ceo` is a declared alias; `new` is time, not vocabulary."""
    from app.domain.predicate_migration import plan_migrations

    plans = {p.legacy: p for p in plan_migrations({'ceo': 2, 'new_ceo': 1})}

    alias = plans['ceo']
    assert (alias.canonical, alias.temporal_signal) == ('has_ceo', None)
    assert alias.source == 'registry_alias' and alias.migratable

    temporal = plans['new_ceo']
    assert (temporal.canonical, temporal.temporal_signal) == ('has_ceo', 'new')
    assert temporal.source == 'temporal_split' and temporal.migratable


def test_plan_never_guesses_an_unmappable_value():
    from app.domain.predicate_migration import plan_migrations

    (plan,) = plan_migrations({'head_of_company': 4})
    assert plan.migratable is False and plan.canonical is None
    assert plan.action == 'needs_review'


def test_plan_puts_the_migratable_first():
    from app.domain.predicate_migration import plan_migrations

    plans = plan_migrations({'head_of_company': 9, 'ceo': 2})
    assert [p.legacy for p in plans] == ['ceo', 'head_of_company']


# --- the CLI, end to end ------------------------------------------------------

def _configured_db(tmp_path):
    """A schema-correct database holding three rows that predate the ontology gate."""
    os.environ['DATABASE_PATH'] = str(tmp_path / 'configured.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config as config
    import app.db as db
    importlib.reload(config)
    importlib.reload(db)
    for name in _RELOAD:
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db()

    from app.service import create_document, write_chunks
    text = '苹果公司的首席执行官是蒂姆·库克。'
    doc_id = create_document(title='苹果公司简介', content=text, source_type='note',
                             source_uri=None, metadata={})
    chunk_id = write_chunks(doc_id, text)[0]['id']
    entity_ids = {'苹果公司': 'e-apple', '蒂姆·库克': 'e-tim', '约翰·特努斯': 'e-john'}
    with db.transaction() as conn:
        for name, eid in entity_ids.items():
            etype = 'Organization' if name == '苹果公司' else 'Person'
            conn.execute(
                'INSERT INTO entities(id,type,types_json,name,aliases_json,properties_json,status) '
                'VALUES(?,?,?,?,?,?,?)',
                (eid, etype, json.dumps([etype]), name, '[]', '{}', 'candidate'))

        def claim(predicate, obj_id, obj_text):
            cid = str(uuid.uuid4())
            conn.execute(
                '''INSERT INTO claims(id,subject_id,predicate,object_id,object_text,content,context_json,
                       claim_type,polarity,modality,confidence,status,created_by,source_document_id,
                       source_chunk_id,source_start_offset,source_end_offset,source_quote)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (cid, 'e-apple', predicate, obj_id, obj_text, text, '{}', 'factual', 'positive',
                 'asserted', 0.9, 'candidate', 'llm', doc_id, chunk_id, 0, len(text), text))
            return cid

        ids = [claim('ceo', 'e-tim', '蒂姆·库克'),
               claim('ceo', 'e-tim', '蒂姆·库克'),
               claim('new_ceo', 'e-john', '约翰·特努斯')]
    return {'path': tmp_path / 'configured.db', 'ids': ids}


def _clone(source: Path, target: Path) -> None:
    src, dst = sqlite3.connect(source), sqlite3.connect(target)
    src.backup(dst)
    dst.close(); src.close()


def _run_cli(*args, env_db: Path):
    env = {**os.environ, 'DATABASE_PATH': str(env_db),
           'SETTINGS_PATH': str(env_db.parent / 'settings.json')}
    return subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'migrate_predicates.py'), *args],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding='utf-8', errors='replace')


def _predicates(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(path)
    counts = {r[0]: r[1] for r in conn.execute('SELECT predicate, COUNT(*) FROM claims GROUP BY predicate')}
    conn.close()
    return counts


def _migration_rows(path: Path) -> list[tuple]:
    conn = sqlite3.connect(path)
    rows = list(conn.execute(
        "SELECT payload_json, reason FROM knowledge_operations WHERE kind='MIGRATE_PREDICATE'"))
    conn.close()
    return rows


def _claim_rows(path: Path) -> dict[str, dict]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = {r['id']: dict(r) for r in conn.execute('SELECT * FROM claims')}
    conn.close()
    return rows


def test_dry_run_reports_the_plan_and_writes_nothing(tmp_path):
    seed = _configured_db(tmp_path)
    before = _predicates(seed['path'])

    result = _run_cli(env_db=seed['path'])
    assert result.returncode == 0, result.stderr
    assert 'Predicate Hygiene Report' in result.stdout
    assert 'DRY RUN' in result.stdout
    assert 'new_ceo' in result.stdout and 'has_ceo' in result.stdout

    assert _predicates(seed['path']) == before
    assert _migration_rows(seed['path']) == []


def test_apply_moves_the_claims_and_leaves_every_fact_alone(tmp_path):
    seed = _configured_db(tmp_path)
    before = _claim_rows(seed['path'])

    result = _run_cli('--apply', env_db=seed['path'])
    assert result.returncode == 0, result.stderr
    assert 'APPLIED' in result.stdout

    after = _claim_rows(seed['path'])
    assert set(after) == set(before)                       # same claims, not new ones
    assert {r['predicate'] for r in after.values()} == {'has_ceo'}
    for cid, row in after.items():
        for field in ('subject_id', 'object_id', 'object_text', 'content', 'status',
                      'claim_type', 'polarity', 'modality', 'confidence',
                      'source_document_id', 'source_chunk_id', 'source_start_offset',
                      'source_end_offset', 'source_quote'):
            assert row[field] == before[cid][field], field

    # The temporal signal left the predicate name and landed where time belongs.
    assert before[seed['ids'][2]]['predicate'] == 'new_ceo'
    assert json.loads(after[seed['ids'][2]]['context_json'])['temporal_signal'] == 'new'

    # And the move explains itself afterwards.
    rows = _migration_rows(seed['path'])
    assert len(rows) == 3
    assert all('legacy invalid predicate' in reason for _, reason in rows)
    payload = json.loads(rows[0][0])
    assert payload['from'] in ('ceo', 'new_ceo') and payload['to'] == 'has_ceo'


def test_migration_runs_against_the_named_database_never_an_inferred_one(tmp_path):
    """The accident this guards.

    ``app.config.DEFAULTS`` reads ``DATABASE_PATH`` at import time and
    ``get_settings()`` then lets ``settings.json`` override it, so an explicit
    target can be silently ignored and the run lands on whatever the config says.
    ``--database`` must win over both.
    """
    seed = _configured_db(tmp_path)
    configured = seed['path']
    explicit = tmp_path / 'explicit.db'
    _clone(configured, explicit)
    unchanged = _predicates(configured)

    result = _run_cli('--apply', '--database', str(explicit), env_db=configured)
    assert result.returncode == 0, result.stderr
    assert str(explicit.resolve()) in result.stdout        # the report names the target

    assert _predicates(explicit) == {'has_ceo': 3}         # the named database moved
    assert _predicates(configured) == unchanged            # the other one did not
    assert _migration_rows(configured) == []


def test_migration_makes_the_legacy_claims_visible_to_the_canonical_lookup(tmp_path):
    """The point of the whole exercise.

    ``related(predicate=...)`` matches the stored string, so a claim recorded as
    ``ceo`` is invisible to a lookup for ``has_ceo`` — search, conflict detection
    and the correction planner all miss it. That is the defect being repaired.
    """
    seed = _configured_db(tmp_path)

    from app.repositories.claim_repo import ClaimRepository
    assert ClaimRepository().related(subject_id='e-apple', predicate='has_ceo',
                                     exclude_id='', limit=10) == []

    assert _run_cli('--apply', env_db=seed['path']).returncode == 0

    # The write happened in another process, so this process's read cache must be
    # told. Without this the assertion below could pass on a stale answer either way.
    from app.cache import bump_data_epoch
    bump_data_epoch()
    found = ClaimRepository().related(subject_id='e-apple', predicate='has_ceo',
                                     exclude_id='', limit=10)
    assert len(found) == 3
