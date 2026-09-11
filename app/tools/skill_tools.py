from __future__ import annotations

import json

from ..skills import describe_skills, read_reference


def list_skills() -> str:
    """List all available LLM-Wiki skills and their reference documents.

    Use this to discover which skill contract or reference registry to load.
    """
    return json.dumps(describe_skills(), ensure_ascii=False)


def read_skill_reference(skill: str, reference: str) -> str:
    """Load a reference document that belongs to a skill, on demand.

    :param skill: Skill name, for example knowledge-extraction or knowledge-curation.
    :param reference: Reference file name such as entity-types.md, claim-predicates.md, extraction-v2.md, relation-predicates.md, evidence.md, deduplication.md, entity-resolution.md or normalization.md.
    """
    try:
        return read_reference(skill, reference)
    except ValueError as exc:
        return json.dumps({'error': str(exc)}, ensure_ascii=False)
