"""Tool Result Compression (Context Runtime P2b).

Tool outputs are the one context source that grows *during* a run: every ReAct
step appends the raw return value of a tool to the conversation. Before P2b the
knowledge tools returned whole rows - full chunk text, up to 50 claims, up to
100 graph nodes with descriptions - so two or three tool calls could outweigh
the entire system prompt.

Principles (Context Runtime spec §2/§6/§12):

- minimal sufficient: the model gets what it needs to *decide*, not the record;
- RETRIEVE_LATER: ids always survive compression so the agent can drill down
  with `get_entity` / `get_entities`;
- honest truncation: a compressed payload states what was cut (`truncated`)
  and how to get more (`hint`) instead of silently dropping data.

The compression is deterministic - no LLM in the path - per spec §5
("deterministic logic first, LLM reasoning second").
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from ..config import runtime

# Sentence-ending separators, CJK first: clipping prefers to stop after a
# complete sentence so a quote never ends mid-word when avoidable.
_SEPARATORS = ('。', '；', '！', '？', '.', ';', '!', '?', '\n')


def _limit(key: str, default: int) -> int:
    """Read a tunable from settings, falling back to the built-in default."""
    try:
        return max(1, int(runtime().get(key, default)))
    except Exception:
        return default


def clip(text: Any, limit: int) -> str:
    """Clip ``text`` to about ``limit`` characters on a sentence/word boundary."""
    if not text:
        return ''
    text = str(text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in _SEPARATORS:
        pos = cut.rfind(sep)
        if pos > limit * 0.6:
            return cut[:pos + 1].rstrip() + ' …'
    space = cut.rfind(' ')
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip() + ' …'


def compress_chunks(rows: Iterable[dict], quote_chars: int | None = None) -> list[dict]:
    """Search hits as quote cards: ids + score + a short quote, never the chunk.

    ``content`` (the full chunk body) is deliberately not part of the tool
    result - it is what the L1 evidence level of the QA pipeline already proved
    unnecessary for grounded answering.
    """
    quote_chars = quote_chars or _limit('tool_quote_chars', 240)
    out = []
    for r in rows:
        score = r.get('score')
        out.append({
            'chunk_id': r.get('id'),
            'document_id': r.get('document_id'),
            'title': r.get('title'),
            'source_type': r.get('source_type'),
            'chunk_index': r.get('chunk_index'),
            'score': round(score, 4) if isinstance(score, (int, float)) else score,
            'method': r.get('method'),
            'quote': clip(r.get('content'), quote_chars),
        })
    return out


def compress_claim(c: dict, claim_chars: int = 160) -> dict:
    """One claim as a decision card: predicate + short content + trust fields."""
    out: dict = {
        'id': c.get('id'),
        'predicate': c.get('predicate'),
        'content': clip(c.get('content'), claim_chars),
        'confidence': c.get('confidence'),
        'status': c.get('status'),
    }
    if c.get('subject_name'):
        out['subject'] = c['subject_name']
    obj = c.get('object_name') or c.get('object_text')
    if obj:
        out['object'] = obj
    return out


def compress_entity(e: dict, claims: Iterable[dict] | None = None,
                    max_claims: int | None = None, claims_total: int | None = None,
                    description_chars: int = 280) -> dict:
    """An entity as a summary card, strongest claims first.

    Claims are ranked by the program (verified first, then confidence) so the
    model sees the most trustworthy subset instead of an unordered dump.
    ``claims_total`` is the authoritative claim count (SQL COUNT) - when it
    exceeds what is shown, the card says so and how to drill down.
    """
    max_claims = max_claims if max_claims is not None else _limit('tool_entity_claims', 8)
    card: dict = {
        'entity_id': e.get('id'),
        'name': e.get('name'),
        'type': e.get('type'),
        'status': e.get('status'),
        'description': clip(e.get('description'), description_chars),
    }
    aliases = e.get('aliases') or []
    if aliases:
        card['aliases'] = aliases[:5]
    props = e.get('properties') or {}
    if props:
        encoded = json.dumps(props, ensure_ascii=False)
        # Small structured properties stay; large ones degrade to their keys so
        # one verbose entity cannot flood the context (drill down via ids).
        card['properties'] = props if len(encoded) <= 200 else {'keys': sorted(props)}

    ranked = sorted(claims or [],
                    key=lambda c: (c.get('status') == 'verified', c.get('confidence') or 0),
                    reverse=True)
    shown = [compress_claim(c) for c in ranked[:max_claims]]
    if shown:
        card['claims'] = shown
        total = claims_total if claims_total is not None else len(ranked)
        if total > len(shown):
            card['claims_total'] = total
            card['hint'] = (f'{total - len(shown)} more claims omitted - '
                            'ask for a specific aspect instead of listing all claims')
    return card


def compress_graph(graph: dict, max_nodes: int | None = None) -> dict:
    """Graph neighbourhood without descriptions, deduped and node-capped.

    Node ids survive (drill-down via ``get_entity``); edge payloads shrink to
    predicate + endpoint names + status, which is what graph reasoning needs.
    """
    max_nodes = max_nodes or _limit('tool_graph_nodes', 25)
    nodes_in: list[dict] = graph.get('nodes') or []
    keep = nodes_in[:max_nodes]          # neighborhood() puts the root node first
    ids = {n.get('id') for n in keep}
    nodes = [{'id': n.get('id'), 'name': n.get('name'),
              'type': n.get('type'), 'status': n.get('status')} for n in keep]

    edges: list[dict] = []
    seen: set[tuple] = set()
    for e in graph.get('edges') or []:
        src = e.get('source_id') or e.get('subject_id')
        dst = e.get('target_id') or e.get('object_id')
        if src not in ids or dst not in ids:
            continue
        key = (src, dst, e.get('predicate'), e.get('kind'))
        if key in seen:
            continue
        seen.add(key)
        edge = {'predicate': e.get('predicate'),
                'source': e.get('source_name') or e.get('subject_name'),
                'target': e.get('target_name') or e.get('object_name'),
                'status': e.get('status')}
        if e.get('kind'):
            edge['kind'] = e['kind']
        edges.append(edge)

    out = {'nodes': nodes, 'edges': edges}
    dropped = len(nodes_in) - len(keep)
    if dropped > 0:
        out['truncated'] = dropped
        out['hint'] = (f'{dropped} nodes omitted - increase depth selectively '
                       'or query a specific entity instead')
    return out
