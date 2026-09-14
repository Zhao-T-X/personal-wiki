"""Aggregate / read-model queries: stats, export, timeseries, integrity, global graph.

These are whole-store reads rather than per-entity persistence, so they live in one
place instead of being scattered across route handlers.
"""
from __future__ import annotations

from .base import Repository, one, rows

_STATS_TABLES = ['documents', 'chunks', 'entities', 'claims', 'relations', 'ideas', 'questions',
                 'events', 'chunk_embeddings', 'llm_runs', 'llm_run_steps', 'context_runs',
                 'context_sections']

_EXPORT = {
    'documents': 'SELECT * FROM documents ORDER BY created_at',
    'chunks': 'SELECT * FROM chunks ORDER BY document_id,chunk_index',
    'entities': 'SELECT * FROM entities ORDER BY created_at',
    'entity_aliases': 'SELECT * FROM entity_aliases ORDER BY entity_id',
    'claims': 'SELECT * FROM claims ORDER BY created_at',
    'relations': 'SELECT * FROM relations ORDER BY created_at',
    'ideas': 'SELECT * FROM ideas ORDER BY created_at',
    'questions': 'SELECT * FROM questions ORDER BY created_at',
    'events': 'SELECT * FROM events ORDER BY created_at',
}


class CatalogRepository(Repository):
    def stats(self) -> dict:
        with self.read() as conn:
            return {t: one(conn.execute(f'SELECT COUNT(*) c FROM {t}'), 'c') for t in _STATS_TABLES}

    def export_all(self) -> dict:
        with self.read() as conn:
            return {table: rows(conn.execute(sql)) for table, sql in _EXPORT.items()}

    def timeseries(self, days: int) -> list[dict]:
        from datetime import date, timedelta
        with self.read() as conn:
            def series(table: str) -> dict:
                return {r['d']: r['c'] for r in conn.execute(
                    f"SELECT date(created_at) d,COUNT(*) c FROM {table} "
                    f"WHERE created_at>=date('now',?) GROUP BY date(created_at)", (f'-{days} days',))}
            docs, ents, clms = series('documents'), series('entities'), series('claims')
        out = []
        for i in range(days - 1, -1, -1):
            d = str(date.today() - timedelta(days=i))
            out.append({'date': d, 'documents': docs.get(d, 0),
                        'entities': ents.get(d, 0), 'claims': clms.get(d, 0)})
        return out

    def integrity(self) -> dict:
        with self.read() as conn:
            integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
            page_count = conn.execute('PRAGMA page_count').fetchone()[0]
            page_size = conn.execute('PRAGMA page_size').fetchone()[0]
        return {'integrity': integrity, 'page_count': page_count,
                'size_mb': round(page_count * page_size / 1048576, 1)}

    def context_metrics(self, days: int) -> dict:
        window = f'-{days} days'
        with self.read() as conn:
            agents = rows(conn.execute('''
                SELECT agent_name, COUNT(*) calls,
                       CAST(AVG(actual_tokens) AS INT) avg_context_tokens,
                       COALESCE(SUM(actual_tokens),0) context_tokens,
                       COALESCE(SUM(trimmed_tokens),0) trimmed_tokens,
                       COALESCE(SUM(over_budget),0) over_budget_calls,
                       ROUND(AVG(efficiency),4) avg_efficiency
                FROM context_runs WHERE created_at>=datetime('now',?)
                GROUP BY agent_name ORDER BY calls DESC''', (window,)))
            tasks = rows(conn.execute('''
                SELECT r.task_type, COUNT(DISTINCT r.id) runs, COUNT(s.id) steps,
                       COALESCE(SUM(s.prompt_tokens),0) prompt_tokens,
                       COALESCE(SUM(s.completion_tokens),0) completion_tokens,
                       COALESCE(SUM(CASE WHEN s.usage_source='provider' THEN 1 ELSE 0 END),0) provider_steps
                FROM llm_runs r LEFT JOIN llm_run_steps s ON s.run_id=r.id
                WHERE r.created_at>=datetime('now',?)
                GROUP BY r.task_type ORDER BY runs DESC''', (window,)))
        for t in tasks:
            total = t['prompt_tokens'] + t['completion_tokens']
            t['total_tokens'] = total
            t['tokens_per_run'] = round(total / t['runs']) if t['runs'] else 0
        return {'window_days': days, 'agents': agents, 'by_task_type': tasks,
                'totals': {'prompt_tokens': sum(t['prompt_tokens'] for t in tasks),
                           'completion_tokens': sum(t['completion_tokens'] for t in tasks),
                           'context_calls': sum(a['calls'] for a in agents),
                           'context_tokens': sum(a['context_tokens'] for a in agents)}}

    def graph_nodes(self, limit: int) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                'SELECT id,type,name,description,status FROM entities ORDER BY updated_at DESC LIMIT ?',
                (limit,)))

    def graph_claims(self, limit: int) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                "SELECT c.id,c.subject_id,c.object_id,c.object_text,c.predicate,c.confidence,c.status,"
                "c.source_document_id,c.source_chunk_id,s.name subject_name,o.name object_name "
                "FROM claims c JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id "
                "ORDER BY c.created_at DESC LIMIT ?", (limit,)))
