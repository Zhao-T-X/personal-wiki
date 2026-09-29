"""验收用合成数据（无需 LLM / API Key）。

仅用于端到端人工验收：灌入足量且结构正确的实体 / 声明 / 关系 / 事件 / 问题，
以便真实点开 P0/P1 重构后的界面，并验证 P2-1 的规模护栏
（时间线"加载更多事件"、研究"加载更多问题"、图谱"仅展示部分网络"提示）。

与 scripts/seed_demo.py 正交：本脚本所有行以 ``acc-`` 前缀标识，可重复执行
（先清后插），不依赖 LLM 抽取。验收结束后可用 DELETE FROM ... WHERE id LIKE 'acc-%' 清理。
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db, connect

init_db()
conn = connect()
cur = conn.cursor()

# 先清理上一轮验收数据，保证可重复执行
for table, col in [
    ("ideas", "id"), ("questions", "id"), ("events", "id"),
    ("relations", "id"), ("claims", "id"), ("entities", "id"),
    ("chunks", "id"), ("documents", "id"),
]:
    cur.execute(f"DELETE FROM {table} WHERE {col} LIKE 'acc-%'")

DATE_POOL = ["2026-09-21", "2026-09-23", "2026-09-25", "2026-09-27", "2026-09-29"]


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# 来源文档 + chunks（声明 / 关系 / 事件 / 问题都要引用）
doc_id = uid("acc-doc")
cur.execute(
    "INSERT INTO documents (id,title,content,source_type,content_hash,metadata_json,created_at) "
    "VALUES (?,?,?,?,?,?,?)",
    (doc_id, "验收演示文档", "这是用于人工验收的演示文档内容。", "note", doc_id, "{}", "2026-09-29"),
)
chunk_ids = []
for i in range(6):
    cid = uid("acc-chunk")
    cur.execute(
        "INSERT INTO chunks (id,document_id,content,chunk_index,start_offset,end_offset,created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (cid, doc_id, f"演示片段 {i}", i, i * 10, i * 10 + 10, "2026-09-29"),
    )
    chunk_ids.append(cid)

# 实体
entity_ids = []
for i in range(80):
    eid = uid("acc-ent")
    cur.execute(
        "INSERT INTO entities (id,type,types_json,name,aliases_json,description,properties_json,status,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (eid, "Concept", "[]", f"对象{i:03d}", "[]", f"第 {i} 个演示概念", "{}", "verified", "2026-09-29"),
    )
    entity_ids.append(eid)

# 声明（candidate，喂给"待确认"）
for i in range(120):
    cid = uid("acc-claim")
    s = entity_ids[i % len(entity_ids)]
    o = entity_ids[(i + 1) % len(entity_ids)]
    d = DATE_POOL[i % len(DATE_POOL)]
    cur.execute(
        "INSERT INTO claims (id,subject_id,predicate,object_id,object_text,content,context_json,"
        "claim_type,polarity,modality,confidence,status,created_by,source_document_id,"
        "source_chunk_id,source_start_offset,source_end_offset,source_quote,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, s, "related_to", o, None, f"对象{s[-4:]} 与 对象{o[-4:]} 相关",
         "{}", "factual", "positive", "asserted", 0.82, "candidate", "llm",
         doc_id, chunk_ids[i % len(chunk_ids)], 0, 10, "演示引用", d),
    )

# 关系（verified，喂给图谱）
for i in range(140):
    rid = uid("acc-rel")
    s = entity_ids[i % len(entity_ids)]
    t = entity_ids[(i + 3) % len(entity_ids)]
    d = DATE_POOL[i % len(DATE_POOL)]
    cur.execute(
        "INSERT INTO relations (id,source_id,predicate,target_id,context_json,confidence,status,"
        "created_by,source_document_id,source_chunk_id,source_start_offset,source_end_offset,"
        "source_quote,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, s, "linked_to", t, "{}", 0.7, "verified", "llm",
         doc_id, chunk_ids[i % len(chunk_ids)], 0, 10, "演示引用", d),
    )

# 事件（260 条，触发时间线"加载更多"按钮）
for i in range(260):
    eid = uid("acc-ev")
    d = DATE_POOL[i % len(DATE_POOL)]
    cur.execute(
        "INSERT INTO events (id,event_type,description,participants_json,time_json,location,status,"
        "confidence,source_document_id,source_chunk_id,source_start_offset,source_end_offset,"
        "source_quote,properties_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (eid, "milestone", f"事件{i:03d} 发生", "[]", "{}", None, "unknown", 0.9,
         doc_id, chunk_ids[i % len(chunk_ids)], 0, 10, "演示引用", "{}", d),
    )

# 问题（420 条 open，触发研究页"加载更多"按钮）
for i in range(420):
    qid = uid("acc-q")
    d = DATE_POOL[i % len(DATE_POOL)]
    cur.execute(
        "INSERT INTO questions (id,content,status,source_document_id,source_chunk_id,"
        "source_start_offset,source_end_offset,source_quote,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (qid, f"问题{i:03d}：这个概念具体指什么？", "open",
         doc_id, chunk_ids[i % len(chunk_ids)], 0, 10, "演示引用", d),
    )

# 想法
for i in range(20):
    iid = uid("acc-idea")
    cur.execute(
        "INSERT INTO ideas (id,content,status,confidence,source_document_id,source_chunk_id,"
        "source_start_offset,source_end_offset,source_quote,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (iid, f"想法 {i}: 是否应把相关概念合并？", "candidate", 0.6,
         doc_id, chunk_ids[0], 0, 10, "演示引用", "2026-09-29"),
    )

conn.commit()
print("acceptance seed done: "
      f"entities={len(entity_ids)} claims=120 relations=140 events=260 questions=420 ideas=20")
