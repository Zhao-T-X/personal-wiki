import asyncio
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import init_db, connect
from app.service import create_document, index_document

init_db()
text='''# RAG vs Fine-tuning\n\nRAG uses external retrieval to augment model answers. When knowledge changes frequently, I think RAG is often more suitable than fine-tuning. However, fine-tuning may be preferable when the goal is stable behavior.'''
row=connect().execute('SELECT id FROM documents WHERE title=?',('RAG vs Fine-tuning',)).fetchone()
if row:
    did=row['id']
else:
    did=create_document(title='RAG vs Fine-tuning',content=text,source_type='note',source_uri=None,metadata={'demo':True})
asyncio.run(index_document(did,use_llm=False))
print('Seeded demo document:',did)
