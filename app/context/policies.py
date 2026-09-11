"""The four context loading policies.

Every piece of information that could enter a context must be classified before
it is allowed in:

``LOAD``           the current task needs it now
``SUMMARIZE``      relevant but too long - shrink it before loading
``RETRIEVE_LATER`` not needed now - keep only an ID and fetch it with a tool
``NEVER_LOAD``     deterministic code handles it, or it is unrelated to the task
"""
from __future__ import annotations

from enum import Enum


class Load(str, Enum):
    LOAD = 'LOAD'
    SUMMARIZE = 'SUMMARIZE'
    RETRIEVE_LATER = 'RETRIEVE_LATER'
    NEVER_LOAD = 'NEVER_LOAD'


# Section names that must never reach the model: deterministic code owns them.
# Kept as a marker list so callers can assert intent explicitly.
NEVER_LOAD_SECTIONS: frozenset[str] = frozenset({
    'sqlite_schema',
    'database_indexes',
    'backend_implementation',
    'ui_configuration',
    'raw_registry',
    'raw_json_schema',
})

# Above this size a section should be summarised rather than loaded verbatim.
SUMMARIZE_THRESHOLD_TOKENS = 1200


def classify(section_name: str, tokens: int, *, required: bool = False,
             summarizable: bool = False, id_only: bool = False) -> Load:
    """Choose the policy for a candidate section."""
    if section_name in NEVER_LOAD_SECTIONS:
        return Load.NEVER_LOAD
    if id_only:
        return Load.RETRIEVE_LATER
    if required and tokens <= SUMMARIZE_THRESHOLD_TOKENS:
        return Load.LOAD
    if summarizable or tokens > SUMMARIZE_THRESHOLD_TOKENS:
        return Load.SUMMARIZE
    return Load.LOAD
