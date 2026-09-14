"""Architecture boundary tests (docs/architecture/PUBLIC-ENTRYPOINTS.md, ADR-002/004).

These are cheap, mechanical guards against the architecture silently eroding:
they read source, not runtime, so they fail loudly the moment a layer starts
reaching where it should not.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Business / presentation modules that must not speak SQL themselves.
BUSINESS_MODULES = (
    'app/main.py',
    'app/service.py',
    'app/retrieval.py',
    'app/knowledge.py',
    'app/resolution.py',
    'app/claim_relations.py',
    'app/graph.py',
)

# The only places allowed to open a connection or execute SQL.
PERSISTENCE_PREFIXES = (
    'app/db.py',
    'app/repositories/',
    'app/context/',
    'app/tools/',
    'app/embeddings.py',
    'app/prompt_profiles.py',
    'app/runlog.py',
    # Evaluation landing files that legitimately persist results (task §16).
    # They are the *only* evaluation modules exempt from the purity red line below.
    'app/evaluation/store.py',
    'app/evaluation/runners.py',
)

# The five harness modules that must stay pure: imported by ``app.evaluation`` and
# exercised in CI with no API key and no database. They may not reach for the web
# framework, the agent framework, the database driver or the LLM client.
EVAL_PURE_MODULES = (
    'app/evaluation/__init__.py',
    'app/evaluation/extraction_eval.py',
    'app/evaluation/qa_eval.py',
    'app/evaluation/retrieval_eval.py',
    'app/evaluation/metrics.py',
)

# Imports the pure evaluation harness must never make.
EVAL_BANNED_IMPORTS = ('fastapi', 'agentscope', 'sqlite3', 'llm')

FRAMEWORKS = ('fastapi', 'agentscope', 'sqlite3')


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding='utf-8')


def test_business_modules_contain_no_sql():
    for rel in BUSINESS_MODULES:
        src = _source(rel)
        assert '.execute(' not in src, f'{rel} executes SQL directly'
        assert 'connect()' not in src, f'{rel} opens a DB connection directly'


def test_sql_lives_only_in_the_persistence_layer():
    offenders = []
    for path in (ROOT / 'app').rglob('*.py'):
        rel = str(path.relative_to(ROOT)).replace('\\', '/')
        if rel.startswith(PERSISTENCE_PREFIXES):
            continue
        src = path.read_text(encoding='utf-8')
        if '.execute(' in src or 'connect()' in src:
            offenders.append(rel)
    assert offenders == [], f'SQL outside the persistence layer: {offenders}'


def test_domain_layer_is_framework_free():
    for path in (ROOT / 'app' / 'domain').glob('*.py'):
        src = path.read_text(encoding='utf-8')
        for framework in FRAMEWORKS:
            assert f'import {framework}' not in src, f'{path.name} must not import {framework}'
        assert 'from ..db' not in src and 'from .db' not in src, \
            f'{path.name} must not depend on the database driver'
        assert '.execute(' not in src, f'{path.name} must not run SQL'


def test_llm_client_is_not_imported_by_the_domain_layer():
    """Domain must not reach for the model directly; the Workflow owns the LLM step."""
    for path in (ROOT / 'app' / 'domain').glob('*.py'):
        src = path.read_text(encoding='utf-8')
        assert 'from ..llm' not in src and 'import llm' not in src, \
            f'{path.name} must not import the LLM client'


def test_ontology_semantics_are_not_redeclared_in_python():
    """ADR-011 rule 6 / task §31: the ontology is registry data, not Python.

    Registered vocabularies (predicates, entity types, and predicate semantics
    such as ``functional``) may only be declared in ``schemas/`` and read
    through ``app/ontology.py``. A second, code-resident copy would let the
    runtime drift from — or silently extend — the ontology.
    """
    literals = ('FUNCTIONAL_PREDICATES = frozenset({', 'CLAIM_PREDICATES = {',
                'ENTITY_TYPES = {', 'RELATION_TYPES = {')
    offenders = []
    for path in (ROOT / 'app').rglob('*.py'):
        rel = str(path.relative_to(ROOT)).replace('\\', '/')
        if rel == 'app/ontology.py':
            continue                       # the one reader of the registry
        src = path.read_text(encoding='utf-8')
        offenders.extend(f'{rel}: {literal}' for literal in literals if literal in src)
    assert offenders == [], f'ontology values redeclared in Python: {offenders}'


def test_evaluation_modules_are_pure():
    """The offline eval harness must not depend on runtime infrastructure (red line).

    ``app.evaluation`` is imported and run in CI with no API key and no database to
    produce reproducible numbers. If any of the five pure harness modules starts to
    import FastAPI, AgentScope, sqlite3 or the LLM client, those numbers can no
    longer be trusted and the regression suite silently becomes environment-dependent.

    DB-backed landing files (``store.py`` / ``runners.py``) are intentionally exempt
    and covered by the persistence rule in
    :func:`test_sql_lives_only_in_the_persistence_layer`.
    """
    pattern = re.compile(
        r'^\s*(?:import\s+.*\b(?:'
        + '|'.join(EVAL_BANNED_IMPORTS)
        + r')\b|from\s+[^#]*\b(?:'
        + '|'.join(EVAL_BANNED_IMPORTS)
        + r')\b)',
        re.MULTILINE,
    )
    for rel in EVAL_PURE_MODULES:
        src = _source(rel)
        bad = pattern.search(src)
        assert bad is None, f'{rel} must not import {EVAL_BANNED_IMPORTS} (matched: {bad.group(0).strip()!r})'
