from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import init_db, connect

init_db(); conn=connect()
result={}
for table in ('documents','chunks','entities','entity_aliases','claims','relations','ideas','questions','events'):
    result[table]=[dict(r) for r in conn.execute(f'SELECT * FROM {table}').fetchall()]
conn.close()
out=Path('data/wiki-export.json'); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(out)
