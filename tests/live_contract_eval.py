"""Three-level evaluation of a live extraction case (Step 13.1).

The Step 13 report conflated three different things and called the mix
"Predicate Resolution Accuracy = 100%". It was not: `is` is a registered predicate, so
the *structural* and *registry* layers passed while the *semantic* layer failed. This
module keeps the three layers apart, because they fail for different reasons and are
fixed in different places.

    Structural compliance  the envelope is legal: it compiled, evidence was attached,
                           nothing was rejected (Step 11 stayed invisible)
    Registry compliance    what the model proposed resolved to a registered predicate,
                           an allowed entity type, an allowed object_kind
    Semantic correctness   the proposal actually means what the sentence means:
                           the specific canonical predicate (not a generic one), the
                           right side of the relationship, the right object_kind

A case only counts as semantically correct when the third layer passes too.
"""
from __future__ import annotations

from app.object_classification import classify_object
from app.ontology import claim_predicate_spec, relation_spec

STRUCTURAL = 'structural'
REGISTRY = 'registry'
SEMANTIC = 'semantic'


def _entity_index(envelope: dict) -> dict[str, str]:
    index: dict[str, str] = {}
    for entity in envelope['entities']:
        index[entity['name'].casefold()] = entity['name']
        for alias in entity.get('aliases') or []:
            index.setdefault(alias.casefold(), entity['name'])
    return index


def object_class(claim: dict, index: dict[str, str]) -> str | None:
    """The class persistence would store (same code path as app.knowledge)."""
    text = (claim.get('object') or '').strip()
    if not text:
        return None
    return classify_object(text, known_entity=text.casefold() in index,
                           object_kind=claim.get('object_kind'))[0]


def registered(predicate: str | None) -> bool:
    return bool(predicate) and (claim_predicate_spec(predicate) is not None
                                or relation_spec(predicate) is not None)


def _mentions(claims: list[dict], needle: str) -> list[dict]:
    low = needle.casefold()
    return [c for c in claims
            if low in (c.get('subject') or '').casefold() or low in (c.get('object') or '').casefold()]


def evaluate_case(case: dict, envelope: dict, counts: dict, failures: list) -> dict:
    """Return ``{'checks': [...], 'by_layer': {...}, 'passed': {...}}``."""
    claims = envelope['claims']
    index = _entity_index(envelope)
    declared = {name.casefold() for name in index.values()}
    inv = case.get('invariants') or {}
    gold = case.get('semantic_expected') or {}
    checks: list[dict] = []

    def add(layer: str, name: str, ok: bool, detail: str = '') -> None:
        checks.append({'layer': layer, 'check': name, 'ok': bool(ok), 'detail': detail})

    # ---------------------------------------------------------------- structural
    if 'min_claims' in inv:
        add(STRUCTURAL, 'min_claims', len(claims) >= inv['min_claims'], f'claims={len(claims)}')
    if inv.get('evidence_required'):
        missing = [c.get('subject') for c in claims if not (c.get('evidence_quote') or '').strip()]
        add(STRUCTURAL, 'evidence_present', not missing, f'missing={missing}')
        add(STRUCTURAL, 'evidence_located', counts.get('imprecise_quotes', 0) == 0,
            f'imprecise_quotes={counts.get("imprecise_quotes", 0)}')
    if inv.get('no_rejected_items'):
        bad = [r.get('error_code') for r in failures]
        add(STRUCTURAL, 'no_rejected_items', not bad, f'rejected={bad}')
    if 'no_literal_object' in inv:
        bad = [c.get('object') for text in inv['no_literal_object'] for c in _mentions(claims, text)
               if object_class(c, index) == 'literal' and text.casefold() in (c.get('object') or '').casefold()]
        add(STRUCTURAL, 'no_literal_object', not bad, f'literal_objects={bad}')
    if 'no_entity_object' in inv:
        bad = [c.get('object') for text in inv['no_entity_object']
               for c in claims if text.casefold() in (c.get('object') or '').casefold()
               and object_class(c, index) == 'entity']
        add(STRUCTURAL, 'no_entity_object', not bad, f'entity_objects={bad}')
    if 'no_declared_entity' in inv:
        bad = [n for n in inv['no_declared_entity'] if n.casefold() in declared]
        add(STRUCTURAL, 'no_declared_entity', not bad, f'bad={bad}')
    if 'referenced_any' in inv:
        hit = any(_mentions(claims, t) for t in inv['referenced_any'])
        add(STRUCTURAL, 'referenced_any', hit, f'want_any={inv["referenced_any"]}')

    # ------------------------------------------------------------------ registry
    if inv.get('predicate_in_registry'):
        bad = [c.get('predicate') for c in claims if not registered(c.get('predicate'))]
        add(REGISTRY, 'predicate_in_registry', not bad, f'unregistered={bad}')
    if 'predicate_not' in inv:
        bad = [c.get('predicate') for c in claims if c.get('predicate') in inv['predicate_not']]
        add(REGISTRY, 'predicate_not', not bad, f'forbidden={bad}')
    if 'predicate_not_containing' in inv:
        bad = [c.get('predicate') for c in claims
               if any(tok in (c.get('predicate') or '') for tok in inv['predicate_not_containing'])]
        add(REGISTRY, 'predicate_not_containing', not bad, f'bad={bad}')
    if 'entity_candidates' in inv:
        hit = [n for n in inv['entity_candidates'] if n.casefold() in declared]
        add(REGISTRY, 'entity_candidates', bool(hit), f'hit={hit}')
    if 'object_kind_in' in inv:
        spec = inv['object_kind_in']
        matched = [c for c in claims if spec['match'].casefold() in (c.get('object') or '').casefold()]
        classes = [object_class(c, index) for c in matched]
        add(REGISTRY, 'object_kind_in', bool(matched) and all(k in spec['kinds'] for k in classes),
            f'classes={classes} allowed={spec["kinds"]}')

    # ------------------------------------------------------------------ semantic
    if 'claim_about' in gold:
        matched = _mentions(claims, gold['claim_about'])
        add(SEMANTIC, 'claim_exists', bool(matched),
            f'about={gold["claim_about"]} subjects={[c.get("subject") for c in claims]}')
        if 'subject_contains' in gold:
            ok = [c for c in matched
                  if gold['subject_contains'].casefold() in (c.get('subject') or '').casefold()]
            add(SEMANTIC, 'subject_is', bool(ok),
                f'subjects={[c.get("subject") for c in matched]} want={gold["subject_contains"]}')
        if 'object_contains' in gold:
            ok = [c for c in matched
                  if gold['object_contains'].casefold() in (c.get('object') or '').casefold()]
            add(SEMANTIC, 'object_is', bool(ok),
                f'objects={[c.get("object") for c in matched]} want={gold["object_contains"]}')
        if 'predicate' in gold:
            got = [c.get('predicate') for c in matched]
            add(SEMANTIC, 'predicate_is', bool(matched) and all(g == gold['predicate'] for g in got),
                f'predicates={got} want={gold["predicate"]}')
        if 'object_kind' in gold:
            got = [object_class(c, index) for c in matched]
            bad = [g for g in got if g != gold['object_kind'] or not g]
            add(SEMANTIC, 'object_kind_is', bool(matched) and not bad,
                f'kinds={got} want={gold["object_kind"]}')
        if 'polarity' in gold:
            got = [c.get('polarity') for c in matched]
            add(SEMANTIC, 'polarity_is', bool(matched) and all(g == gold['polarity'] for g in got),
                f'polarities={got} want={gold["polarity"]}')
        if 'temporal_signal' in gold:
            got = [c.get('temporal_signal') for c in matched]
            add(SEMANTIC, 'temporal_is', bool(matched) and all(g == gold['temporal_signal'] for g in got),
                f'temporal={got} want={gold["temporal_signal"]}')
    if gold.get('abstain_ok'):
        # An abstention case (Step 13.2 §2): extracting nothing is a legitimate outcome,
        # so nothing requires a claim. What is checked is that abstaining did not come at
        # the cost of a broken claim: anything produced must still be compiler-clean.
        add(SEMANTIC, 'abstention_respected', not failures,
            f'claims={len(claims)} rejected={[f.get("error_code") for f in failures]}')
    if 'forbidden_entity' in gold:
        bad = gold['forbidden_entity'].casefold() in declared
        bad = bad or any(object_class(c, index) == 'entity'
                         and gold['forbidden_entity'].casefold() in (c.get('object') or '').casefold()
                         for c in claims)
        add(SEMANTIC, 'not_an_entity', not bad, f'path={gold["forbidden_entity"]}')
    if gold.get('declared_kind_consistency'):
        # A phrase the claim types as `concept`/`literal` must not simultaneously be
        # declared as an Entity: the two statements contradict each other.
        bad = [c.get('object') for c in claims
               if c.get('object_kind') in ('concept', 'literal')
               and (c.get('object') or '').casefold() in declared]
        add(SEMANTIC, 'declared_kind_consistency', not bad,
            f'object_declared_as_entity_but_kinded={bad}')

    by_layer = {layer: [c['ok'] for c in checks if c['layer'] == layer]
                for layer in (STRUCTURAL, REGISTRY, SEMANTIC)}
    return {
        'checks': checks,
        'by_layer': by_layer,
        'passed': {
            STRUCTURAL: all(by_layer[STRUCTURAL]),
            REGISTRY: all(by_layer[REGISTRY]),
            SEMANTIC: all(by_layer[SEMANTIC]),
        },
        'semantic_failures': [c for c in checks if c['layer'] == SEMANTIC and not c['ok']],
    }


def metrics(results: list[dict]) -> dict:
    """Four rates. Abstention cases are reported separately, never in the accuracy
    denominator — an unfalsifiable expectation must not look like a model failure."""
    semantic_cases = [r for r in results if r.get('kind', 'semantic') == 'semantic']
    abstention_cases = [r for r in results if r.get('kind') == 'abstention']

    def rate(rows: list[dict], layer: str) -> str:
        ok = sum(1 for r in rows if r['passed'][layer])
        return f'{ok}/{len(rows)}' if rows else 'n/a'

    semantic_checks = [c for r in semantic_cases for c in r['checks'] if c['layer'] == SEMANTIC]
    return {
        'cases': len(results),
        'semantic_cases': len(semantic_cases),
        'abstention_cases': len(abstention_cases),
        'structural_compliance': rate(results, STRUCTURAL),
        'registry_compliance': rate(results, REGISTRY),
        'semantic_accuracy': rate(semantic_cases, SEMANTIC),
        'abstention_behavior': rate(abstention_cases, SEMANTIC),
        'semantic_checks_passed': f"{sum(1 for c in semantic_checks if c['ok'])}/{len(semantic_checks)}",
        'failed_cases': [r['id'] for r in semantic_cases if not r['passed'][SEMANTIC]],
        'failed_abstention': [r['id'] for r in abstention_cases if not r['passed'][SEMANTIC]],
    }
