"""Architecture boundary tests (docs/architecture/PUBLIC-ENTRYPOINTS.md, ADR-002/004).

Most of these are cheap, mechanical guards against the architecture silently
eroding: they read source, not runtime, so they fail loudly the moment a layer
starts reaching where it should not.

One section at the bottom is different on purpose. "One owner for claim
normalisation" is not a property of text — a second implementation can be written
in a perfectly import-clean way and still disagree with the first — so that
section runs the pipeline and compares behaviour instead.
"""
import re
from pathlib import Path

import claim_entries

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
    'app/integrity.py',
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


# --------------------------------------------------------------------------- #
# The behavioural guard — the only test here that runs the pipeline instead of
# reading it. "Single owner for claim normalisation" cannot be proven by import
# rules: the defect this guards against was never an illegal import, it was two
# plausible-looking lines in the extraction path resolving predicates on its own.
# --------------------------------------------------------------------------- #

def test_claim_normalization_has_single_owner(tmp_path):
    """One Claim Draft must compile identically through every claim entry.

    ``KnowledgeCompiler`` is the single owner of claim semantics (ADR-011). This is
    that statement executed: the invented predicate ``new_ceo`` — with the change
    baked into its name, the way a model actually writes it — must come out of
    Extraction, Correction and Research as the same canonical ``has_ceo``, with
    ``new`` moved into ``temporal_signal``, and with no other field differing
    either.
    """
    claim_entries.prepare_database(tmp_path)
    claim_entries.seed_entities(claim_entries.ENTITIES)

    produced = {name: entry(claim_entries.DRAFT, claim_entries.ENTITIES)
                for name, entry in claim_entries.ENTRIES.items()}

    predicates = {c['predicate'] for c in produced.values()}
    signals = {c['temporal_signal'] for c in produced.values()}
    assert predicates == {'has_ceo'}, f'entries disagree on the predicate: {produced}'
    assert signals == {'new'}, f'entries disagree on the temporal signal: {produced}'

    reference = produced['extraction']
    for name, claim in produced.items():
        assert claim == reference, f'{name} produced a different canonical claim'


def test_new_ceo_reaches_the_stored_claim_through_every_entry(tmp_path):
    """The "苹果的新任 CEO" case as a global ontology contract, not a Correction bug.

    Seeded knowledge is ``苹果公司 has_ceo 蒂姆·库克``. The model then reports
    "苹果的新任 CEO 是约翰·特努斯" and encodes the change as a *predicate*
    (``new_ceo``). Whichever entry receives that draft, it must not survive: every
    route has to compile it to the same ``has_ceo`` + ``temporal_signal=new``,
    because only then does candidate retrieval find the stored claim and offer to
    supersede it — instead of filing a second, unrelated CEO fact beside the first.

    This began as a Correction-specific failure. Asserting it at the compiler
    boundary, for all three entries, is what stops it returning through a
    different door.
    """
    claim_entries.prepare_database(tmp_path)
    claim_entries.seed_entities(claim_entries.ENTITIES + (claim_entries.INCUMBENT_ENTITY,))
    incumbent_id = claim_entries.seed_claim(claim_entries.INCUMBENT_CLAIM)

    from app.repositories.claim_repo import ClaimRepository
    from app.repositories.entity_repo import EntityRepository

    apple_id = EntityRepository().by_name('苹果公司')['id']
    claims = ClaimRepository()
    assert [c['object_text'] for c in claims.related(
        subject_id=apple_id, predicate='has_ceo', exclude_id='', limit=10)] == ['蒂姆·库克']

    for name, entry in claim_entries.ENTRIES.items():
        claim = entry(claim_entries.DRAFT, claim_entries.ENTITIES)
        assert claim['predicate'] == 'has_ceo', f'{name} kept the invented predicate'
        assert claim['temporal_signal'] == 'new', f'{name} lost the temporal signal'
        # The payoff: the resolved predicate addresses the *stored* claim, which is
        # what makes a supersede plan possible at all.
        related = claims.related(subject_id=apple_id, predicate=claim['predicate'],
                                 exclude_id='', limit=10)
        assert [c['id'] for c in related] == [incumbent_id], f'{name} cannot find the incumbent'
