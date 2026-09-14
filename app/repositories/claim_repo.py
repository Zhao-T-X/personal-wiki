"""Claims and claim-to-claim relationships (Claim Evolution)."""
from __future__ import annotations

import uuid

from ..db import dumps, loads
from .base import Repository, one, row, rows

# Retrieval's compact claim shape (with subject/object names).
_CLAIM_COLUMNS = '''c.content,c.source_quote,c.predicate,c.polarity,c.modality,c.confidence,c.status,
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
               source_quote: str | None) -> str:
        claim_id = str(uuid.uuid4())
        with self.write() as conn:
            conn.execute(
                '''INSERT INTO claims(id,subject_id,predicate,object_id,object_text,content,context_json,claim_type,
                       polarity,modality,confidence,status,created_by,source_document_id,source_chunk_id,
                       source_start_offset,source_end_offset,source_quote)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (claim_id, subject_id, predicate, object_id, object_text, content, dumps(context or {}),
                 claim_type, polarity, modality, confidence, status, created_by, source_document_id,
                 source_chunk_id, source_start_offset, source_end_offset, source_quote))
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

    # -- claim relations (evolution) --------------------------------------
    def comparison_target(self, claim_id: str) -> dict | None:
        with self.read() as conn:
            return row(conn.execute(_RELATION_SELECT + ' WHERE c.id=?', (claim_id,)))

    def related(self, *, subject_id: str, predicate: str, exclude_id: str, limit: int) -> list[dict]:
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
