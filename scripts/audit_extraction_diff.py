"""Step 10.1 Extraction Recall Audit — read-only.

Explains the real-document change (Entity 65->34, Claim 101->62) entity by entity and
claim-delta by source, WITHOUT touching business code and WITHOUT calling an LLM.

Inputs : tests/golden_corpus/semantic_before.json / semantic_after.json
Outputs: tests/golden_corpus/semantic_diff.json
         tests/golden_corpus/entity_recall_audit.json
         tests/golden_corpus/claim_recall_audit.json
         tests/golden_corpus/recall_ground_truth.json

Every removed Entity carries a human classification (noise|structural|domain_term|
valid_concept|valid_entity|unclear) and a keep|remove|unclear ground-truth label.
Claims are attributed to *source* (aborted document vs contract/filter), because the
saved snapshots do not carry per-claim detail — stated explicitly, not hidden.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GC = ROOT / 'tests' / 'golden_corpus'

# --- human ground truth for every removed Entity --------------------------------
# classification: noise | structural | domain_term | valid_concept | valid_entity | unclear
# expected      : keep | remove | unclear   (keep = has future knowledge value)
GROUND_TRUTH: dict[str, dict] = {
    # --- document structure (section headings / titles) -> remove
    'Deterministic post-processing': {'classification': 'structural', 'expected': 'remove', 'reason': 'document section heading, not a knowledge object'},
    'Extraction procedure': {'classification': 'structural', 'expected': 'remove', 'reason': 'document section heading'},
    'Repair policy': {'classification': 'structural', 'expected': 'remove', 'reason': 'document section heading'},
    'LLM-Wiki Extraction Standard v2.0': {'classification': 'unclear', 'expected': 'unclear', 'reason': 'document title; a bound "Standard" object could be legitimate'},
    # --- source artefacts (paths / files) -> remove
    'app/domain/operations.py': {'classification': 'structural', 'expected': 'remove', 'reason': 'file path, not a domain entity'},
    'app/retrieval.py::_current_claims': {'classification': 'structural', 'expected': 'remove', 'reason': 'path::symbol reference'},
    'app/claim_relations.py': {'classification': 'structural', 'expected': 'remove', 'reason': 'file path'},
    'EXTENSION-POINTS.md': {'classification': 'structural', 'expected': 'remove', 'reason': 'file reference'},
    # --- relation / schema metadata -> remove
    'supersedes': {'classification': 'structural', 'expected': 'remove', 'reason': 'relation/predicate name, not an entity'},
    'ADR-005': {'classification': 'unclear', 'expected': 'unclear', 'reason': 'document id; "decision" objects can be knowledge'},
    'Claim Predicate Registry': {'classification': 'domain_term', 'expected': 'unclear', 'reason': 'named system component; component vs concept is a judgement call'},
    'Relation Predicate Registry': {'classification': 'domain_term', 'expected': 'unclear', 'reason': 'named system component'},
    'Relation Registry': {'classification': 'domain_term', 'expected': 'unclear', 'reason': 'named system component'},
    'Entity Type Registry': {'classification': 'domain_term', 'expected': 'unclear', 'reason': 'named system component'},
    'Claim Predicate': {'classification': 'domain_term', 'expected': 'unclear', 'reason': 'ontology meta-term; core concept of THIS project'},
    # --- ontology meta-terms: kept somewhere, removed elsewhere -> NOT a global loss
    'Entity': {'classification': 'domain_term', 'expected': 'unclear', 'reason': 'core ontology term; still present in DOMAIN-MODEL after run'},
    'Claim': {'classification': 'domain_term', 'expected': 'unclear', 'reason': 'core ontology term; still present after run'},
    'Relation': {'classification': 'domain_term', 'expected': 'unclear', 'reason': 'core ontology term; still present after run'},
    'Event': {'classification': 'domain_term', 'expected': 'unclear', 'reason': 'core ontology term; still present after run'},
    'Idea': {'classification': 'domain_term', 'expected': 'unclear', 'reason': 'core ontology term; still present after run'},
    'Question': {'classification': 'domain_term', 'expected': 'unclear', 'reason': 'core ontology term; still present after run'},
    # --- abstract process nouns -> unclear / possibly valid concept
    'Deduplication': {'classification': 'domain_term', 'expected': 'unclear', 'reason': 'abstract process; no stable identity as a knowledge object'},
    'Verification': {'classification': 'domain_term', 'expected': 'unclear', 'reason': 'abstract process'},
    'Current Knowledge': {'classification': 'valid_concept', 'expected': 'unclear', 'reason': 'a defined layer concept; borderline keep'},
    'Static Context': {'classification': 'valid_concept', 'expected': 'unclear', 'reason': 'design concept from the spec; dropped when the model collapsed the three tiers'},
    'Dynamic Context': {'classification': 'valid_concept', 'expected': 'unclear', 'reason': 'design concept'},
    'On-Demand Context': {'classification': 'valid_concept', 'expected': 'unclear', 'reason': 'design concept'},
    'Progressive Disclosure': {'classification': 'valid_concept', 'expected': 'unclear', 'reason': 'design concept'},
    'Extraction Agent': {'classification': 'unclear', 'expected': 'unclear', 'reason': 'naming variant: "ExtractionAgent" is present after the run'},
    # --- genuine technical objects -> keep (recall risk if filter-caused)
    'LLM': {'classification': 'valid_entity', 'expected': 'keep', 'reason': 'a real technology object; kept in one doc, dropped in others (variance)'},
    'ClaimStateResolver': {'classification': 'valid_entity', 'expected': 'keep', 'reason': 'a code class; legitimate Technical Entity — removed by the DOMAIN-MODEL run (filter/contract effect)'},
    'Operation': {'classification': 'valid_entity', 'expected': 'keep', 'reason': 'registered operation concept; loss is from the ABORTED doc, not filtering'},
    'CREATE': {'classification': 'valid_entity', 'expected': 'keep', 'reason': 'registered operation name; aborted-doc loss'},
    'CORRECT': {'classification': 'valid_entity', 'expected': 'keep', 'reason': 'registered operation name; aborted-doc loss'},
    'MERGE': {'classification': 'valid_entity', 'expected': 'keep', 'reason': 'registered operation name; aborted-doc loss'},
    'SPLIT': {'classification': 'valid_entity', 'expected': 'keep', 'reason': 'registered operation name; aborted-doc loss'},
    'SUPERSEDE': {'classification': 'valid_entity', 'expected': 'keep', 'reason': 'registered operation name; aborted-doc loss'},
    'CONTRADICT': {'classification': 'valid_entity', 'expected': 'keep', 'reason': 'registered operation name; aborted-doc loss'},
    'ARCHIVE': {'classification': 'valid_entity', 'expected': 'keep', 'reason': 'registered operation name; aborted-doc loss'},
    'RESTORE': {'classification': 'valid_entity', 'expected': 'keep', 'reason': 'registered operation name; aborted-doc loss'},
    'OperationRequest': {'classification': 'valid_entity', 'expected': 'keep', 'reason': 'a DTO Class — exactly the Technical Entity the contract protects; aborted-doc loss'},
    'OperationResult': {'classification': 'valid_entity', 'expected': 'keep', 'reason': 'a DTO Class; aborted-doc loss'},
    'Claim Evolution': {'classification': 'valid_concept', 'expected': 'unclear', 'reason': 'named concept; still present after the run'},
}

ABORTED_DOC = 'docs/architecture/KNOWLEDGE-OPERATIONS.md'


def _load(name: str) -> dict:
    return json.loads((GC / name).read_text(encoding='utf-8'))


def _doc_map(snapshot: dict) -> dict[str, dict]:
    return {d['doc']: d for d in snapshot['documents']}


def main() -> int:
    before, after = _load('semantic_before.json'), _load('semantic_after.json')
    bmap, amap = _doc_map(before), _doc_map(after)

    # --- entity diff -----------------------------------------------------------
    per_doc, removed_global, added_global = [], set(), set()
    before_all, after_all = set(), set()
    for doc, bd in bmap.items():
        ad = amap.get(doc, {})
        bset = set(bd.get('entities') or [])
        aset = set(ad.get('entities') or []) if 'error' not in ad else set()
        before_all |= bset
        after_all |= aset
        per_doc.append({
            'doc': doc, 'error': ad.get('error'),
            'before_entities': sorted(bset), 'after_entities': sorted(aset),
            'removed': sorted(bset - aset), 'added': sorted(aset - bset),
            'kept_both': sorted(bset & aset),
            'before_claims': bd.get('claims'), 'after_claims': ad.get('claims'),
        })
        removed_global |= (bset - aset)
    removed_global = sorted(before_all - after_all)
    added_global = sorted(after_all - before_all)

    # --- entity recall audit ---------------------------------------------------
    audit = []
    for name in removed_global:
        g = GROUND_TRUTH.get(name, {'classification': 'unclear', 'expected': 'unclear',
                                    'reason': 'not individually reviewed'})
        docs = [d['doc'] for d in per_doc if name in d['removed']]
        audit.append({'name': name, 'classification': g['classification'],
                      'expected': g['expected'], 'reason': g['reason'],
                      'removed_from_documents': docs,
                      'from_aborted_document': docs == [ABORTED_DOC]})

    by_class: dict[str, int] = {}
    by_expected: dict[str, int] = {}
    for a in audit:
        by_class[a['classification']] = by_class.get(a['classification'], 0) + 1
        by_expected[a['expected']] = by_expected.get(a['expected'], 0) + 1
    aborted_only = [a['name'] for a in audit if a['from_aborted_document']]
    filter_caused = [a['name'] for a in audit if not a['from_aborted_document']]
    recall_risk = [a['name'] for a in audit if a['expected'] == 'keep' and not a['from_aborted_document']]

    # --- claim delta attribution (no per-claim detail is stored) ---------------
    claim_rows, aborted_claims = [], 0
    for d in per_doc:
        b, a = d['before_claims'], d['after_claims']
        if a is None:
            a = 0
        delta = a - (b or 0)
        attribution = 'document_aborted' if d['error'] else 'not_reextracted_or_contract'
        if d['error']:
            aborted_claims = (b or 0)
        claim_rows.append({'doc': d['doc'], 'before_claims': b, 'after_claims': a,
                           'delta': delta, 'attribution': attribution})
    claim_total_before = sum((d['before_claims'] or 0) for d in per_doc)
    claim_total_after = sum((d['after_claims'] or 0) for d in per_doc)

    # --- write artifacts -------------------------------------------------------
    (GC / 'semantic_diff.json').write_text(json.dumps({
        'source': {'before': 'semantic_before.json', 'after': 'semantic_after.json'},
        'note': ('Before and After are two INDEPENDENT LLM runs. Deltas therefore mix '
                 '(a) contract/filter effects, (b) model run-to-run variance, and '
                 '(c) one aborted document. Claim-level detail was not captured.'),
        'documents': per_doc,
        'entities': {'removed_global': removed_global, 'added_global': added_global,
                     'removed_count': len(removed_global), 'added_count': len(added_global)},
        'claims': {'before_total': claim_total_before, 'after_total': claim_total_after,
                   'delta': claim_total_after - claim_total_before, 'by_document': claim_rows},
        'global_counts': {'before': before['global'], 'after': after['global']},
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    (GC / 'entity_recall_audit.json').write_text(json.dumps({
        'totals': {'db_distinct_before': before['global']['entity_total'],
                   'db_distinct_after': after['global']['entity_total'],
                   'db_distinct_delta': after['global']['entity_total'] - before['global']['entity_total'],
                   'removed_name_union': len(removed_global)},
        'by_classification': by_class, 'by_expected': by_expected,
        'from_aborted_document': aborted_only, 'filter_caused': filter_caused,
        'recall_risk_keep_filter_caused': recall_risk,
        'entities': audit,
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    (GC / 'claim_recall_audit.json').write_text(json.dumps({
        'limitation': ('semantic_before/after.json store only per-document claim COUNTS, '
                       'not per-claim subject/predicate/object. A claim-by-claim audit is '
                       'therefore not possible from these artifacts; the deltas are '
                       'attributed to source instead.'),
        'totals': {'before': claim_total_before, 'after': claim_total_after,
                   'removed': claim_total_before - claim_total_after},
        'attribution': {'document_aborted_not_reextracted': aborted_claims,
                        'contract_or_reextraction_variance': (claim_total_before - claim_total_after) - aborted_claims},
        'by_document': claim_rows,
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    gt = [{'id': a['name'], 'type': 'entity', 'expected': a['expected'],
           'classification': a['classification'], 'reason': a['reason'],
           'source_evidence': '; '.join(a['removed_from_documents'])} for a in audit]
    (GC / 'recall_ground_truth.json').write_text(json.dumps({
        'definition': {'keep': 'has future Knowledge/Search/QA/Research value',
                       'remove': 'document structure / path / relation or schema metadata / noise',
                       'unclear': 'cannot be decided from the standard alone'},
        'labels': gt}, ensure_ascii=False, indent=2), encoding='utf-8')

    # --- object_kind audit -----------------------------------------------------
    # The per-document object_classes in the snapshot came from the deterministic
    # classifier (no LLM kind), the global one from the persisted claims (with kind).
    # Sample every object the deterministic guardrail called `literal` and check it.
    kind_errors = []
    literal_samples = []
    for doc in after['documents']:
        for text, cls in (doc.get('object_classes') or {}).items():
            if cls == 'literal':
                literal_samples.append({'doc': doc['doc'], 'text': text})
                # A "/" only marks a path when it is a path-ish token, not an enumeration.
                if '/' in text and not text.strip().startswith(('http', '/')) and ' ' in text:
                    kind_errors.append({'doc': doc['doc'], 'text': text,
                                        'detected': 'literal',
                                        'likely_intended': 'concept',
                                        'cause': 'slash_guardrail: any "/" treated as a path'})
    (GC / 'object_kind_audit.json').write_text(json.dumps({
        'distribution_after': after['global'].get('object_classes'),
        'note': ('object_kind is now declared by the LLM and validated by code. Two error '
                 'classes were found by sampling: (1) the deterministic slash guardrail marks '
                 'any text containing "/" as a source artefact -> literal (wrong for '
                 'enumerations); (2) some very long enumerations were declared literal by the '
                 'model itself. Recorded, not fixed (Step 10.1 is audit-only).'),
        'literal_sample_count': len(literal_samples),
        'object_kind_errors': kind_errors,
        'error_count': len(kind_errors),
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    # --- report ----------------------------------------------------------------
    print('== Entity: DB distinct ==')
    print(f"  before={before['global']['entity_total']}  after={after['global']['entity_total']}  "
          f"delta={after['global']['entity_total']-before['global']['entity_total']}")
    print(f"  removed name-union={len(removed_global)}  added={len(added_global)}  "
          f"(union > distinct delta because Before had cross-doc duplicate names)")
    print('  removed by classification:', by_class)
    print('  removed by expected     :', by_expected)
    print(f"  from ABORTED doc only   : {len(aborted_only)} -> {aborted_only}")
    print(f"  filter-caused           : {len(filter_caused)}")
    print(f"  RECALL RISK (keep & filter-caused): {recall_risk}")
    print('\n== Entity eligibility (after) ==', after['global'].get('entity_eligibility'))
    print('== object_kind (after)       ==', after['global'].get('object_classes'))
    print('\n== Claim ==')
    print(f"  before={claim_total_before}  after={claim_total_after}  removed={claim_total_before-claim_total_after}")
    print(f"  aborted-doc claims lost : {aborted_claims}")
    print(f"  contract/variance       : {(claim_total_before-claim_total_after)-aborted_claims}")
    for r in claim_rows:
        print(f"    {r['doc']}: {r['before_claims']} -> {r['after_claims']} ({r['delta']:+}) [{r['attribution']}]")
    print('\nartifacts written to tests/golden_corpus/{semantic_diff,entity_recall_audit,claim_recall_audit,recall_ground_truth}.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
