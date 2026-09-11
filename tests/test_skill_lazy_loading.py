"""Context Runtime P2c: skill/reference lazy loading with cost metadata.

Every reference declares *when* loading it is justified (load_when) and what it
costs (estimated_tokens). The selector fires only on signals that deviate from
the intent defaults, so the P1 extraction baseline (zero references inlined)
must hold for default-shaped tasks.
"""
from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RELOAD_MODULES = (
    'app.skills',
    'app.runtime.task',
    'app.context.tokens',
    'app.context.policies',
    'app.context.items',
    'app.context.planner',
    'app.context.providers.skills',
)


def _reload(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'wiki.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config
    importlib.reload(app.config)
    for name in _RELOAD_MODULES:
        importlib.reload(importlib.import_module(name))


def test_reference_metadata_declares_cost_and_load_when(tmp_path):
    _reload(tmp_path)
    from app.skills import reference_meta

    meta = {m['reference']: m for m in reference_meta('knowledge-extraction')}
    assert 'entity-types.md' in meta and 'claim-predicates.md' in meta
    for m in meta.values():
        assert m['lazy'] is True
        assert m['estimated_tokens'] > 0
        assert m['load_when']


def test_default_shaped_task_loads_no_references(tmp_path):
    _reload(tmp_path)
    from app.runtime.task import features_for
    from app.skills import references_for

    assert references_for('knowledge-extraction', features_for('ExtractionAgent', 'extract')) == []
    assert references_for('knowledge-curation', features_for('CuratorAgent', 'curate')) == []


def test_load_when_rules_fire_only_on_deviating_signals(tmp_path):
    _reload(tmp_path)
    from app.runtime.task import features_for
    from app.skills import references_for

    conflict = features_for('ExtractionAgent', 'extract', conflict_level=0.5)
    assert references_for('knowledge-extraction', conflict) == ['relation-predicates.md']

    deep = features_for('ExtractionAgent', 'extract', complexity=0.8)
    assert 'extraction-v2.md' in references_for('knowledge-extraction', deep)

    evidence_critical = features_for('ExtractionAgent', 'extract', evidence_requirement=0.97)
    assert references_for('knowledge-extraction', evidence_critical) == ['evidence.md']

    curate_conflict = features_for('CuratorAgent', 'curate', conflict_level=0.5)
    assert references_for('knowledge-curation', curate_conflict) == ['normalization.md']


def test_provider_merges_explicit_and_derived_references_with_costs(tmp_path):
    _reload(tmp_path)
    from app.context.providers.skills import SkillProvider
    from app.runtime.task import TaskContext, features_for

    task = TaskContext(
        task_type='extract', agent='ExtractionAgent', skill='knowledge-extraction',
        features=features_for('ExtractionAgent', 'extract',
                              complexity=0.8, references=['claim-predicates.md']),
    )
    items = {i.id: i for i in SkillProvider().provide(task, None)}

    assert 'reference:knowledge-extraction/claim-predicates.md' in items    # explicit
    assert 'reference:knowledge-extraction/extraction-v2.md' in items       # load_when
    loaded = items['reference:knowledge-extraction/extraction-v2.md']
    assert loaded.metadata['estimated_tokens'] > 0
    assert 'load_when' in loaded.reason or 'selected by load_when' in loaded.reason
    assert items['skill:knowledge-extraction'].metadata['estimated_tokens'] > 0


def test_unknown_reference_names_are_ignored(tmp_path):
    _reload(tmp_path)
    from app.context.providers.skills import SkillProvider
    from app.runtime.task import TaskContext, features_for

    task = TaskContext(
        task_type='extract', agent='ExtractionAgent', skill='knowledge-extraction',
        features=features_for('ExtractionAgent', 'extract', references=['nope.md']),
    )
    ids = [i.id for i in SkillProvider().provide(task, None)]
    assert not any('nope.md' in i for i in ids)
