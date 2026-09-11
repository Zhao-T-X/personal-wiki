# PKOS Frontend v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Rebuild the Vue frontend into the approved Personal Knowledge OS IA (6 primary nav items, Knowledge Object / Claim / Research closed loop, ⌘K palette), wired to the real backend plus 7 small new endpoints.

**Architecture:** Same Vue 3 + TS + Vite app; router and views restructured, shared components retained. New backend endpoints aggregate per-entity data and detect conflicts heuristically. Prototype `docs/prototype/llm-wiki-pkos-v1.html` is the pixel/interaction source of truth.

**Spec:** `docs/superpowers/specs/2026-09-10-pkos-redesign-design.md`

## Global Constraints

- Not a git repo — skip commits; each task ends with its verification command.
- No fake data: any metric must come from an API; use empty states otherwise.
- Prototype is visual truth; keep tokens.css unchanged.
- Backend additions follow app/main.py compact style; pytest must stay green.

---

### Task 1: Backend — 7 endpoints + tests

**Files:** Modify `app/main.py`; Test `tests/test_pkos_api.py` (create)

**Interfaces (produced):**
- `GET /api/claims/{claim_id}` → claim dict + `subject_name`/`object_name`
- `GET /api/entities/{id}/object` → `{entity, counts:{claims,relations,events,ideas,questions,evidence,documents}, claims, relations, evidence, events, ideas, questions, type_decision:[{type,reason,ok}]}`
- `GET /api/conflicts` → `[{subject_name, predicate, claims:[claim dicts]}]` (same subject_id+predicate, differing polarity)
- `GET /api/knowledge/health` → `{evidence_coverage, graph_coverage, orphan_claims, open_questions, stale, total_claims}`
- `GET /api/skills` → `[{name, purpose}]`; `GET /api/skills/{name}` → `{name, content}`

- [ ] **Step 1: Failing tests** (`tests/test_pkos_api.py`) — seed doc/chunk/entity/claims (one positive + one negative same subject+predicate), verify each endpoint shape; integrity of `/api/entities/{id}/object` counts.
- [ ] **Step 2: Run → red**
- [ ] **Step 3: Implement** in `app/main.py`:

```python
@app.get('/api/claims/{claim_id}')
def get_claim(claim_id:str):
    conn=connect()
    row=conn.execute('''SELECT c.*,s.name subject_name,o.name object_name FROM claims c
        JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id WHERE c.id=?''',(claim_id,)).fetchone()
    if not row: conn.close(); raise HTTPException(404,'Claim not found')
    conn.close(); return dict(row)

@app.get('/api/entities/{entity_id}/object')
def entity_object(entity_id:str):
    conn=connect()
    e=conn.execute('SELECT * FROM entities WHERE id=?',(entity_id,)).fetchone()
    if not e: conn.close(); raise HTTPException(404,'Entity not found')
    claims=conn.execute('''SELECT c.*,s.name subject_name,o.name object_name FROM claims c
        JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id
        WHERE c.subject_id=? OR c.object_id=? ORDER BY c.created_at DESC''',(entity_id,entity_id)).fetchall()
    rels=conn.execute('''SELECT r.*,a.name source_name,b.name target_name FROM relations r
        JOIN entities a ON a.id=r.source_id JOIN entities b ON b.id=r.target_id
        WHERE r.source_id=? OR r.target_id=? ORDER BY r.created_at DESC''',(entity_id,entity_id)).fetchall()
    docs=[r['source_document_id'] for r in claims]
    q=','.join('?'*len(docs)) if docs else 'NULL'
    def by_docs(sql):
        return [dict(r) for r in conn.execute(sql%f'{q} WITHOUT ROWID' if False else sql.replace('@Q',q),(*docs,)).fetchall()] if docs else []
    events=by_docs('SELECT * FROM events WHERE source_document_id IN (@Q) ORDER BY created_at DESC LIMIT 50')
    ideas=by_docs('SELECT * FROM ideas WHERE source_document_id IN (@Q) ORDER BY created_at DESC LIMIT 50')
    questions=by_docs('SELECT id,content,status,source_document_id,source_quote,created_at FROM questions WHERE source_document_id IN (@Q) ORDER BY created_at DESC LIMIT 50')
    conn.close()
    cl=[dict(r) for r in claims]
    evidence=[c for c in cl if c['source_quote']]
    td=[]
    for c in cl:
        if c['subject_id']==entity_id and c['predicate']=='defined_as' and c['object_text']:
            td.append({'type':c['object_text'][:40],'reason':f"来源明确定义：{c['source_quote'][:80]}",'ok':c['polarity']=='positive'})
    counts={'claims':len(cl),'relations':len([dict(r) for r in rels]),'events':len(events),'ideas':len(ideas),
            'questions':len(questions),'evidence':len(evidence),'documents':len(set(docs))}
    return {'entity':dict(e)|{'aliases':loads(e['aliases_json'],[]),'properties':loads(e['properties_json'],{})},
            'counts':counts,'claims':cl,'relations':[dict(r) for r in rels],'evidence':evidence,
            'events':events,'ideas':ideas,'questions':questions,'type_decision':td}

@app.get('/api/conflicts')
def conflicts_list(limit:int=50):
    conn=connect()
    rows=conn.execute('''SELECT c.id,s.name subject_name,c.predicate,c.polarity,c.content,c.object_text,c.status,c.source_document_id,c.source_quote,c.modality
        FROM claims c JOIN entities s ON s.id=c.subject_id ORDER BY c.subject_id,c.predicate''').fetchall()
    conn.close()
    groups={}; out=[]
    for r in rows:
        groups.setdefault((r['subject_name'],r['predicate']),[]).append(dict(r))
    for (subj,pred),cs in groups.items():
        pols={c['polarity'] for c in cs}
        if len(cs)>=2 and len(pols)>1:
            out.append({'subject_name':subj,'predicate':pred,'claims':cs})
        if len(out)>=max(1,min(limit,200)): break
    return out

@app.get('/api/knowledge/health')
def knowledge_health():
    conn=connect()
    total=conn.execute('SELECT COUNT(*) c FROM claims').fetchone()['c'] or 1
    quoted=conn.execute('SELECT COUNT(*) c FROM claims WHERE source_quote IS NOT NULL AND source_quote!=\'\'').fetchone()['c']
    graphed=conn.execute('SELECT COUNT(DISTINCT source_id||predicate||IFNULL(object_id,object_text)) c FROM relations').fetchone()['c']
    orphan=conn.execute('SELECT COUNT(*) c FROM claims WHERE object_id IS NULL').fetchone()['c']
    open_q=conn.execute("SELECT COUNT(*) c FROM questions WHERE status='open'").fetchone()['c']
    stale=conn.execute("SELECT COUNT(*) c FROM entities WHERE status='candidate' AND updated_at<datetime('now','-90 days')").fetchone()['c']
    conn.close()
    return {'evidence_coverage':round(quoted/total*100,1),'graph_coverage':graphed,'orphan_claims':orphan,
            'open_questions':open_q,'stale':stale,'total_claims':total}

@app.get('/api/skills')
def skills_list():
    from pathlib import Path
    root=Path('skills'); out=[]
    if root.exists():
        for p in sorted(root.rglob('*.md')):
            head=''
            for line in p.read_text(encoding='utf-8',errors='replace').splitlines():
                if line.startswith('# '): head=line[2:].strip(); break
            out.append({'name':p.stem,'purpose':head})
    return out

@app.get('/api/skills/{name}')
def skill_detail(name:str):
    from pathlib import Path
    root=Path('skills')
    for p in sorted(root.rglob(name+'.md')) or []:
        return {'name':p.stem,'content':p.read_text(encoding='utf-8',errors='replace')}
    raise HTTPException(404,'Skill not found')
```

- [ ] **Step 4: pytest green + full suite green**

---

### Task 2: Router + shell + command palette

**Files:** Modify `src/router/index.ts`, `src/App.vue`; Create `src/components/CommandPalette.vue`, `src/components/KpiStrip.vue`, `src/components/Triple.vue`, `src/components/BoundaryList.vue`, `src/components/TypeDecision.vue`, `src/components/FlowSteps.vue`; Delete views `DashboardView.vue EntitiesView.vue EventsView.vue IdeasView.vue QuestionsView.vue EvidenceView.vue TasksView.vue LogsView.vue` (contents absorbed)

**Router:** `/` Home, `/knowledge`, `/knowledge/object/:id`, `/knowledge/claim/:id`, `/qa`, `/research`, `/agent`, `/settings`, `/settings/database`

**App.vue:** sidebar exactly as prototype (6 nav + health card from store.health); topbar breadcrumb + omni (opens palette). Keyboard: Ctrl/Cmd+K.

**CommandPalette.vue:** commands: 搜索知识→/knowledge; Ask→/qa; 打开 RAG/KO 输入→/knowledge/object/:id (search entities via /api/entities?filter client); 创建问题→toast; 开始研究→/research; 冲突中心→/research?tab=conflicts; Agent 工作台; 编辑 Prompt→/agent?tab=prompt; 设置; Database。Free text → /qa with query。

---

### Task 3: HomeView + KnowledgeView

- **HomeView**（照原型）：居中 ask 框（Enter→/qa 带问题）、示例 chips、KpiStrip（stats+questions/conflicts counts）、Recent Knowledge（entities→object 页）、Research 摘要（open/conflicts/new）、Activity（runs timeline）。
- **KnowledgeView**：askbox 过滤 + Tabs Library/Graph/Timeline。Library：Documents 列表（现有抽屉逻辑保留）+ Knowledge Objects g3 卡（entities → object 页）。Graph：静态 SVG 顶点用真实 /api/graph 前 7 实体？——改为 Cytoscape 保留现有 GraphView 实现，嵌入本页 Tab。Timeline：events 时间线。

### Task 4: ObjectView + ClaimView

- **ObjectView**（/knowledge/object/:id）：hero + counts 行 + 七 Tab，全部来自 `/api/entities/{id}/object`；TypeDecision 渲染 type_decision（无则显示来源计数说明）；Claims Tab 行点击→claim 页；Relations Tab + Normalization 说明条；Evidence/EVENTS/Ideas/Questions 列表；`Ask about this` → /qa 带问题。
- **ClaimView**（/knowledge/claim/:id）：`GET /api/claims/{id}` → Triple 视觉 + propgrid + Evidence 块 + Normalization 说明（modality!=asserted → “未入图谱：modality=xxx”）+ Verify/Reject 按钮（PATCH status）+ 就此创建研究→/research。

### Task 5: QaView 重构

照原型：askbox + Knowledge/+Web seg（web 禁用 toast）+ Answer 卡（loading 态）+ Evidence 列表（evidence[].method 聚合为 Retrieval Trace 面板）+ BoundaryList（✓ evidence 数 / △ modality 条件说明 / ? open questions 关键词匹配数）+ 最近问答（runs task_type=ask）+ 继续研究 CTA（/research 带问题 query）。

### Task 6: ResearchView

四 Tab：Questions（列表 + 「继续研究」→ 展开流程面板：Known=该问题关键词命中的 claims（客户端 /api/claims 过滤）、Unknown=来源 quote、CTA=POST /api/agent/ask{role:'research',message:question} 显示 Findings）；Conflicts（/api/conflicts → Claim A/B 对照 + Difference 占位说明 + 保留两者/创建研究按钮）；Knowledge Health（/api/knowledge/health）。流程面板初始显示“研究如何运作”说明。

### Task 7: AgentView 重构 + Settings/Database

- **AgentView**：左 agent 列表（/api/agent/roles + 颜色）+ 右 Tabs Overview/Prompt/Skills/Tools/Runs。Prompt：管线图 + System Contract 锁定说明 + custom_prompt 编辑（现有 PUT/restore/reset/preview 逻辑迁移）；Skills：/api/skills + 详情抽屉（/api/skills/{name} 全文）；Tools：静态只读列表（与后端 tools 一致）；Runs：/api/runs?task_type=agent 按 agent_role 过滤 + RunDrawer。
- **SettingsView**：沿用 + 高级区两条目（Database→/settings/database、导出 toast）。**DatabaseView** 路由改为 /settings/database，加返回面包屑。

### Task 8: 构建与验收

`npm run build` 零错误 → 重启 uvicorn → 逐页验收（对照原型路径）+ 旧 UI 回退检查 + pytest 全绿。
