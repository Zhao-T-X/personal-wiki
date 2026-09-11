"""Per-agent context budgets and the compression order used when a budget is exceeded.

Budgets are soft limits: the manager compresses low-value sections first and never
trims the sections listed in ``NEVER_TRIM``.
"""
from __future__ import annotations

DEFAULT_BUDGET = 4000

# Soft budget per agent (tokens). Configurable at runtime through
# ``agent_context_budgets`` in data/settings.json.
AGENT_BUDGETS: dict[str, int] = {
    'PersonalAgent': 4000,
    'KnowledgeAgent': 3000,
    'ResearchAgent': 6000,
    'CuratorAgent': 4000,
    'ReviewAgent': 3500,
    'ExtractionAgent': 3500,
}

# Sections that carry the task itself; trimming these changes behaviour, so they
# are never compressed automatically.
NEVER_TRIM: frozenset[str] = frozenset({
    'bootstrap',      # agent identity + grounding rules
    'task',           # current task / question / source payload
    'custom',         # user instructions - trimming these silently changes intent
    'constraints',    # non-negotiable rules
    'output_contract',  # required output shape
})

# Compression order: lower index is compressed first.
COMPRESSION_ORDER: tuple[str, ...] = (
    'history',
    'examples',
    'knowledge',
    'evidence',
    'references',
    'skill',
    'tools',
    'runtime',
)


def budget_for(agent_name: str, overrides: dict[str, int] | None = None) -> int:
    """Return the soft token budget for an agent, honouring user overrides."""
    if overrides:
        value = overrides.get(agent_name)
        if isinstance(value, int) and value > 0:
            return value
    return AGENT_BUDGETS.get(agent_name, DEFAULT_BUDGET)


def compression_rank(section_name: str) -> int:
    """Rank a section for compression; unknown sections are compressed last."""
    try:
        return COMPRESSION_ORDER.index(section_name)
    except ValueError:
        return len(COMPRESSION_ORDER)


def compressible(section_name: str) -> bool:
    """True when the manager may automatically compress this section."""
    return section_name not in NEVER_TRIM
