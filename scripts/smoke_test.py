import asyncio
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import init_db
from app.service import create_document, index_document
from app.retrieval import search

init_db()
did=create_document(title='Smoke Test',content='RAG retrieves external knowledge for changing information.',source_type='note',source_uri=None,metadata={'smoke':True})
print('document:', did)
print('index:', asyncio.run(index_document(did,use_llm=False)))
print('search:', search('changing information', limit=5, semantic=False))
