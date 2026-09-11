from __future__ import annotations
from .db import connect, loads


def neighborhood(entity_id: str, depth: int = 1, limit: int = 100):
    conn=connect()
    root=conn.execute('SELECT id,type,name,description,status FROM entities WHERE id=?',(entity_id,)).fetchone()
    if not root:
        conn.close(); return None
    nodes={root['id']:dict(root)}; edges=[]; frontier={entity_id}
    for _ in range(max(1,min(depth,3))):
        if not frontier: break
        qmarks=','.join('?' for _ in frontier)
        rows=conn.execute(f'''SELECT r.id,r.source_id,r.target_id,r.predicate,r.confidence,r.status,
                                     a.name source_name,b.name target_name,r.source_document_id,r.source_chunk_id
                              FROM relations r JOIN entities a ON a.id=r.source_id JOIN entities b ON b.id=r.target_id
                              WHERE r.source_id IN ({qmarks}) OR r.target_id IN ({qmarks})
                              LIMIT ?''', tuple(frontier)+tuple(frontier)+(limit,)).fetchall()
        nxt=set()
        for r in rows:
            rd=dict(r); edges.append(rd)
            for nid,name,typ in ((r['source_id'],r['source_name'],None),(r['target_id'],r['target_name'],None)):
                if nid not in nodes:
                    er=conn.execute('SELECT id,type,name,description,status FROM entities WHERE id=?',(nid,)).fetchone()
                    if er: nodes[nid]=dict(er); nxt.add(nid)
        frontier=nxt
    # Add claim edges to make claim-centric graph visible.
    claim_rows=conn.execute('''SELECT c.id,c.subject_id,c.object_id,c.object_text,c.predicate,c.confidence,c.status,c.source_document_id,c.source_chunk_id,
                                      s.name subject_name,o.name object_name
                               FROM claims c JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id
                               WHERE c.subject_id=? OR c.object_id=? LIMIT ?''',(entity_id,entity_id,limit)).fetchall()
    for c in claim_rows:
        edges.append(dict(c)|{'kind':'claim'})
    conn.close()
    return {'nodes':list(nodes.values()),'edges':edges}
