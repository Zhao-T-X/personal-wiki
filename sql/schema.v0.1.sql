-- LLM-Wiki canonical SQLite schema (v0.1 + llm run records).
-- The executable copy is app/db.py::SCHEMA.
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

-- v0.2: extraction_runs was replaced by llm_runs + llm_run_steps
-- (app/db.py::SCHEMA drops it on init).
