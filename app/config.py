from __future__ import annotations
import json, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

SETTINGS_PATH = os.getenv('SETTINGS_PATH', './data/settings.json')
DEFAULTS = {
    'database_path': os.getenv('DATABASE_PATH', './data/wiki.db'),
    'openai_api_key': os.getenv('OPENAI_API_KEY', ''),
    'openai_base_url': os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1'),
    'openai_model': os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
    'openai_embedding_model': os.getenv('OPENAI_EMBEDDING_MODEL', 'text-embedding-3-small'),
    'embedding_dims': int(os.getenv('EMBEDDING_DIMS', '1536')),
    'llm_batch_chunks': int(os.getenv('LLM_BATCH_CHUNKS', '8')),
    'max_search_results': int(os.getenv('MAX_SEARCH_RESULTS', '50')),
    'auto_embed': os.getenv('AUTO_EMBED', 'false').lower() in {'1','true','yes'},
    # Entity Eligibility gate (task: extraction precision). When on, declared entities
    # without claim support / structural garbage (file paths, relation names, pure
    # modifier phrases) are quarantined or dropped before they reach the verified
    # entity pool. Toggle OFF only for A/B experiments (Before run).
    'extraction_eligibility_enabled': os.getenv('EXTRACTION_ELIGIBILITY', 'true').lower() in {'1','true','yes'},
    # Claim Object classification (task: semantic boundary). When on, a free-text claim
    # object is typed as entity/literal/concept/unknown and recorded on the claim, and
    # only *entity*-like objects may enter Object Linking. Toggle OFF for A/B (Before).
    'object_classification_enabled': os.getenv('OBJECT_CLASSIFICATION', 'true').lower() in {'1','true','yes'},
    'agentscope_enabled': os.getenv('AGENTSCOPE_ENABLED', 'true').lower() in {'1','true','yes'},
    # Extraction context mode (Step 15): "agentic" = the model fetches references itself
    # through read_skill_reference (two provider calls per step); "prefetch" = the
    # Context Planner selects the minimal context up front and the agent answers in one
    # call. Switched to "prefetch" after the Step 15 A/B measured 5/5 semantic parity at
    # -60.5% prompt tokens on the five contract cases
    # (tests/fixtures/extraction_cases/step15_ab.json). Set EXTRACTION_CONTEXT_MODE=agentic
    # to roll back — both arms are kept and tested.
    'extraction_context_mode': os.getenv('EXTRACTION_CONTEXT_MODE', 'prefetch').strip().lower(),
    # Pass 1 detection (Step 16 audit): "on" = triage each batch before extracting (the
    # historical behaviour, still the production default); "off" = extract directly, no
    # detection call. The audit switch exists so the A/B can compare the two pipelines
    # with everything else identical. It does NOT change the production default.
    'extraction_detection_mode': os.getenv('EXTRACTION_DETECTION_MODE', 'on').strip().lower(),
    # Extraction skill variant (Step 17 minimality audit): "current" ships
    # skills/knowledge-extraction/SKILL.md; "compact" ships SKILL_COMPACT.md — the same
    # behavioural contract with the registry/schema restatements removed (557 -> 378 est.
    # tokens). A/B only: production stays on "current" until a live canary shows semantic
    # parity (see docs/development/extraction-skill-audit.md).
    'extraction_skill_variant': os.getenv('EXTRACTION_SKILL_VARIANT', 'current').strip().lower(),
    # Per-agent context budgets in tokens, e.g. {"KnowledgeAgent": 2500}.
    # Empty means "use app/context/budget.py::AGENT_BUDGETS".
    'agent_context_budgets': {},
    # Tool Result Compression (Context Runtime P2b): bounds on what the agent
    # tools append to the ReAct context. Ids always survive; only payload size
    # is capped. See app/tools/compression.py.
    'tool_quote_chars': int(os.getenv('TOOL_QUOTE_CHARS', '240')),
    'tool_entity_claims': int(os.getenv('TOOL_ENTITY_CLAIMS', '8')),
    'tool_graph_nodes': int(os.getenv('TOOL_GRAPH_NODES', '25')),
    'tool_batch_size': int(os.getenv('TOOL_BATCH_SIZE', '8')),
}


def get_settings() -> dict:
    data = dict(DEFAULTS)
    try:
        p = Path(SETTINGS_PATH)
        if p.exists():
            loaded = json.loads(p.read_text(encoding='utf-8'))
            if isinstance(loaded, dict):
                data.update(loaded)
    except Exception:
        pass
    return data


def save_settings(updates: dict) -> dict:
    data = get_settings()
    allowed = set(DEFAULTS)
    for key, value in updates.items():
        if key in allowed and value is not None:
            data[key] = value
    p = Path(SETTINGS_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(p)
    return data


# Explicit, in-process overrides that must beat the persisted settings file. Used only
# by the Before/After experiment harness and its tests to force a phase deterministically
# (a settings.json written by an unrelated request must not silently keep a gate on).
# Empty in production, so runtime() behaves exactly as before.
_OVERRIDES: dict = {}


def runtime():
    s = get_settings()
    if _OVERRIDES:
        s.update(_OVERRIDES)
    return s


def llm_test_mode() -> str:
    """LLM test mode: ``live`` | ``replay`` | ``disabled``.

    Defaults to ``live`` (production). The test suite forces ``disabled`` so a test can
    never silently spend money, and ``replay`` serves recorded fixtures instead of a
    real call. Read dynamically so a test fixture can set it per-test.
    """
    return str(_OVERRIDES.get('llm_test_mode') or os.getenv('LLM_TEST_MODE', 'live')).lower()

# Backward-compatible constants for callers that have not yet been migrated.
DATABASE_PATH = DEFAULTS['database_path']
OPENAI_API_KEY = DEFAULTS['openai_api_key']
OPENAI_BASE_URL = DEFAULTS['openai_base_url']
OPENAI_MODEL = DEFAULTS['openai_model']
OPENAI_EMBEDDING_MODEL = DEFAULTS['openai_embedding_model']
EMBEDDING_DIMS = DEFAULTS['embedding_dims']
LLM_BATCH_CHUNKS = DEFAULTS['llm_batch_chunks']
MAX_SEARCH_RESULTS = DEFAULTS['max_search_results']
AUTO_EMBED = DEFAULTS['auto_embed']
