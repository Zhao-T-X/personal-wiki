from app.normalization import claim_to_relation_candidates, derive_relations


def test_direct_trained_on_relation():
    extraction = {
        "entities": [
            {"name":"GPT-4","types":["Model"],"aliases":[]},
            {"name":"MMLU","types":["Dataset"],"aliases":[]},
        ],
        "claims": [{
            "subject":"GPT-4","predicate":"trained_on","object":"MMLU",
            "claim_type":"factual","polarity":"positive","modality":"asserted","context":{},
            "confidence":0.99,"source_chunk":"c1","evidence_quote":"GPT-4 was trained on MMLU."
        }], "events":[], "ideas":[], "questions":[]
    }
    c = claim_to_relation_candidates(extraction)
    assert c[0].outcome == "DIRECT_RELATION"
    assert derive_relations(extraction)[0]["predicate"] == "trained_on"


def test_possible_improves_stays_claim_only():
    extraction = {
        "entities": [
            {"name":"RAG","types":["Technology"],"aliases":[]},
            {"name":"Answer Quality","types":["Concept"],"aliases":[]},
        ],
        "claims": [{
            "subject":"RAG","predicate":"improves","object":"Answer Quality",
            "claim_type":"causal","polarity":"positive","modality":"possible","context":{},
            "confidence":0.96,"source_chunk":"c1","evidence_quote":"RAG may improve answer quality."
        }], "events":[], "ideas":[], "questions":[]
    }
    assert claim_to_relation_candidates(extraction)[0].outcome == "CLAIM_ONLY"
    assert derive_relations(extraction) == []


def test_type_constraint_rejects_invalid_training_relation():
    extraction = {
        "entities": [
            {"name":"GPT-4","types":["Model"],"aliases":[]},
            {"name":"Transformer","types":["Technology"],"aliases":[]},
        ],
        "claims": [{
            "subject":"GPT-4","predicate":"trained_on","object":"Transformer",
            "claim_type":"factual","polarity":"positive","modality":"asserted","context":{},
            "confidence":0.99,"source_chunk":"c1","evidence_quote":"GPT-4 was trained on Transformer."
        }], "events":[], "ideas":[], "questions":[]
    }
    assert claim_to_relation_candidates(extraction)[0].outcome == "CLAIM_ONLY"
