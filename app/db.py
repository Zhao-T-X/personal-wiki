from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import runtime

SCHEMA = r'''
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'note',
  source_uri TEXT,
  content_hash TEXT NOT NULL UNIQUE,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_documents_updated ON documents(updated_at DESC);

CREATE TABLE IF NOT EXISTS chunks (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  start_offset INTEGER NOT NULL,
  end_offset INTEGER NOT NULL,
  -- Incremental processing (§32): the content fingerprint tells us whether a
  -- chunk changed, and extraction_version records which extractor + ontology
  -- produced the knowledge derived from it, so an unchanged chunk is not
  -- re-extracted (and its Claims/Evidence are not thrown away).
  content_hash TEXT,
  extraction_version TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);

CREATE TABLE IF NOT EXISTS entities (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  types_json TEXT NOT NULL DEFAULT '[]',
  name TEXT NOT NULL,
  aliases_json TEXT NOT NULL DEFAULT '[]',
  description TEXT,
  properties_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('draft','candidate','verified','rejected','archived')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name_ci ON entities(lower(name));

CREATE TABLE IF NOT EXISTS entity_aliases (
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  alias TEXT NOT NULL,
  alias_normalized TEXT NOT NULL,
  PRIMARY KEY(entity_id, alias_normalized)
);
CREATE INDEX IF NOT EXISTS idx_entity_alias_normalized ON entity_aliases(alias_normalized);

CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL REFERENCES entities(id),
  predicate TEXT NOT NULL,
  object_id TEXT REFERENCES entities(id),
  object_text TEXT,
  content TEXT,
  context_json TEXT NOT NULL DEFAULT '{}',
  claim_type TEXT NOT NULL DEFAULT 'factual',
  polarity TEXT NOT NULL DEFAULT 'positive',
  modality TEXT NOT NULL DEFAULT 'asserted',
  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('draft','candidate','verified','rejected','archived','superseded')),
  created_by TEXT NOT NULL DEFAULT 'llm',
  source_document_id TEXT NOT NULL REFERENCES documents(id),
  source_chunk_id TEXT NOT NULL REFERENCES chunks(id),
  source_start_offset INTEGER NOT NULL,
  source_end_offset INTEGER NOT NULL,
  source_quote TEXT,
  ontology_version TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_claims_subject ON claims(subject_id);
CREATE INDEX IF NOT EXISTS idx_claims_object ON claims(object_id);
CREATE INDEX IF NOT EXISTS idx_claims_document ON claims(source_document_id);

CREATE TABLE IF NOT EXISTS relations (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES entities(id),
  predicate TEXT NOT NULL,
  target_id TEXT NOT NULL REFERENCES entities(id),
  context_json TEXT NOT NULL DEFAULT '{}',
  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('draft','candidate','verified','rejected','archived')),
  created_by TEXT NOT NULL DEFAULT 'llm',
  source_document_id TEXT NOT NULL REFERENCES documents(id),
  source_chunk_id TEXT NOT NULL REFERENCES chunks(id),
  source_start_offset INTEGER NOT NULL,
  source_end_offset INTEGER NOT NULL,
  source_quote TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rel_source ON relations(source_id);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relations(target_id);
CREATE INDEX IF NOT EXISTS idx_rel_document ON relations(source_document_id);

CREATE TABLE IF NOT EXISTS ideas (
  id TEXT PRIMARY KEY,
  content TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('candidate','accepted','implemented','rejected','archived')),
  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  source_document_id TEXT REFERENCES documents(id),
  source_chunk_id TEXT REFERENCES chunks(id),
  source_start_offset INTEGER,
  source_end_offset INTEGER,
  source_quote TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY,
  content TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','answered','partially_answered','resolved','rejected','archived')),
  source_document_id TEXT REFERENCES documents(id),
  source_chunk_id TEXT REFERENCES chunks(id),
  source_start_offset INTEGER,
  source_end_offset INTEGER,
  source_quote TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  description TEXT NOT NULL,
  participants_json TEXT NOT NULL DEFAULT '[]',
  time_json TEXT NOT NULL DEFAULT '{}',
  location TEXT,
  status TEXT NOT NULL DEFAULT 'unknown',
  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  source_document_id TEXT REFERENCES documents(id),
  source_chunk_id TEXT REFERENCES chunks(id),
  source_start_offset INTEGER,
  source_end_offset INTEGER,
  source_quote TEXT,
  properties_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS research_tasks (
  id TEXT PRIMARY KEY,
  question_id TEXT REFERENCES questions(id) ON DELETE SET NULL,
  question_text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','running','completed','failed')),
  findings TEXT,
  run_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_research_tasks_created ON research_tasks(created_at DESC);

CREATE TABLE IF NOT EXISTS agent_prompt_profiles (
  role TEXT PRIMARY KEY,
  custom_prompt TEXT NOT NULL DEFAULT '',
  active_version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_prompt_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  role TEXT NOT NULL REFERENCES agent_prompt_profiles(role) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  prompt TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(role, version)
);
CREATE INDEX IF NOT EXISTS idx_agent_prompt_versions_role ON agent_prompt_versions(role, version DESC);

CREATE TABLE IF NOT EXISTS llm_runs (
  id TEXT PRIMARY KEY,
  task_type TEXT NOT NULL CHECK(task_type IN ('extract','ask','agent')),
  document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
  agent_role TEXT,
  model TEXT,
  status TEXT NOT NULL CHECK(status IN ('started','success','failed')),
  step_count INTEGER NOT NULL DEFAULT 0,
  summary_json TEXT NOT NULL DEFAULT '{}',
  error_message TEXT,
  duration_ms INTEGER,
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_runs_created ON llm_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_runs_type ON llm_runs(task_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_runs_document ON llm_runs(document_id);

CREATE TABLE IF NOT EXISTS llm_run_steps (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES llm_runs(id) ON DELETE CASCADE,
  step_index INTEGER NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('started','success','failed')),
  input_summary TEXT,
  output_text TEXT,
  error_message TEXT,
  duration_ms INTEGER,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  usage_source TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id, step_index)
);
CREATE INDEX IF NOT EXISTS idx_llm_run_steps_run ON llm_run_steps(run_id, step_index);

-- Claim-to-claim relationships (duplicate / coexists / supersedes / contradicts).
-- New knowledge never overwrites an old claim; the relationship between them is
-- what changes. Both sides CASCADE so removing a claim can never leave a
-- dangling relation behind (or fail a foreign-key check).
CREATE TABLE IF NOT EXISTS claim_relations (
  id TEXT PRIMARY KEY,
  source_claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  target_claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  relationship TEXT NOT NULL CHECK(relationship IN ('duplicate','coexists','supersedes','contradicts','unclear')),
  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  reason TEXT,
  suggested_action TEXT,
  status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('candidate','accepted','rejected')),
  created_by TEXT NOT NULL DEFAULT 'system',
  -- Status the older claim held before a human confirmed it was superseded, so
  -- that decision can be undone exactly instead of guessed at.
  target_previous_status TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_claim_id, target_claim_id, relationship)
);
CREATE INDEX IF NOT EXISTS idx_claim_rel_source ON claim_relations(source_claim_id);
CREATE INDEX IF NOT EXISTS idx_claim_rel_target ON claim_relations(target_claim_id);

CREATE TABLE IF NOT EXISTS context_runs (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  agent_name TEXT NOT NULL,
  budget_tokens INTEGER NOT NULL DEFAULT 0,
  actual_tokens INTEGER NOT NULL DEFAULT 0,
  trimmed_tokens INTEGER NOT NULL DEFAULT 0,
  efficiency REAL,
  over_budget INTEGER NOT NULL DEFAULT 0,
  optimizations_json TEXT NOT NULL DEFAULT '[]',
  withheld_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_context_runs_agent ON context_runs(agent_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_context_runs_run ON context_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_context_runs_created ON context_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS context_sections (
  id TEXT PRIMARY KEY,
  context_run_id TEXT NOT NULL REFERENCES context_runs(id) ON DELETE CASCADE,
  section_index INTEGER NOT NULL,
  name TEXT NOT NULL,
  policy TEXT NOT NULL,
  source TEXT,
  reason TEXT,
  tokens INTEGER NOT NULL DEFAULT 0,
  chars INTEGER NOT NULL DEFAULT 0,
  trimmed INTEGER NOT NULL DEFAULT 0,
  UNIQUE(context_run_id, section_index)
);
CREATE INDEX IF NOT EXISTS idx_context_sections_run ON context_sections(context_run_id, section_index);

CREATE TABLE IF NOT EXISTS task_packets (
  id TEXT PRIMARY KEY,
  goal TEXT,
  context_summary TEXT,
  agent TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  estimated_tokens INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_task_packets_created ON task_packets(created_at DESC);

-- Persisted *compressed* conversation state (Context Runtime P3, spec §7/§10):
-- one row per (conversation, agent) holding the rolling summary of everything
-- that fell out of the recent window, plus the recent window itself. This row
-- is all a new turn needs - full transcripts are never stored or replayed.
CREATE TABLE IF NOT EXISTS conversation_summaries (
  conversation_id TEXT NOT NULL,
  agent TEXT NOT NULL DEFAULT 'PersonalAgent',
  summary TEXT NOT NULL DEFAULT '',
  recent_json TEXT NOT NULL DEFAULT '[]',
  messages_seen INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (conversation_id, agent)
);
CREATE INDEX IF NOT EXISTS idx_conversation_summaries_updated
  ON conversation_summaries(updated_at DESC);

-- Context Cache (Context Runtime P3, spec §10). The cache key is a composite
-- hash of every version the compiled prompt depends on (prompt content, skill /
-- reference / registry / schema versions, history state), so any version change
-- produces a new key and the old entry is invalid *by construction*. One row per
-- distinct key: rows = misses, hits = reuse.
CREATE TABLE IF NOT EXISTS context_cache (
  cache_key TEXT PRIMARY KEY,
  agent TEXT NOT NULL DEFAULT '',
  prompt_json TEXT NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_hit_at TEXT
);

-- Knowledge Operations audit (docs/architecture/KNOWLEDGE-OPERATIONS.md): every
-- knowledge mutation is recorded so it is traceable and, where possible, undoable.
-- The operation *is* the record — claims themselves are never silently overwritten.
CREATE TABLE IF NOT EXISTS knowledge_operations (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'user',
  status TEXT NOT NULL DEFAULT 'applied' CHECK(status IN ('applied','rejected')),
  reason TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_knowledge_operations_created
  ON knowledge_operations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_operations_kind
  ON knowledge_operations(kind, created_at DESC);

DROP TABLE IF EXISTS extraction_runs;

-- Evaluation board (Phase 7): persisted offline eval runs. One row per run;
-- summary/report are JSON (summary = scalar aggregates, report keeps case_details).
CREATE TABLE IF NOT EXISTS evaluation_runs (
  id TEXT PRIMARY KEY,
  suite TEXT NOT NULL,
  created_at TEXT NOT NULL,
  summary TEXT NOT NULL,
  report TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunk_embeddings (
  chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  dims INTEGER NOT NULL,
  embedding_blob BLOB NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_embeddings_model ON chunk_embeddings(model);

-- Embedding cache (§33): keyed by *content*, not by chunk id. Re-chunking a
-- document gives its chunks new ids, so an id-keyed store throws away vectors
-- that are still perfectly valid; the same text always embeds to the same
-- vector for a given model.
CREATE TABLE IF NOT EXISTS embedding_cache (
  content_hash TEXT NOT NULL,
  model TEXT NOT NULL,
  dims INTEGER NOT NULL,
  embedding_blob BLOB NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (content_hash, model)
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
  title,
  content,
  content='documents',
  content_rowid='rowid',
  tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
  INSERT INTO documents_fts(rowid,title,content) VALUES (new.rowid,new.title,new.content);
END;
CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
  INSERT INTO documents_fts(documents_fts,rowid,title,content)
  VALUES ('delete',old.rowid,old.title,old.content);
END;
CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
  INSERT INTO documents_fts(documents_fts,rowid,title,content)
  VALUES ('delete',old.rowid,old.title,old.content);
  INSERT INTO documents_fts(rowid,title,content)
  VALUES (new.rowid,new.title,new.content);
END;
'''


def connect() -> sqlite3.Connection:
    database_path = runtime()['database_path']
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 5000')
    return conn


def _migrate_legacy_entities(conn: sqlite3.Connection) -> None:
    cols = {r['name'] for r in conn.execute("PRAGMA table_info(entities)").fetchall()}
    if 'types_json' in cols:
        return
    conn.execute("ALTER TABLE entities RENAME TO entities_legacy")
    conn.execute("""
        CREATE TABLE entities (
          id TEXT PRIMARY KEY, type TEXT NOT NULL, types_json TEXT NOT NULL DEFAULT '[]',
          name TEXT NOT NULL, aliases_json TEXT NOT NULL DEFAULT '[]', description TEXT,
          properties_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'candidate'
            CHECK(status IN ('draft','candidate','verified','rejected','archived')),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    mapping = {'person':'Person','organization':'Organization','place':'Location','concept':'Concept','project':'Resource'}
    for row in conn.execute('SELECT * FROM entities_legacy').fetchall():
        et = mapping.get(str(row['type']).lower(), 'Resource')
        conn.execute('INSERT INTO entities(id,type,types_json,name,aliases_json,description,properties_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                     (row['id'], et, json.dumps([et]), row['name'], row['aliases_json'], row['description'], row['properties_json'], row['status'], row['created_at'], row['updated_at']))
    conn.execute('DROP TABLE entities_legacy')

def _migrate_legacy_claims(conn: sqlite3.Connection) -> None:
    cols = {r['name'] for r in conn.execute("PRAGMA table_info(claims)").fetchall()}
    if 'claim_type' not in cols:
        conn.execute("ALTER TABLE claims ADD COLUMN claim_type TEXT NOT NULL DEFAULT 'factual'")
    if 'polarity' not in cols:
        conn.execute("ALTER TABLE claims ADD COLUMN polarity TEXT NOT NULL DEFAULT 'positive'")
    if 'modality' not in cols:
        conn.execute("ALTER TABLE claims ADD COLUMN modality TEXT NOT NULL DEFAULT 'asserted'")
    if 'ontology_version' not in cols:
        # Which ontology was in force when this claim was compiled (ADR-015), so a
        # claim that later becomes unrecognisable can be explained, not guessed at.
        conn.execute("ALTER TABLE claims ADD COLUMN ontology_version TEXT")


def _ensure_chunk_columns(conn: sqlite3.Connection) -> None:
    """Add the incremental-processing fingerprints to databases that predate them.

    Existing chunks keep NULL: a NULL hash means "unknown", so the next index run
    treats them as changed and re-extracts once. That is the safe direction —
    guessing "unchanged" would silently keep stale knowledge.
    """
    cols = {r['name'] for r in conn.execute('PRAGMA table_info(chunks)').fetchall()}
    if 'content_hash' not in cols:
        conn.execute('ALTER TABLE chunks ADD COLUMN content_hash TEXT')
    if 'extraction_version' not in cols:
        conn.execute('ALTER TABLE chunks ADD COLUMN extraction_version TEXT')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_chunks_content_hash ON chunks(content_hash)')


def _ensure_document_columns(conn: sqlite3.Connection) -> None:
    """Backfill ``documents`` columns introduced in Phase 6 for older dev databases.

    The current schema declares ``content_hash TEXT NOT NULL UNIQUE`` and
    ``metadata_json TEXT NOT NULL DEFAULT '{}'``. ``CREATE TABLE IF NOT EXISTS``
    leaves an already-existing ``documents`` table untouched, so databases created
    before those columns shipped are missing them. We add the columns and backfill
    ``content_hash`` from ``sha256(content)`` (matching ``document_repo._hash``) so
    the ``NOT NULL UNIQUE`` contract holds for runtime inserts.
    """
    import hashlib

    cols = {r['name'] for r in conn.execute('PRAGMA table_info(documents)').fetchall()}
    if 'metadata_json' not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
    if 'content_hash' not in cols:
        conn.execute('ALTER TABLE documents ADD COLUMN content_hash TEXT')
        seen: set[str] = set()
        for row in conn.execute('SELECT id, content FROM documents').fetchall():
            digest = hashlib.sha256((row['content'] or '').encode('utf-8')).hexdigest()
            # Guarantee the UNIQUE constraint even if two documents share content.
            if digest in seen:
                digest = hashlib.sha256(
                    f"{row['content']}|{row['id']}".encode('utf-8')).hexdigest()
            seen.add(digest)
            conn.execute('UPDATE documents SET content_hash=? WHERE id=?', (digest, row['id']))
        conn.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash)')

def _table_ddl(table: str) -> str:
    """The CREATE TABLE statement for a table, taken from SCHEMA (without IF NOT EXISTS)."""
    marker = f'CREATE TABLE IF NOT EXISTS {table} ('
    start = SCHEMA.index(marker)
    end = SCHEMA.index(');', start) + 2
    return SCHEMA[start:end].replace('IF NOT EXISTS ', '')


def _migrate_nullable_sources(conn: sqlite3.Connection) -> None:
    """Older databases declared ideas/questions/events sources NOT NULL, which
    blocks manually created objects. SQLite cannot drop NOT NULL, so rebuild.

    ``legacy_alter_table`` keeps RENAME from rewriting foreign keys in other
    tables (e.g. research_tasks.question_id) to the temporary ``*_old`` name.
    """
    conn.execute('PRAGMA legacy_alter_table = ON')
    try:
        for table in ('ideas', 'questions', 'events'):
            info = conn.execute(f'PRAGMA table_info({table})').fetchall()
            if not info:
                continue
            doc_col = next((c for c in info if c['name'] == 'source_document_id'), None)
            if not doc_col or not doc_col['notnull']:
                continue
            columns = [c['name'] for c in info]
            cols = ','.join(columns)
            conn.execute(f'DROP TABLE IF EXISTS {table}_old')
            conn.execute(f'ALTER TABLE {table} RENAME TO {table}_old')
            conn.execute(_table_ddl(table))
            conn.execute(f'INSERT INTO {table}({cols}) SELECT {cols} FROM {table}_old')
            conn.execute(f'DROP TABLE {table}_old')
    finally:
        conn.execute('PRAGMA legacy_alter_table = OFF')
    _repair_research_fk(conn)


def _repair_research_fk(conn: sqlite3.Connection) -> None:
    """Fix databases migrated before the legacy_alter_table fix: their
    research_tasks foreign key still points at the dropped questions_old."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='research_tasks'").fetchone()
    if not row or 'questions_old' not in (row['sql'] or ''):
        return
    conn.execute('DROP TABLE research_tasks')
    conn.execute(_table_ddl('research_tasks'))
    conn.execute('CREATE INDEX IF NOT EXISTS idx_research_tasks_created ON research_tasks(created_at DESC)')


_TOKEN_COLUMNS = {
    'llm_runs': {
        'prompt_tokens': 'INTEGER NOT NULL DEFAULT 0',
        'completion_tokens': 'INTEGER NOT NULL DEFAULT 0',
    },
    'llm_run_steps': {
        'prompt_tokens': 'INTEGER',
        'completion_tokens': 'INTEGER',
        'usage_source': 'TEXT',
    },
    # Context Runtime: cost that was deliberately not loaded.
    'context_runs': {
        'withheld_json': "TEXT NOT NULL DEFAULT '[]'",
    },
}


def _ensure_token_columns(conn: sqlite3.Connection) -> None:
    """Add accounting columns to databases created before the Context Runtime."""
    for table, columns in _TOKEN_COLUMNS.items():
        existing = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}')


def _migrate_claim_statuses(conn: sqlite3.Connection) -> None:
    """Allow 'superseded' as a claim status.

    Superseded is history, not error, so it must be distinct from rejected — that
    distinction is what lets retrieval prefer current knowledge while still being
    able to answer historical questions. SQLite cannot alter a CHECK constraint,
    so the table is rebuilt from its own stored DDL, which keeps the column order
    (and therefore `INSERT ... SELECT *`) identical.

    Foreign keys are switched off across the swap: claim_relations and friends
    reference `claims` by name, and the DROP/RENAME would otherwise trip them.
    """
    import re
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='claims'").fetchone()
    ddl = (row['sql'] if row else '') or ''
    if not ddl or 'superseded' in ddl:
        return  # fresh schema already allows it, or nothing to migrate
    if "'rejected','archived'" not in ddl:
        return  # unrecognised shape — leave it alone rather than guess
    rebuilt = re.sub(r'CREATE TABLE\s+(IF NOT EXISTS\s+)?claims\b',
                     'CREATE TABLE claims_migrated', ddl, count=1, flags=re.I)
    rebuilt = rebuilt.replace("'rejected','archived'", "'rejected','archived','superseded'")

    conn.commit()  # PRAGMA foreign_keys is a no-op inside a transaction
    conn.execute('PRAGMA foreign_keys=OFF')
    try:
        conn.executescript(
            f'{rebuilt};\n'
            'INSERT INTO claims_migrated SELECT * FROM claims;\n'
            'DROP TABLE claims;\n'
            'ALTER TABLE claims_migrated RENAME TO claims;\n'
            'CREATE INDEX IF NOT EXISTS idx_claims_subject ON claims(subject_id);\n'
            'CREATE INDEX IF NOT EXISTS idx_claims_object ON claims(object_id);\n'
            'CREATE INDEX IF NOT EXISTS idx_claims_document ON claims(source_document_id);\n')
    finally:
        conn.execute('PRAGMA foreign_keys=ON')


def _ensure_claim_relation_columns(conn: sqlite3.Connection) -> None:
    """Add columns introduced after claim_relations first shipped."""
    existing = {row['name'] for row in conn.execute('PRAGMA table_info(claim_relations)')}
    if existing and 'target_previous_status' not in existing:
        conn.execute('ALTER TABLE claim_relations ADD COLUMN target_previous_status TEXT')


def _migrate_fts_tokenizer(conn: sqlite3.Connection) -> None:
    """Switch documents_fts to the trigram tokenizer.

    unicode61 treats a whole run of CJK characters as a single token, so a Chinese
    document could only be found by a query repeating it verbatim — searching
    "苹果" returned nothing from a document titled "苹果的SEO是乔布斯". trigram
    indexes overlapping three-character windows, which makes substring matching
    work for Chinese as well as it does for English.

    The index is external-content, so 'rebuild' refills it from `documents`
    instead of re-inserting every row by hand.
    """
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='documents_fts'").fetchone()
    ddl = (row['sql'] if row else '') or ''
    if not ddl or 'trigram' in ddl:
        return
    conn.executescript('''
        DROP TABLE IF EXISTS documents_fts;
        CREATE VIRTUAL TABLE documents_fts USING fts5(
          title, content, content='documents', content_rowid='rowid', tokenize='trigram');
    ''')
    conn.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")


def init_db() -> None:
    conn = connect()
    # Fresh DBs use the current schema; old dev DBs are upgraded in-place when possible.
    conn.executescript(SCHEMA)
    try:
        _migrate_legacy_entities(conn)
        _migrate_legacy_claims(conn)
        _ensure_chunk_columns(conn)
        _ensure_document_columns(conn)
        _migrate_nullable_sources(conn)
        _ensure_token_columns(conn)
        _ensure_claim_relation_columns(conn)
        _migrate_fts_tokenizer(conn)
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name_ci ON entities(lower(name))')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_rel_key ON relations(source_id,predicate,target_id)')
        # Runs last: it commits internally to toggle foreign keys safely.
        _migrate_claim_statuses(conn)
    except Exception:
        # A partially initialized old database should not block fresh startup; the next run can retry.
        conn.rollback()
        conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def loads(value, default=None):
    try:
        return json.loads(value)
    except Exception:
        return {} if default is None else default
