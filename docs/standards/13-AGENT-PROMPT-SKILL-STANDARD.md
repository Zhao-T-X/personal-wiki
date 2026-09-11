# Agent Prompt & Skill Standard v1.0

## Purpose

LLM-Wiki Agents use a layered prompt architecture so that user-editable instructions remain flexible without replacing ontology, evidence, tool, or output contracts.

## Layers

1. **Core Contract** — immutable role identity and non-negotiable safety/data rules.
2. **Skill** — reusable task procedure stored under `skills/<name>/SKILL.md`.
3. **References** — detailed registries and standards loaded only for tasks that need them.
4. **Custom Prompt** — user-editable instructions managed from the UI and persisted locally.
5. **Runtime Input** — the current user request and tool/context data.

Effective prompt order is:

```text
Core Contract
  → Skill
  → Optional References
  → User Custom Instructions
  → Runtime Input
```

## Editable Boundary

The UI edits only `custom_prompt`. Users cannot overwrite the Core Contract or Skill files through the prompt editor. Prompt state and history are persisted in SQLite rather than a sidecar JSON file.

Custom instructions may change tone, verbosity, preferred answer structure, workflow preferences, and domain emphasis, but they cannot override source-grounding, provenance, tool contracts, schema requirements, or other non-negotiable rules.

## Persistence

Prompt profiles are stored in the local JSON file configured by `agent_prompts_path` (default `./data/agent_prompts.json`). Each role keeps up to 20 saved versions locally.

## Roles

The built-in roles are:

- PersonalAgent
- KnowledgeAgent
- ResearchAgent
- CuratorAgent
- ReviewAgent
- ExtractionAgent

## Skill Mapping

- ExtractionAgent → `knowledge-extraction`
- KnowledgeAgent → `knowledge-curation`
- ResearchAgent → `knowledge-curation`
- CuratorAgent → `knowledge-curation`
- ReviewAgent → `knowledge-curation`
- PersonalAgent → no mandatory domain skill

## Skill Tools

Skill material can be loaded at runtime instead of embedding every reference into the system prompt:

- Native Agent Skills — each `skills/<name>/SKILL.md` carries YAML frontmatter (`name`, `description`) and is registered through `LocalSkillLoader` when the agent toolkit is built. The agent therefore receives the skill catalogue in its system prompt and can open a skill contract with the built-in skill viewer tool.
- `list_skills()` — list available skills, their purpose, and their reference documents.
- `read_skill_reference(skill, reference)` — load a single reference document (for example `extraction-v2.md`) on demand.

The Skill layer (Core Contract → Skill → References) stays authoritative. The tools only expose the same files; they do not change ontology, evidence, or schema contracts. The available reference set is discovered from `skills/<name>/references/` and is not hard-coded per caller.

## Extraction Pipeline

Knowledge extraction runs on the same Agent runtime as conversation. `ExtractionAgent` executes a reasoning-acting loop, can open the Knowledge Extraction skill and load registries on demand, and must return its result through a structured-output contract.

- The output contract is declared once as Pydantic models in `app/agents/extraction_agent.py`, mirroring `schemas/extraction.schema.json`. AgentScope delivers it through tool calling, so the closed vocabularies (`claim_type`, `polarity`, `modality`, entity/event/question types) are enforced at the protocol level rather than by prompt wording alone.
- The extraction agent is given only skill access (`Skill`, `list_skills`, `read_skill_reference`). It is never handed knowledge-base query tools while extracting a document.
- Keywords in the schema stay deterministic: a malformed extraction is re-run by the agent rather than patched mechanically, and `normalize_extraction` remains the final application-side validator and provenance normalizer.
- The agent loop is asynchronous, so `service.index_document` and the indexing endpoints are `async`.

## Design Principle

Prompt flexibility belongs at the **behavior layer**. Ontology, evidence, schema, and persistence contracts remain deterministic and versioned outside user-editable text.


## SQLite persistence model

Prompt configuration is application state and MUST use the same SQLite database configured by `database_path`. The canonical tables are:

- `agent_prompt_profiles`: one current profile per Agent role.
- `agent_prompt_versions`: append-only recent revisions used for history and restore.

A sidecar JSON file is not the canonical store. Older versions may contain `agent_prompts.json`; on first initialization, compatible records are imported into SQLite and the legacy file is renamed with a `.migrated` suffix when the filesystem permits.
