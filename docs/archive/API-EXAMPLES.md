# API examples

Create a note:

```bash
curl -X POST http://127.0.0.1:8000/api/documents \
  -H 'Content-Type: application/json' \
  -d '{"title":"RAG vs Fine-tuning","content":"When knowledge changes frequently, I think RAG is more suitable.","source_type":"note"}'
```

Build chunks without an API key:

```bash
curl -X POST http://127.0.0.1:8000/api/documents/DOCUMENT_ID/index/local
```

Run LLM extraction:

```bash
curl -X POST http://127.0.0.1:8000/api/documents/DOCUMENT_ID/index
```

Generate embeddings:

```bash
curl -X POST http://127.0.0.1:8000/api/documents/DOCUMENT_ID/embed
```

Hybrid search:

```bash
curl 'http://127.0.0.1:8000/api/search?q=RAG%20changing%20knowledge&limit=8&semantic=true'
```

Ask:

```bash
curl -X POST http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"What did I think about RAG when knowledge changes?","top_k":8}'
```
