"""Entities and aliases."""
from __future__ import annotations

from ..db import dumps, loads
from .base import Repository, one, row, rows

# Columns an entity update is allowed to touch (guards against arbitrary SQL).
_UPDATABLE = {'name', 'aliases_json', 'description', 'type', 'types_json', 'properties_json', 'status'}


class EntityRepository(Repository):
    def get_raw(self, entity_id: str) -> dict | None:
        with self.read() as conn:
            return row(conn.execute('SELECT * FROM entities WHERE id=?', (entity_id,)))

    def exists(self, entity_id: str) -> bool:
        with self.read() as conn:
            return conn.execute('SELECT 1 FROM entities WHERE id=?', (entity_id,)).fetchone() is not None

    def get(self, entity_id: str) -> dict | None:
        """Entity with aliases/properties decoded (raw JSON columns dropped)."""
        item = self.get_raw(entity_id)
        if item is None:
            return None
        item['aliases'] = loads(item.pop('aliases_json', '[]') or '[]', [])
        item['properties'] = loads(item.pop('properties_json', '{}') or '{}', {})
        return item

    def get_full(self, entity_id: str) -> dict | None:
        """Entity with aliases/properties decoded, keeping the raw JSON columns."""
        item = self.get_raw(entity_id)
        if item is None:
            return None
        item['aliases'] = loads(item.get('aliases_json', '[]') or '[]', [])
        item['properties'] = loads(item.get('properties_json', '{}') or '{}', {})
        return item

    def list(self, *, limit: int, offset: int, status: str | None = None,
             type: str | None = None, q: str | None = None) -> tuple[list[dict], int]:
        sql = 'SELECT id,type,name,description,properties_json,status,created_at,updated_at FROM entities'
        params: list = []
        cond: list[str] = []
        if status:
            cond.append('status=?'); params.append(status)
        if type:
            cond.append('type=?'); params.append(type)
        if q:
            cond.append("(lower(name) LIKE ? OR lower(IFNULL(description,'')) LIKE ?)")
            params.extend([f'%{q.lower()}%', f'%{q.lower()}%'])
        where = (' WHERE ' + ' AND '.join(cond)) if cond else ''
        with self.read() as conn:
            total = one(conn.execute(f'SELECT COUNT(*) c FROM entities{where}', params), 'c')
            items = rows(conn.execute(
                sql + where + ' ORDER BY updated_at DESC LIMIT ? OFFSET ?',
                (*params, max(1, limit), max(0, offset))))
        for item in items:
            item['properties'] = loads(item['properties_json'], {})
        return items, total

    def by_name(self, name: str) -> dict | None:
        with self.read() as conn:
            return row(conn.execute(
                'SELECT id,types_json FROM entities WHERE lower(name)=lower(?)', (name,)))

    def by_alias(self, alias_normalized: str) -> str | None:
        with self.read() as conn:
            return one(conn.execute(
                'SELECT entity_id FROM entity_aliases WHERE alias_normalized=? LIMIT 1', (alias_normalized,)),
                'entity_id')

    def resolution_candidates(self, limit: int = 500) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                'SELECT id,types_json,name FROM entities ORDER BY updated_at DESC LIMIT ?', (limit,)))

    def similarity_pool(self, exclude_id: str, limit: int = 800) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                'SELECT id,name,type,types_json,status FROM entities WHERE id != ? '
                'ORDER BY updated_at DESC LIMIT ?', (exclude_id, limit)))

    def insert(self, entity_id: str, *, type: str, types: list[str], name: str,
               aliases: list[str], description: str | None, properties: dict,
               status: str = 'candidate', normalize=None) -> None:
        with self.write() as conn:
            conn.execute(
                'INSERT INTO entities(id,type,types_json,name,aliases_json,description,properties_json,status) '
                'VALUES(?,?,?,?,?,?,?,?)',
                (entity_id, type, dumps(types), name, dumps(aliases or []), description,
                 dumps(properties or {}), status))
            if normalize is not None:
                for alias in aliases or []:
                    conn.execute(
                        'INSERT OR IGNORE INTO entity_aliases(entity_id,alias,alias_normalized) VALUES(?,?,?)',
                        (entity_id, alias, normalize(alias)))

    def fetch_types_aliases(self, entity_id: str) -> dict | None:
        with self.read() as conn:
            return row(conn.execute(
                'SELECT types_json,aliases_json,description,properties_json FROM entities WHERE id=?',
                (entity_id,)))

    def merge(self, entity_id: str, *, types: list[str], aliases: list[str],
              description: str | None, properties: dict) -> None:
        """Resolution merge: widen types/aliases, never narrow."""
        with self.write() as conn:
            conn.execute(
                'UPDATE entities SET type=?, types_json=?, aliases_json=?, description=COALESCE(?,description), '
                'properties_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                (types[0], dumps(types), dumps(aliases), description, dumps(properties or {}), entity_id))

    def insert_alias(self, entity_id: str, alias: str, alias_normalized: str) -> None:
        with self.write() as conn:
            conn.execute('INSERT OR IGNORE INTO entity_aliases(entity_id,alias,alias_normalized) VALUES(?,?,?)',
                         (entity_id, alias, alias_normalized))

    def replace_aliases(self, entity_id: str, aliases: list[str], normalize) -> None:
        with self.write() as conn:
            conn.execute('DELETE FROM entity_aliases WHERE entity_id=?', (entity_id,))
            for alias in aliases:
                conn.execute('INSERT OR IGNORE INTO entity_aliases(entity_id,alias,alias_normalized) VALUES(?,?,?)',
                             (entity_id, alias, normalize(alias)))

    def update(self, entity_id: str, changes: dict, *, aliases: list[str] | None = None,
               normalize=None) -> int:
        """Update whitelisted columns; optionally rebuild the alias table in the
        same transaction. Returns affected row count."""
        cols = [c for c in changes if c in _UPDATABLE]
        if not cols and aliases is None:
            return 0
        with self.write() as conn:
            rowcount = 0
            if cols:
                sets = ','.join(f'{c}=?' for c in cols)
                rowcount = conn.execute(
                    f'UPDATE entities SET {sets},updated_at=CURRENT_TIMESTAMP WHERE id=?',
                    (*[changes[c] for c in cols], entity_id)).rowcount
            if aliases is not None and normalize is not None:
                conn.execute('DELETE FROM entity_aliases WHERE entity_id=?', (entity_id,))
                for alias in aliases:
                    conn.execute(
                        'INSERT OR IGNORE INTO entity_aliases(entity_id,alias,alias_normalized) VALUES(?,?,?)',
                        (entity_id, alias, normalize(alias)))
            return rowcount

    def name_and_aliases(self, entity_id: str) -> dict | None:
        with self.read() as conn:
            return row(conn.execute('SELECT name,aliases_json FROM entities WHERE id=?', (entity_id,)))

    def names(self, names: list[str], limit: int = 40) -> list[dict]:
        names = names[:limit]
        if not names:
            return []
        placeholders = ','.join('?' for _ in names)
        with self.read() as conn:
            return rows(conn.execute(
                f'SELECT name,type,description,status FROM entities WHERE name IN ({placeholders})', names))

    def candidates(self, limit: int) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                "SELECT id,name,type,description,status,created_at FROM entities "
                "WHERE status='candidate' ORDER BY created_at DESC LIMIT ?", (limit,)))

    def count_claims_for(self, entity_id: str) -> list[dict]:
        """Claims referencing this entity (with document title)."""
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT c.*,d.title FROM claims c JOIN documents d ON d.id=c.source_document_id
                   WHERE c.subject_id=? OR c.object_id=? ORDER BY c.created_at DESC LIMIT 100''',
                (entity_id, entity_id)))

    def health_counts(self) -> dict:
        with self.read() as conn:
            def count(sql, *args):
                return one(conn.execute(sql, args), 'c')
            return {
                'total': count('SELECT COUNT(*) c FROM entities'),
                'verified': count("SELECT COUNT(*) c FROM entities WHERE status='verified'"),
                'stale': count("SELECT COUNT(*) c FROM entities WHERE status='candidate' "
                               "AND updated_at<datetime('now','-90 days')"),
            }
