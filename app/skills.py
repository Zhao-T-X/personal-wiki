from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / 'skills'


def _safe_name(value: str) -> str:
    return ''.join(ch for ch in value if ch.isalnum() or ch in {'-', '_'})


def _strip_frontmatter(text: str) -> str:
    """Remove a leading YAML frontmatter block from a skill document.

    SKILL.md files carry frontmatter (name/description) for AgentScope's
    native skill loader, but the prompt contract should contain only the body.
    """
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end != -1:
            return text[end + 4:].lstrip('\n')
    return text


# Named alternative contracts a skill ships *alongside* SKILL.md. Only the extraction
# skill has one: Step 17's minimality audit added `compact` so the short contract can be
# A/B'd against the production one without ever editing SKILL.md in place. An unknown
# variant (or a skill with no variants) falls back to SKILL.md.
SKILL_VARIANTS: dict[str, dict[str, str]] = {
    'knowledge-extraction': {'compact': 'SKILL_COMPACT.md'},
}

# Config key that selects the extraction skill's variant. Read lazily so a test can flip it
# per-run (config._OVERRIDES). Production default is 'current' -> SKILL.md.
_VARIANT_CONFIG_KEY = 'extraction_skill_variant'


def configured_variant(name: str) -> str | None:
    """The variant name ``EXTRACTION_SKILL_VARIANT`` selects for ``name``, if any.

    Returns ``None`` for the default (``current``), for a skill with no variants, or for
    an unknown variant name — every one of those means "ship SKILL.md".
    """
    wanted = str(_runtime().get(_VARIANT_CONFIG_KEY) or 'current').strip().lower()
    if wanted in {'', 'current', 'default'}:
        return None
    return wanted if wanted in SKILL_VARIANTS.get(name, {}) else None


def _skill_file(name: str, variant: str | None = None) -> Path:
    """The contract file to load: the named variant if it exists, else SKILL.md."""
    skill_dir = SKILLS_DIR / _safe_name(name)
    key = variant if variant is not None else configured_variant(name)
    if key and key != 'current':
        alt = SKILL_VARIANTS.get(name, {}).get(key)
        if alt and (skill_dir / alt).exists():
            return skill_dir / alt
    return skill_dir / 'SKILL.md'


def _runtime():
    from .config import runtime
    return runtime()


def skill_source(name: str, variant: str | None = None) -> str:
    """Repo-relative label of the contract file a load will use (trace/debug)."""
    return f'skills/{_safe_name(name)}/{_skill_file(name, variant).name}'


def load_skill(name: str, *, references: Iterable[str] = (), variant: str | None = None) -> str:
    """Load a compact skill contract and optional reference documents.

    SKILL.md is intentionally concise. Reference files are loaded only when the
    agent explicitly declares that it needs them, keeping the default context small.
    ``variant`` picks an alternative contract (Step 17 ``compact``); the default reads the
    variant ``EXTRACTION_SKILL_VARIANT`` names.
    """
    skill_dir = SKILLS_DIR / _safe_name(name)
    skill_file = _skill_file(name, variant)
    if not skill_file.exists():
        raise FileNotFoundError(f'Skill not found: {name}')
    parts = [_strip_frontmatter(skill_file.read_text(encoding='utf-8')).strip()]
    for ref in references:
        ref_path = skill_dir / 'references' / Path(ref).name
        if ref_path.exists():
            parts.append(f"\n\n## Reference: {ref_path.name}\n{ref_path.read_text(encoding='utf-8').strip()}")
    return '\n'.join(p for p in parts if p)


def list_skill_names() -> list[str]:
    """Return the names of all skills that expose a SKILL.md contract."""
    if not SKILLS_DIR.exists():
        return []
    return sorted(
        p.name for p in SKILLS_DIR.iterdir()
        if p.is_dir() and (p / 'SKILL.md').exists()
    )


def skill_purpose(name: str) -> str:
    """Return the one-line purpose declared in a skill contract."""
    skill_file = SKILLS_DIR / _safe_name(name) / 'SKILL.md'
    if not skill_file.exists():
        raise ValueError(f'Skill not found: {name}')
    for line in skill_file.read_text(encoding='utf-8').splitlines():
        if line.strip().lower().startswith('purpose:'):
            return line.split(':', 1)[1].strip()
    return ''


def list_references(name: str) -> list[str]:
    """Return the reference documents shipped with a skill."""
    ref_dir = SKILLS_DIR / _safe_name(name) / 'references'
    if not ref_dir.exists():
        return []
    return sorted(
        p.name for p in ref_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {'.md', '.txt', '.json'}
    )


def describe_skills() -> list[dict[str, Any]]:
    """Return a compact catalogue of available skills for agent discovery."""
    return [
        {'name': n, 'purpose': skill_purpose(n), 'references': list_references(n)}
        for n in list_skill_names()
    ]


def read_skill_contract(name: str, *, variant: str | None = None) -> str:
    """Read a skill's contract body (SKILL.md, or the named variant)."""
    skill_file = _skill_file(name, variant)
    if not skill_file.exists():
        raise ValueError(f'Skill not found: {name}')
    return _strip_frontmatter(skill_file.read_text(encoding='utf-8')).strip()


def read_reference(name: str, reference: str) -> str:
    """Read a single reference document belonging to a skill."""
    ref_path = SKILLS_DIR / _safe_name(name) / 'references' / Path(reference).name
    if not ref_path.exists():
        raise ValueError(f'Reference not found: {name}/{reference}')
    return ref_path.read_text(encoding='utf-8').strip()


def _content_version(path: Path) -> str:
    """Short content hash used as a version/precondition key (cache, trace)."""
    if not path.exists():
        return ''
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def skill_version(name: str, *, variant: str | None = None) -> str:
    """Content version of the contract a load would use (variant-aware).

    The cache key hashes this, so switching the extraction skill's variant invalidates
    the compiled-prompt cache by construction instead of replaying the other contract.
    """
    return _content_version(_skill_file(name, variant))


def reference_version(name: str, reference: str) -> str:
    """Content version of one reference document."""
    return _content_version(SKILLS_DIR / _safe_name(name) / 'references' / Path(reference).name)


def reference_costs(name: str) -> list[tuple[str, int]]:
    """Reference documents shipped with a skill, with their token cost.

    Used by the Context Runtime to report the cost of the references it decided
    *not* to load.
    """
    from .context.tokens import estimate_tokens
    ref_dir = SKILLS_DIR / _safe_name(name) / 'references'
    if not ref_dir.exists():
        return []
    costs: list[tuple[str, int]] = []
    for path in sorted(ref_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in {'.md', '.txt'}:
            costs.append((path.name, estimate_tokens(path.read_text(encoding='utf-8'))))
    return costs


# Cost metadata (Context Runtime P2c, spec §4): every reference declares *when*
# loading it is justified. Deliberately conservative - the P1 extraction baseline
# (zero references inlined, vocabularies carried by the machine schema) must not
# regress, so the thresholds sit above the intent defaults in
# app/runtime/task.py::features_for.
REFERENCE_LOAD_WHEN: dict[str, dict[str, str]] = {
    'knowledge-extraction': {
        'entity-types.md': 'explicit request only - the entity-type enum already ships in the output schema',
        'claim-predicates.md': 'explicit request only - the predicate enum already ships in the output schema',
        'relation-predicates.md': 'conflict detected (conflict_level >= 0.3)',
        'extraction-v2.md': 'complex document (complexity >= 0.7) or after a failed extraction pass',
        'evidence.md': 'evidence-critical task (evidence_requirement >= 0.95)',
    },
    'knowledge-curation': {
        'deduplication.md': 'explicit request only - dedup pass',
        'entity-resolution.md': 'merge work on a complex task (complexity >= 0.7)',
        'normalization.md': 'predicate/relation conflicts (conflict_level >= 0.3)',
    },
}


def reference_meta(name: str) -> list[dict[str, Any]]:
    """Per-reference cost metadata: lazy flag, token cost, load condition."""
    return [
        {'reference': ref, 'lazy': True, 'estimated_tokens': tokens,
         'load_when': REFERENCE_LOAD_WHEN.get(name, {}).get(ref, 'explicit request only')}
        for ref, tokens in reference_costs(name)
    ]


def references_for(skill: str, features: Any) -> list[str]:
    """Deterministic load_when selector: task features -> references to load.

    Only fires on signals that *deviate from the intent defaults* (see the
    threshold comments on REFERENCE_LOAD_WHEN); a default-shaped task loads
    nothing and keeps the P1 baseline. Explicitly requested references are
    merged by the caller (SkillProvider).
    """
    known = set(list_references(skill))
    rules = REFERENCE_LOAD_WHEN.get(skill, {})
    refs: list[str] = []
    conflict = float(getattr(features, 'conflict_level', 0.0) or 0.0)
    complexity = float(getattr(features, 'complexity', 0.0) or 0.0)
    evidence = float(getattr(features, 'evidence_requirement', 0.0) or 0.0)
    for ref, when in rules.items():
        if ref not in known:
            continue
        if conflict >= 0.3 and 'conflict_level' in when:
            refs.append(ref)
        elif complexity >= 0.7 and 'complexity' in when:
            refs.append(ref)
        elif evidence >= 0.95 and 'evidence_requirement' in when:
            refs.append(ref)
    return refs
