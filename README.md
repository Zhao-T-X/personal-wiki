# LLM-Wiki v0.1 — implemented

A local-first, LLM-native personal knowledge base built on SQLite.

## What this version does

**Raw layer**: preserves original Markdown/TXT/HTML documents and exact chunk offsets.

**Knowledge layer**: extracts candidate entities, claims, relations, ideas and questions with provenance.

**Retrieval layer**: SQLite FTS5 lexical search plus optional OpenAI embedding search with a NumPy cosine-similarity fallback. Hybrid ranking uses reciprocal-rank fusion.

**Reasoning layer**: evidence-grounded Q&A with document/chunk citations.

**Graph layer**: entity neighborhood and global graph APIs.

**Operations**: candidate review/status changes, extraction audit records, CRUD, import, export, statistics, smoke test and additive migration helper.

## Data model

```text
Document (source of truth)
    ↓
Chunk (exact offsets)
    ├── FTS5
    └── embedding
    ↓
LLM candidate JSON
    ↓
validation → normalization → entity resolution → provenance
    ↓
Entity / Claim / Relation / Idea / Question
    ↓
Hybrid Retrieval / Graph / RAG
```

Original documents are never replaced by structured output.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/init_db.py
uvicorn app.main:app --reload
```

Windows:

```bat
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python scripts\init_db.py
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

Without `OPENAI_API_KEY`, note creation, file import, chunking, FTS search and graph/review still work. LLM extraction, answers and embeddings require the configured OpenAI-compatible endpoint.

## API

- `GET /api/health`
- `GET /api/stats`
- `POST /api/documents`
- `PUT /api/documents/{id}`
- `DELETE /api/documents/{id}`
- `GET /api/documents`
- `GET /api/documents/{id}`
- `POST /api/documents/import`
- `POST /api/documents/{id}/index/local`
- `POST /api/documents/{id}/index`
- `POST /api/documents/{id}/embed`
- `GET/POST /api/search`
- `GET /api/entities`
- `GET /api/entities/{id}`
- `GET /api/entities/{id}/graph`
- `GET /api/graph`
- `GET /api/claims`
- `GET /api/relations`
- `GET /api/review`
- `PATCH /api/knowledge/{kind}/{id}/status`
- `GET /api/extractions`
- `POST /api/ask`
- `GET /api/export`

## LLM flow

LLM output is not trusted blindly:

```text
chunk batch
  → structured JSON candidate
  → JSON Schema validation
  → predicate normalization
  → exact/alias/fuzzy entity resolution
  → source chunk validation
  → optional exact evidence quote localization
  → candidate persistence
```

Knowledge objects are initially `candidate`; the UI can promote entities/claims/relations to `verified` or reject them.

## Vector retrieval

Embeddings are optional. When available, chunks are stored as float32 blobs in SQLite. Search loads those vectors and computes cosine similarity in NumPy, which is deliberately simple for personal-scale datasets. A future sqlite-vec adapter can replace this without changing the document/ontology schema.

## Backups

The canonical store is `data/wiki.db`. You can also create a portable JSON snapshot:

```bash
python scripts/export_json.py
```

For a full backup, copy the SQLite file while the app is stopped or use SQLite's backup API.

## Tests

```bash
pytest -q
```

The test suite covers chunk offsets, FTS, entity resolution, provenance, search fallback and JSON Schema handling.

## AgentScope 2.x

LLM-Wiki optionally integrates AgentScope 2.x as the agent execution layer. The built-in AgentScope agent uses the configured OpenAI-compatible LLM and exposes LLM-Wiki search/entity/graph tools; it does not access SQLite directly. Enable or disable it from **Settings → Enable AgentScope**. AgentScope 2.x currently requires Python >= 3.11. citeturn0search1turn0search4

## AgentScope multi-agent architecture

AgentScope 2.x is used as the execution layer. The single PersonalAgent has been split into five roles:

- PersonalAgent — front-door general assistant
- KnowledgeAgent — knowledge lookup, entities, relations, evidence
- ResearchAgent — multi-step research and synthesis
- CuratorAgent — deduplication, contradiction and quality recommendations
- ReviewAgent — candidate review assistance

`POST /api/agent/ask` accepts `role=auto|personal|knowledge|research|curator|review`.
`GET /api/agent/roles` lists available roles.


## Knowledge standards

The normative extraction and ontology standards are maintained under [`docs/standards/`](docs/standards/README.md). Start with the ontology overview and Extraction Standard v2.0.

## Agent prompts and Skills

Agent behavior is configurable from **Prompts** in the web UI. Each role has an immutable Core Contract, an optional local Skill with references, and an editable custom prompt. Prompt profiles are stored in the same SQLite database as the knowledge base (`agent_prompt_profiles` and `agent_prompt_versions`). Recent versions are retained for rollback/reset; legacy JSON prompt profiles are migrated once on first initialization.

The prompt/skill architecture is documented in [`docs/standards/13-AGENT-PROMPT-SKILL-STANDARD.md`](docs/standards/13-AGENT-PROMPT-SKILL-STANDARD.md).
