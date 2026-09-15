"""Claims and claim-to-claim relationships (Claim Evolution)."""
from __future__ import annotations

import uuid

from ..cache import EpochCache
from ..db import dumps, loads
from .base import Repository, one, row, rows

# Retrieval's compact claim shape (with subject/object names, and its own id so a
# caller can always link back to the claim it is showing).
_CLAIM_COLUMNS = '''c.id,c.content,c.source_quote,c.predicate,c.polarity,c.modality,c.confidence,c.status,
                    c.context_json,s.name subject_name,o.name object_name,c.object_text'''

_JOIN_NAMES = '''FROM claims c JOIN entities s ON s.id=c.subject_id
                 LEFT JOIN entities o ON o.id=c.object_id'''

# Full comparison shape used by Claim Evolution (app/claim_relations.py).
_RELATION_SELECT = '''SELECT c.id, c.subject_id, c.predicate, c.object_id, c.object_text, c.content,
       c.claim_type, c.polarity, c.modality, c.confidence, c.status, c.created_at,
       c.source_document_id, c.source_chunk_id, c.source_start_offset, c.source_end_offset, c.source_quote,
       s.name AS subject_name, o.name AS object_name
FROM claims c
JOIN entities s ON s.id = c.subject_id
LEFT JOIN entities o ON o.id = c.object_id
'''

# Read-through cache for :meth:`ClaimRepository.related` (spec §33). That one
# query is the hot path for both the direct-fact lookup and the correction
# planner, and it is asked the same question many times in a row. Entries carry
# the data epoch they were produced under and die the instant any
# ``Repository.write()`` bumps it, so a hit can never return knowledge that a
# write has already superseded — invalidation is guaranteed by construction, not
# by remembering to clear this cache at every write site.
_RELATED_CACHE = EpochCache(maxsize=512, name='claim_related')


def related_cache_stats() -> dict:
    """Snapshot of the ``related()`` cache: name/size/maxsize/hits/misses/epoch.

    A read-only observability hook (spec §37 board); calling it changes nothing.
    """
    return _RELATED_CACHE.stats()


def _decode(claim: dict) -> dict:
    claim['context'] = loads(claim.pop('context_json', '{}') or '{}', {})
    return claim


class ClaimRepository(Repository):
    # -- reads -------------------------------------------------------------
    def get(self, claim_id: str) -> dict | None:
        with self.read() as conn:
            item = row(conn.execute(
                'SELECT c.*,s.name subject_name,o.name object_name FROM claims c '
                'JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id WHERE c.id=?',
                (claim_id,)))
        if item is None:
            return None
        item['context'] = loads(item.pop('context_json', '{}') or '{}', {})
        return item

    def list(self, limit: int, status: str | None = None) -> list[dict]:
        sql = ('SELECT c.id,c.predicate,c.content,c.object_text,c.context_json,c.confidence,c.status,'
               's.id subject_id,s.name subject_name,o.id object_id,o.name object_name,'
               'c.source_document_id,c.source_chunk_id,c.source_start_offset,c.source_end_offset,c.source_quote '
               'FROM claims c JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id')
        params: list = []
        if status:
            sql += ' WHERE c.status=?'; params.append(status)
        sql += ' ORDER BY c.created_at DESC LIMIT ?'; params.append(limit)
        with self.read() as conn:
            return [_decode(c) for c in rows(conn.execute(sql, params))]

    def for_chunk(self, chunk_id: str, limit: int) -> list[dict]:
        with self.read() as conn:
            return [_decode(c) for c in rows(conn.execute(
                f'SELECT {_CLAIM_COLUMNS} {_JOIN_NAMES} '
                "WHERE c.source_chunk_id=? AND c.status!='rejected' ORDER BY c.confidence DESC LIMIT ?",
                (chunk_id, limit)))]

    def claims_for_chunks(self, chunk_ids: list[str]) -> list[dict]:
        """Every claim extracted from these chunks — one query, not one per chunk.

        The batched counterpart of :meth:`for_chunk`. A knowledge search projects a
        whole page of recalled chunks at once, and asking per chunk would turn a
        single lookup into ten. What the projection needs is carried explicitly
        (ids, chunk, timestamp) rather than reused from the compact retrieval shape.
        """
        ids = [i for i in dict.fromkeys(chunk_ids) if i]
        if not ids:
            return []
        marks = ','.join('?' * len(ids))
        with self.read() as conn:
            return [_decode(c) for c in rows(conn.execute(
                f'''SELECT c.id,c.subject_id,c.predicate,c.object_id,c.object_text,c.content,
                           c.source_quote,c.confidence,c.status,c.created_at,
                           c.source_chunk_id,c.source_document_id,c.context_json,
                           s.name subject_name,o.name object_name
                    {_JOIN_NAMES}
                    WHERE c.source_chunk_id IN ({marks}) AND c.status!='rejected'
                    ORDER BY c.confidence DESC''', ids))]

    def unlinked_object_claims_for_document(self, document_id: str) -> list[dict]:
        """This document's claims whose object is still free text.

        The pipeline-scoped form of :meth:`unlinked_object_claims`: an automatic link
        belongs where its cause is, so it asks about the claims that were just written
        rather than about the whole wiki.
        """
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT c.id,c.subject_id,c.predicate,c.object_text,c.content,
                          c.source_chunk_id,s.name subject_name
                   FROM claims c JOIN entities s ON s.id=c.subject_id
                   WHERE c.source_document_id=? AND c.object_id IS NULL
                     AND TRIM(COALESCE(c.object_text,'')) != '' AND c.status != 'rejected' ''',
                (document_id,)))

    def candidates_for_document(self, document_id: str) -> list[dict]:
        """The still-unaccepted claims that came out of one document.

        Used as *provenance*, not similarity: "which knowledge did this research task
        propose?" is answered by where the claims came from, so the answer cannot
        include loosely related knowledge from elsewhere.
        """
        with self.read() as conn:
            return [_decode(c) for c in rows(conn.execute(
                f'''SELECT c.id,c.subject_id,c.predicate,c.object_id,c.object_text,c.content,
                           c.source_quote,c.confidence,c.status,c.created_at,c.context_json,
                           c.source_chunk_id,c.source_document_id,
                           s.name subject_name,o.name object_name
                    {_JOIN_NAMES}
                    WHERE c.source_document_id=? AND c.status='candidate'
                    ORDER BY c.confidence DESC''', (document_id,)))]

    def by_ids(self, claim_ids: list[str]) -> list[dict]:
        """Full claim rows (with subject/object names) for many ids, in one query.

        An answer names the claims that justify it; reading them one at a time would
        make a three-claim answer cost three round trips for no reason.
        """
        ids = [i for i in dict.fromkeys(claim_ids) if i]
        if not ids:
            return []
        marks = ','.join('?' * len(ids))
        with self.read() as conn:
            return [_decode(c) for c in rows(conn.execute(
                f'''SELECT c.id,c.subject_id,c.predicate,c.object_id,c.object_text,c.content,
                           c.source_quote,c.confidence,c.status,c.created_at,c.context_json,
                           c.source_chunk_id,c.source_document_id,
                           s.name subject_name,o.name object_name
                    {_JOIN_NAMES} WHERE c.id IN ({marks})''', ids))]

    def claims_touching(self, entity_ids: list[str]) -> list[dict]:
        """Claims whose subject *or* object is one of these entities — one query.

        Used to work out what an entity merge would move, so the answer must include
        both sides: a claim that merely *mentions* the entity is affected too.
        """
        ids = [i for i in dict.fromkeys(entity_ids) if i]
        if not ids:
            return []
        marks = ','.join('?' * len(ids))
        with self.read() as conn:
            return rows(conn.execute(
                f'''SELECT c.id,c.subject_id,c.object_id,c.object_text,c.predicate,c.status,
                           c.confidence,c.content,c.source_quote,c.source_document_id,c.source_chunk_id,
                           s.name subject_name,o.name object_name
                    {_JOIN_NAMES}
                    WHERE c.subject_id IN ({marks}) OR c.object_id IN ({marks})''',
                (*ids, *ids)))

    def current_counterparts(self, claim_ids: list[str]) -> list[dict]:
        """These claims plus everything filed under the same subject and predicate.

        A disagreement is a property of a *group*, not of a row. After an operation
        touches one claim, "did this create a conflict?" can only be answered by
        looking at the rest of that group — so fetching only the named rows would make
        a conflict invisible from whichever side happened not to be named.

        Deliberately *not* :meth:`claims_touching`, which takes entity ids and answers a
        different question ("what does this entity appear in?").
        """
        ids = [i for i in dict.fromkeys(claim_ids) if i]
        if not ids:
            return []
        marks = ','.join('?' * len(ids))
        with self.read() as conn:
            return rows(conn.execute(
                _RELATION_SELECT
                + f'''WHERE c.id IN ({marks})
                       OR (c.subject_id,c.predicate) IN (
                            SELECT subject_id,predicate FROM claims WHERE id IN ({marks}))''',
                (*ids, *ids)))

    def unlinked_object_claims(self, limit: int = 200) -> list[dict]:
        """Current claims whose object is free text that never resolved to an entity.

        These are the claims that name a thing without *linking* to it — they cannot
        participate in the graph, cannot be compared to other claims, and cannot be
        found by entity. Not an error: extraction legitimately produces literals. The
        question the integrity check asks is narrower — is there an entity this text
        already refers to?
        """
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT c.id,c.subject_id,c.predicate,c.object_text,c.content,c.status,
                          c.confidence,c.source_document_id,c.source_chunk_id,s.name subject_name
                   FROM claims c JOIN entities s ON s.id=c.subject_id
                   WHERE c.object_id IS NULL AND TRIM(COALESCE(c.object_text,'')) != ''
                     AND c.status != 'rejected'
                   ORDER BY c.created_at DESC LIMIT ?''', (max(1, limit),)))

    def claim_counts_by_entity(self, entity_ids: list[str]) -> dict[str, int]:
        """How many claims reference each entity (either end), in one query.

        The number that tells an integrity scan which of two similar names is worth
        looking at: merging two entities nobody has said anything about changes no
        knowledge, and reporting such a pair is the fastest way to teach someone to
        ignore the report.
        """
        ids = [i for i in dict.fromkeys(entity_ids) if i]
        if not ids:
            return {}
        marks = ','.join('?' * len(ids))
        with self.read() as conn:
            found = rows(conn.execute(
                f'''SELECT entity_id, COUNT(*) n FROM (
                        SELECT subject_id AS entity_id FROM claims
                         WHERE subject_id IN ({marks}) AND status != 'rejected'
                        UNION ALL
                        SELECT object_id AS entity_id FROM claims
                         WHERE object_id IN ({marks}) AND status != 'rejected')
                    GROUP BY entity_id''', (*ids, *ids)))
        return {r['entity_id']: r['n'] for r in found}

    def repoint_entity(self, from_id: str, to_id: str) -> int:
        """Move every reference to one entity onto another. Returns rows touched.

        Claims are *not* rewritten in meaning: only the id they point at changes, so a
        merge can never alter what the wiki asserts — it changes which subject the
        statement is filed under. That separation is why merging two entities can
        surface a conflict but can never resolve one.
        """
        with self.write() as conn:
            moved = conn.execute('UPDATE claims SET subject_id=? WHERE subject_id=?',
                                 (to_id, from_id)).rowcount
            moved += conn.execute('UPDATE claims SET object_id=? WHERE object_id=?',
                                  (to_id, from_id)).rowcount
            return moved

    def link_objects(self, links: list[tuple[str, str]]) -> int:
        """Attach free-text objects to entities that already exist, in one write.

        ``object_text`` is deliberately left in place: this records *what the text
        referred to*, it does not rewrite what was written. Only claims that are still
        unlinked are touched (``object_id IS NULL``), so a second confirmation cannot
        overwrite a link a human chose in between.
        """
        if not links:
            return 0
        with self.write() as conn:
            return sum(
                conn.execute('UPDATE claims SET object_id=? WHERE id=? AND object_id IS NULL',
                             (entity_id, claim_id)).rowcount
                for claim_id, entity_id in links)

    def for_document(self, document_id: str, limit: int = 40) -> list[dict]:
        with self.read() as conn:
            return [_decode(c) for c in rows(conn.execute(
                f'SELECT {_CLAIM_COLUMNS} {_JOIN_NAMES} '
                "WHERE c.source_document_id=? AND c.status!='rejected' ORDER BY c.confidence DESC LIMIT ?",
                (document_id, limit)))]

    def for_document_pack(self, document_id: str, limit: int = 40) -> list[dict]:
        """Lean claim rows used by the evidence pack (no context/ids)."""
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT c.content,c.source_quote,c.predicate,c.object_text,c.confidence,c.status,
                          s.name subject_name,o.name object_name
                   FROM claims c JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id
                   WHERE c.source_document_id=? AND c.status != 'rejected'
                   ORDER BY c.created_at DESC LIMIT ?''', (document_id, limit)))

    def for_entity(self, entity_id: str) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT c.*,s.name subject_name,o.name object_name FROM claims c
                   JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id
                   WHERE c.subject_id=? OR c.object_id=? ORDER BY c.created_at DESC''',
                (entity_id, entity_id)))

    def for_documents(self, document_ids: list[str], limit: int = 50) -> list[dict]:
        if not document_ids:
            return []
        q = ','.join('?' * len(document_ids))
        with self.read() as conn:
            return rows(conn.execute(
                f'SELECT * FROM claims WHERE source_document_id IN ({q}) ORDER BY created_at DESC LIMIT ?',
                (*document_ids, limit)))

    def candidates(self, limit: int) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT c.id,c.predicate,c.content,c.object_text,c.confidence,c.status,c.polarity,c.modality,c.claim_type,
                          c.source_document_id,c.source_chunk_id,c.source_quote,c.source_start_offset,c.source_end_offset,
                          s.name subject_name,o.name object_name
                   FROM claims c JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id
                   WHERE c.status='candidate' ORDER BY c.created_at DESC LIMIT ?''', (limit,)))

    def conflicts_rows(self) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT c.id,s.name subject_name,c.predicate,c.polarity,c.content,c.object_text,c.status,
                          c.source_document_id,c.source_quote,c.modality,c.confidence,c.created_at
                   FROM claims c JOIN entities s ON s.id=c.subject_id ORDER BY c.subject_id,c.predicate'''))

    def status_of(self, claim_id: str) -> str | None:
        with self.read() as conn:
            return one(conn.execute('SELECT status FROM claims WHERE id=?', (claim_id,)), 'status')

    def health_counts(self) -> dict:
        with self.read() as conn:
            def count(sql, *args):
                return one(conn.execute(sql, args), 'c')
            return {
                'total': count('SELECT COUNT(*) c FROM claims'),
                'verified': count("SELECT COUNT(*) c FROM claims WHERE status='verified'"),
                'object_text': count('SELECT COUNT(*) c FROM claims WHERE object_id IS NULL'),
            }

    def count(self) -> int:
        with self.read() as conn:
            return one(conn.execute('SELECT COUNT(*) c FROM claims'), 'c')

    # -- writes ------------------------------------------------------------
    def insert(self, *, subject_id: str, predicate: str, object_id: str | None, object_text: str | None,
               content: str | None, context: dict, claim_type: str, polarity: str, modality: str,
               confidence: float | None, status: str, created_by: str, source_document_id: str,
               source_chunk_id: str, source_start_offset: int, source_end_offset: int,
               source_quote: str | None, ontology_version: str | None = None) -> str:
        claim_id = str(uuid.uuid4())
        with self.write() as conn:
            conn.execute(
                '''INSERT INTO claims(id,subject_id,predicate,object_id,object_text,content,context_json,claim_type,
                       polarity,modality,confidence,status,created_by,source_document_id,source_chunk_id,
                       source_start_offset,source_end_offset,source_quote,ontology_version)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (claim_id, subject_id, predicate, object_id, object_text, content, dumps(context or {}),
                 claim_type, polarity, modality, confidence, status, created_by, source_document_id,
                 source_chunk_id, source_start_offset, source_end_offset, source_quote,
                 ontology_version))
        return claim_id

    def set_status(self, claim_id: str, status: str) -> int:
        with self.write() as conn:
            return conn.execute('UPDATE claims SET status=? WHERE id=?', (status, claim_id)).rowcount

    def restore_status(self, claim_id: str, status: str, *, only_if: str) -> int:
        """Restore a status only when the claim currently holds ``only_if``."""
        with self.write() as conn:
            return conn.execute('UPDATE claims SET status=? WHERE id=? AND status=?',
                                (status, claim_id, only_if)).rowcount

    def delete_for_document(self, document_id: str) -> None:
        with self.write() as conn:
            conn.execute('DELETE FROM claims WHERE source_document_id=?', (document_id,))

    def delete_for_chunks(self, chunk_ids: list[str]) -> int:
        """Delete claims sourced from any of ``chunk_ids`` (incremental §32).

        An empty list is a no-op. Incremental re-indexing clears a chunk's
        knowledge *before* replacing/removing that chunk row, so the
        ``source_chunk_id`` foreign key stays satisfiable; deleting a claim also
        cascades its ``claim_relations``.
        """
        ids = list(chunk_ids)
        if not ids:
            return 0
        marks = ','.join('?' for _ in ids)
        with self.write() as conn:
            return conn.execute(f'DELETE FROM claims WHERE source_chunk_id IN ({marks})', ids).rowcount

    # -- claim relations (evolution) --------------------------------------
    def comparison_target(self, claim_id: str) -> dict | None:
        with self.read() as conn:
            return row(conn.execute(_RELATION_SELECT + ' WHERE c.id=?', (claim_id,)))

    def related(self, *, subject_id: str, predicate: str, exclude_id: str, limit: int) -> list[dict]:
        # A repository bound to a caller's connection reads inside that caller's
        # (still uncommitted) transaction. A rollback erases those rows *without*
        # moving the data epoch, so caching such a read could strand an entry that
        # describes a world that never existed. The cache therefore only serves the
        # standalone case — which is every hot path (direct lookup, correction
        # planner); the connection-bound caller just pays the query as before.
        if self._conn is not None:
            return self._related_rows(subject_id=subject_id, predicate=predicate,
                                      exclude_id=exclude_id, limit=limit)
        # Every input that can change the answer is in the key: two different
        # predicates, limits or exclusions must never share an entry.
        key = ('related', subject_id, predicate, exclude_id, limit)
        cached = _RELATED_CACHE.get_or_compute(
            key, lambda: self._related_rows(subject_id=subject_id, predicate=predicate,
                                            exclude_id=exclude_id, limit=limit))
        # Rows are plain, mutable dicts. Hand back fresh copies on every call so a
        # caller that annotates or edits a row cannot reach back and corrupt the
        # shared cache entry that the next caller will be served from.
        return [dict(item) for item in cached]

    def _related_rows(self, *, subject_id: str, predicate: str, exclude_id: str, limit: int) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                _RELATION_SELECT
                + ' WHERE c.subject_id=? AND c.predicate=? AND c.id != ?'
                  " AND c.status NOT IN ('rejected','archived')"
                  ' ORDER BY c.created_at DESC LIMIT ?',
                (subject_id, predicate, exclude_id, limit)))

    def for_document_chronological(self, document_id: str) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                _RELATION_SELECT + ' WHERE c.source_document_id=? ORDER BY c.created_at', (document_id,)))

    def relation(self, relation_id: str) -> dict | None:
        with self.read() as conn:
            return row(conn.execute('SELECT * FROM claim_relations WHERE id=?', (relation_id,)))

    def relations_for_claim(self, claim_id: str) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT r.id,r.relationship,r.confidence,r.reason,r.suggested_action,r.status,r.created_at,
                          r.source_claim_id,r.target_claim_id,
                          c.id other_id,c.predicate,c.content,c.object_text,
                          c.confidence other_confidence,c.status other_status,c.created_at other_created_at,
                          c.source_document_id,c.source_quote,
                          s.name subject_name,o.name object_name,d.title document_title
                   FROM claim_relations r
                   JOIN claims c ON c.id = CASE WHEN r.source_claim_id=? THEN r.target_claim_id ELSE r.source_claim_id END
                   JOIN entities s ON s.id=c.subject_id
                   LEFT JOIN entities o ON o.id=c.object_id
                   LEFT JOIN documents d ON d.id=c.source_document_id
                   WHERE r.source_claim_id=? OR r.target_claim_id=?
                   ORDER BY r.created_at DESC''', (claim_id, claim_id, claim_id)))

    # -- evolution ---------------------------------------------------------
    def supersede_edges(self, subject_id: str, predicate: str) -> list[dict]:
        """Accepted SUPERSEDE pairs for one ``(subject, predicate)`` — a chain's edges.

        Scoped to a single fact on purpose: a supersede chain *is* one relation
        evolving over time, so bounding the query this way keeps the walk small and
        avoids dragging in relations that merely happen to touch the claim.
        """
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT r.source_claim_id, r.target_claim_id, r.created_at
                   FROM claim_relations r JOIN claims sc ON sc.id = r.source_claim_id
                   WHERE r.relationship='supersedes' AND r.status='accepted'
                     AND sc.subject_id=? AND sc.predicate=?''', (subject_id, predicate)))

    def corroboration_counts(self, claim_ids: list[str]) -> dict[str, int]:
        """Accepted DUPLICATE relations per claim — the same assertion, other sources.

        A duplicate relation means "the same statement, asserted again", so counting
        them answers "how many sources back this claim?" without the claim gaining a
        column it would then have to keep in sync with the relations table.
        """
        ids = [i for i in claim_ids if i]
        if not ids:
            return {}
        marks = ','.join('?' * len(ids))
        with self.read() as conn:
            found = rows(conn.execute(
                f'''SELECT CASE WHEN source_claim_id IN ({marks}) THEN source_claim_id
                                ELSE target_claim_id END AS claim_id, COUNT(*) n
                    FROM claim_relations
                    WHERE relationship='duplicate' AND status='accepted'
                      AND (source_claim_id IN ({marks}) OR target_claim_id IN ({marks}))
                    GROUP BY claim_id''', (*ids, *ids, *ids)))
        return {r['claim_id']: r['n'] for r in found}

    def disputed_claim_ids(self, claim_ids: list[str]) -> set[str]:
        """Claims carrying an *unadjudicated* contradiction — "we are not sure yet".

        Only ``candidate`` relations count. A contradiction a human accepted is a
        judgement that the two statements really do conflict, and both stay
        presentable; a pending one means the question is still open, which is
        precisely what a reader has to be warned about before trusting the row.
        """
        ids = [i for i in dict.fromkeys(claim_ids) if i]
        if not ids:
            return set()
        marks = ','.join('?' * len(ids))
        with self.read() as conn:
            found = rows(conn.execute(
                f'''SELECT DISTINCT CASE WHEN source_claim_id IN ({marks}) THEN source_claim_id
                                         ELSE target_claim_id END AS id
                    FROM claim_relations
                    WHERE relationship='contradicts' AND status='candidate'
                      AND (source_claim_id IN ({marks}) OR target_claim_id IN ({marks}))''',
                (*ids, *ids, *ids)))
        return {r['id'] for r in found}

    def relation_queue(self, status: str | None, limit: int) -> list[dict]:
        sql = '''SELECT r.id,r.relationship,r.confidence,r.reason,r.suggested_action,r.status,r.created_by,r.created_at,
                        ns.id new_id,ns.predicate new_predicate,ns.content new_content,ns.object_text new_object_text,
                        ns.confidence new_confidence,ns.status new_status,ns.source_quote new_quote,
                        ns.source_document_id new_document_id,nsu.name new_subject,nou.name new_object,
                        nd.title new_document_title,
                        os.id old_id,os.predicate old_predicate,os.content old_content,os.object_text old_object_text,
                        os.confidence old_confidence,os.status old_status,os.source_quote old_quote,
                        os.source_document_id old_document_id,osu.name old_subject,oou.name old_object,
                        od.title old_document_title
                 FROM claim_relations r
                 JOIN claims ns ON ns.id=r.source_claim_id
                 JOIN entities nsu ON nsu.id=ns.subject_id
                 LEFT JOIN entities nou ON nou.id=ns.object_id
                 LEFT JOIN documents nd ON nd.id=ns.source_document_id
                 JOIN claims os ON os.id=r.target_claim_id
                 JOIN entities osu ON osu.id=os.subject_id
                 LEFT JOIN entities oou ON oou.id=os.object_id
                 LEFT JOIN documents od ON od.id=os.source_document_id'''
        params: list = []
        if status:
            sql += ' WHERE r.status=?'; params.append(status)
        sql += ' ORDER BY r.created_at DESC LIMIT ?'; params.append(limit)
        with self.read() as conn:
            return rows(conn.execute(sql, params))

    def pending_decisions(self, limit: int = 200) -> list[dict]:
        """Claim relations still waiting for a human *and* still worth asking about.

        The difference between this and :meth:`relation_queue` is the difference
        between "what the table holds" and "what is on my desk". Three filters, each
        removing one way a work count can lie:

        * ``status='candidate'`` — a decided relation is history, not work;
        * neither claim ``rejected`` — a pair whose sides were thrown away is not a
          question any more;
        * the target not already ``superseded`` — once the older statement has been
          retired by some other decision, "these two disagree" has been answered.

        Without the last two the number can only grow, and an inbox that never empties
        is one people learn to stop reading.
        """
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT r.id,r.relationship,r.confidence,r.reason,r.created_at,
                          s.id source_claim_id,s.object_text source_object_text,
                          t.id target_claim_id,t.object_text target_object_text,
                          se.name subject_name
                   FROM claim_relations r
                   JOIN claims s ON s.id=r.source_claim_id
                   JOIN claims t ON t.id=r.target_claim_id
                   JOIN entities se ON se.id=s.subject_id
                   WHERE r.status='candidate' AND s.status!='rejected' AND t.status!='rejected'
                     AND t.status!='superseded'
                   ORDER BY r.created_at DESC LIMIT ?''', (max(1, limit),)))

    def brief(self, claim_id: str) -> dict:
        with self.read() as conn:
            return row(conn.execute(
                '''SELECT c.predicate,c.content,c.object_text,c.source_quote,
                          s.name subject_name,o.name object_name
                   FROM claims c JOIN entities s ON s.id=c.subject_id
                   LEFT JOIN entities o ON o.id=c.object_id WHERE c.id=?''', (claim_id,))) or {}

    def update_relation(self, relation_id: str, *, status: str, relationship: str | None = None) -> None:
        with self.write() as conn:
            if relationship is not None:
                conn.execute('UPDATE claim_relations SET status=?,relationship=? WHERE id=?',
                             (status, relationship, relation_id))
            else:
                conn.execute('UPDATE claim_relations SET status=? WHERE id=?', (status, relation_id))

    def set_relation_previous_status(self, relation_id: str, previous: str | None) -> None:
        with self.write() as conn:
            conn.execute('UPDATE claim_relations SET target_previous_status=? WHERE id=?', (previous, relation_id))

    def relation_exists(self, source_claim_id: str, target_claim_id: str) -> bool:
        with self.read() as conn:
            return conn.execute(
                'SELECT 1 FROM claim_relations WHERE source_claim_id=? AND target_claim_id=?',
                (source_claim_id, target_claim_id)).fetchone() is not None

    def insert_relation(self, *, source_claim_id: str, target_claim_id: str, relationship: str,
                        confidence: float | None, reason: str, suggested_action: str,
                        status: str, created_by: str = 'system') -> str | None:
        """Record a claim-to-claim relationship.

        Returns the relation id, or ``None`` when the pair was already recorded
        (the UNIQUE constraint collapses repeats via INSERT OR IGNORE).
        """
        relation_id = str(uuid.uuid4())
        with self.write() as conn:
            cur = conn.execute(
                'INSERT OR IGNORE INTO claim_relations(id,source_claim_id,target_claim_id,relationship,'
                'confidence,reason,suggested_action,status,created_by) VALUES(?,?,?,?,?,?,?,?,?)',
                (relation_id, source_claim_id, target_claim_id, relationship, confidence,
                 reason, suggested_action, status, created_by))
            return relation_id if cur.rowcount else None

    def edges_for_entity(self, entity_id: str, limit: int) -> list[dict]:
        """Claim edges touching an entity (graph view)."""
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT c.id,c.subject_id,c.object_id,c.object_text,c.predicate,c.confidence,c.status,
                          c.source_document_id,c.source_chunk_id,s.name subject_name,o.name object_name
                   FROM claims c JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id
                   WHERE c.subject_id=? OR c.object_id=? LIMIT ?''', (entity_id, entity_id, limit)))

    # -- registry migration (a vocabulary fix, not a knowledge edit) -------
    #
    # The domain forbids editing a claim's content (ADR-009): a correction creates a
    # *new* claim and a relation. Moving a legacy claim onto the predicate it always
    # meant is a different act — the subject, object, polarity, evidence and
    # lifecycle are untouched, and the move is recorded in the audit trail. These
    # three methods exist only for that, and no domain rule may call them.

    def predicate_census(self) -> dict[str, int]:
        """How many claims are stored under each predicate value."""
        with self.read() as conn:
            return {r['predicate']: r['n'] for r in rows(conn.execute(
                'SELECT predicate, COUNT(*) n FROM claims GROUP BY predicate'))}

    def claims_with_predicate(self, predicate: str) -> list[dict]:
        """Every claim recorded under one predicate value, oldest first."""
        with self.read() as conn:
            return rows(conn.execute(
                'SELECT id, context_json FROM claims WHERE predicate=? ORDER BY created_at',
                (predicate,)))

    def set_predicate(self, claim_id: str, predicate: str, *,
                      context: dict | None = None) -> int:
        """Point one claim at a registered predicate. Migration only.

        Passing ``context`` replaces ``context_json`` — the caller owns the merge,
        because only the caller knows which signal it is preserving.
        """
        with self.write() as conn:
            if context is None:
                return conn.execute('UPDATE claims SET predicate=? WHERE id=?',
                                    (predicate, claim_id)).rowcount
            return conn.execute('UPDATE claims SET predicate=?, context_json=? WHERE id=?',
                                (predicate, dumps(context or {}), claim_id)).rowcount
