from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import connect, init_db

init_db(); conn=connect()
# Additive migrations for earlier prototype databases.
checks={
    'claims':['source_quote'],
    'relations':['source_quote'],
    'ideas':['source_quote'],
    'questions':['source_quote']
}
for table, cols in checks.items():
    existing={r['name'] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()}
    for col in cols:
        if col not in existing:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} TEXT')
conn.commit(); conn.close(); print('Migration complete.')
