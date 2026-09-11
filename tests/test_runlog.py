import json

import pytest


def _reload_db(tmp_path):
    import os
    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import importlib
    import sys
    import app.config as config
    import app.db as db
    importlib.reload(config); importlib.reload(db)
    for name in ('app.runlog', 'app.service', 'app.knowledge', 'app.resolution',
                 'app.retrieval', 'app.graph', 'app.importer', 'app.embeddings'):
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db()
    return db


def test_schema_has_run_tables(tmp_path):
    db = _reload_db(tmp_path)
    conn = db.connect()
    names = {r['name'] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert 'llm_runs' in names and 'llm_run_steps' in names
    assert 'extraction_runs' not in names


def test_run_success_records_steps_and_summary(tmp_path):
    db = _reload_db(tmp_path)
    from app.runlog import record_run

    with record_run('ask', agent_role='ask') as run:
        with run.step('answer', input_summary='question text') as step:
            step.output = 'the answer'
        run.summary = {'answer_chars': 10}

    conn = db.connect()
    run_row = conn.execute('SELECT * FROM llm_runs').fetchone()
    steps = conn.execute('SELECT * FROM llm_run_steps ORDER BY step_index').fetchall()
    conn.close()
    assert run_row['status'] == 'success'
    assert run_row['task_type'] == 'ask'
    assert run_row['duration_ms'] is not None
    assert run_row['finished_at'] is not None
    assert json.loads(run_row['summary_json']) == {'answer_chars': 10}
    assert run_row['step_count'] == 1
    assert len(steps) == 1
    assert steps[0]['status'] == 'success'
    assert steps[0]['output_text'] == 'the answer'
    assert steps[0]['step_index'] == 0


def test_run_failure_records_error_and_reraises(tmp_path):
    db = _reload_db(tmp_path)
    from app.runlog import record_run

    with pytest.raises(ValueError):
        with record_run('extract', document_id=None, agent_role='extractor') as run:
            with run.step('extract_batch', input_summary='batch') as step:
                step.output = 'partial'
                raise ValueError('boom')

    conn = db.connect()
    run_row = conn.execute('SELECT * FROM llm_runs').fetchone()
    step = conn.execute('SELECT * FROM llm_run_steps').fetchone()
    conn.close()
    assert run_row['status'] == 'failed'
    assert 'boom' in run_row['error_message']
    assert step['status'] == 'failed'
    assert 'boom' in step['error_message']
    assert step['output_text'] == 'partial'
    assert run_row['step_count'] == 0  # only success steps count


def test_failed_run_counts_successful_steps(tmp_path):
    db = _reload_db(tmp_path)
    from app.runlog import record_run

    with pytest.raises(ValueError):
        with record_run('extract', agent_role='extractor') as run:
            with run.step('extract_batch', input_summary='b1') as step:
                step.output = '{}'
            raise ValueError('persist failed')

    conn = db.connect()
    run_row = conn.execute('SELECT status, step_count FROM llm_runs').fetchone()
    conn.close()
    assert run_row['status'] == 'failed'
    assert run_row['step_count'] == 1


def test_step_indices_increment(tmp_path):
    db = _reload_db(tmp_path)
    from app.runlog import record_run

    with record_run('extract', agent_role='extractor') as run:
        with run.step('extract_batch', input_summary='b1') as s1:
            s1.output = '{}'
        with run.step('extract_repair', input_summary='b1 retry') as s2:
            s2.output = '{}'

    conn = db.connect()
    idx = [r['step_index'] for r in conn.execute('SELECT step_index FROM llm_run_steps ORDER BY step_index')]
    names = [r['name'] for r in conn.execute('SELECT name FROM llm_run_steps ORDER BY step_index')]
    conn.close()
    assert idx == [0, 1]
    assert names == ['extract_batch', 'extract_repair']


def test_async_run_context(tmp_path):
    import asyncio
    db = _reload_db(tmp_path)
    from app.runlog import record_run

    async def main():
        async with record_run('agent', agent_role='personal') as run:
            async with run.step('agent_reply', input_summary='hello') as step:
                step.output = 'hi'
            run.summary = {'answer_chars': 2}

    asyncio.run(main())
    conn = db.connect()
    run_row = conn.execute('SELECT * FROM llm_runs').fetchone()
    conn.close()
    assert run_row['status'] == 'success'
    assert run_row['agent_role'] == 'personal'
    assert run_row['step_count'] == 1


def test_runlog_failure_does_not_break_main_flow(tmp_path):
    db = _reload_db(tmp_path)
    conn = db.connect()
    conn.execute('DROP TABLE llm_run_steps')
    conn.commit()
    conn.close()
    from app.runlog import record_run

    with record_run('ask') as run:
        with run.step('answer') as step:  # INSERT will fail and must be swallowed
            step.output = 'ok'

    conn = db.connect()
    run_row = conn.execute('SELECT status, step_count FROM llm_runs').fetchone()
    conn.close()
    assert run_row['status'] == 'success'
    assert run_row['step_count'] == 0


def test_index_document_records_extract_run(tmp_path, monkeypatch):
    import asyncio
    db = _reload_db(tmp_path)
    import app.llm as llm
    from app.service import create_document, index_document

    async def fake_extract_structured(payload):
        return {'entities': [{'name': 'RAG', 'types': ['Concept']}],
                'claims': [], 'events': [], 'ideas': [], 'questions': []}

    monkeypatch.setattr(llm, 'extract_structured', fake_extract_structured)
    doc_id = create_document(title='T', content='RAG helps.',
                             source_type='note', source_uri=None, metadata={})
    result = asyncio.run(index_document(doc_id, use_llm=True))

    assert result['llm'] == 'success'
    conn = db.connect()
    run_row = conn.execute("SELECT * FROM llm_runs WHERE task_type='extract'").fetchone()
    steps = conn.execute('SELECT * FROM llm_run_steps').fetchall()
    conn.close()
    assert run_row['status'] == 'success'
    assert run_row['document_id'] == doc_id
    assert run_row['agent_role'] == 'extractor'
    assert run_row['step_count'] == 1
    summary = json.loads(run_row['summary_json'])
    assert summary['entities'] == 1 and summary['chunks'] == 1
    assert len(steps) == 1 and steps[0]['name'] == 'extract_batch'
    assert json.loads(steps[0]['output_text'])['entities'][0]['name'] == 'RAG'
