# Step 10.1 — Extraction Recall Audit

**Scope:** read-only. No business code, no prompt/schema/ontology/eligibility changes,
no new keyword rules, no LLM re-runs (default tests stay at 0 tokens).

**Question:** is the Step 10 change (Entity 65→34, Claim 101→62) *de-noising* or
*recall loss*? Every number below is traceable to a named Entity or a source document.

Artifacts (all under `tests/golden_corpus/`):

| file | contents |
|---|---|
| `semantic_diff.json` | per-document Entity diff + Claim delta + global counts |
| `entity_recall_audit.json` | every removed Entity, classified, with reason |
| `claim_recall_audit.json` | Claim delta attributed to source (see limitation) |
| `recall_ground_truth.json` | human `keep/remove/unclear` labels |
| `object_kind_audit.json` | object-kind distribution + sampled errors |
| `failures/knowledge_operations_abort.json` | the aborted document, frozen |

Reproduce: `python scripts/audit_extraction_diff.py` (deterministic, 0 LLM).

---

## Method & its hard limits (read this first)

1. **Before and After are two independent LLM runs.** Deltas therefore mix
   (a) contract/filter effects, (b) model run-to-run variance, (c) one aborted doc.
   Where a delta is variance, this report says so **with a concrete case** — never a
   blanket "it was the model".
2. **The snapshots do not contain per-claim detail.** `semantic_before/after.json` store
   per-document claim *counts* (`entity_created`, `claims`, …) and the Entity name lists,
   **not** claim subject/predicate/object. A claim-by-claim audit is therefore not
   possible from these artifacts; claims are attributed to *source* instead, and the
   gap is recorded rather than hidden.
3. **The failed document is the largest single cause** and is *not* a filter effect.

---

## 1. Entity: 65 → 34

DB-distinct delta = **−31**. Removed **name-union = 35** (the 4-name gap is because
Before contained cross-document duplicate names — e.g. `Entity`, `Claim`, `LLM` appear in
several documents); added = 10.

| classification | count | meaning |
|---|---:|---|
| structural | 8 | headings (`Extraction procedure`), file paths (`app/claim_relations.py`), relation name (`supersedes`) |
| valid_entity | 12 | code/DTO/registered-operation objects (`OperationRequest`, `CREATE`, `ClaimStateResolver`) |
| domain_term | 7 | ontology/registry nouns (`Entity`, `Claim`, `Relation Predicate Registry`) |
| valid_concept | 5 | design concepts (`Static Context`, `Progressive Disclosure`) |
| unclear | 3 | `ADR-005`, doc title, `Extraction Agent` (naming variant of `ExtractionAgent`) |

| ground truth | count |
|---|---:|
| remove | 8 |
| keep | 12 |
| unclear | 15 |

**Split by cause (the crux):**

- **from the aborted document only: 17** — `Operation`, `CREATE…RESTORE`,
  `OperationRequest`, `OperationResult`, `app/domain/operations.py`, `supersedes`,
  `ADR-005`, … These never got a chance because `KNOWLEDGE-OPERATIONS.md` aborted.
  **This is process failure, not de-noising.**
- **filter/contract-caused: 18**. Of these, the only one labelled `keep` is
  **`ClaimStateResolver`** (a real code class, dropped by the DOMAIN-MODEL run).
  The rest are structural/registry/abstract nouns.

So of the 31 Entity drop: **~17 is a rolled-back document**, ~8 is real structural
de-noising, and **1 is a clear filter-caused recall risk** (`ClaimStateResolver`);
the remaining are `unclear` (ontology meta-terms kept in other documents, design nouns).

## 2. Claim: 101 → 62

| source | count |
|---|---:|
| aborted document (never re-extracted) | **18** |
| contract / re-extraction variance | **21** |

Per document: `10-EXTRACTION-V2` 12→4 (−8), `11-NORMALIZATION` 16→5 (−11),
`DOMAIN-MODEL` 37→12 (−25), `KNOWLEDGE-OPERATIONS` 18→0 (−18, aborted),
`context-runtime` **18→41 (+23)**.

The last row matters: one document produced **more** claims after the change, so the net
−39 is not a uniform clamp. `DOMAIN-MODEL`'s −25 is the largest genuine contraction.

## 3. object_kind (sampled)

Distribution after: `literal 40 · concept 13 · entity 1 · unknown 0`.

**Two concrete error classes (recorded, not fixed):**
1. **slash guardrail (14 samples)** — the deterministic "source artefact" rule treats
   *any* `/` as a path, so enumerations become `literal`:
   `Static Context / Dynamic Context / On-Demand Context 三层上下文`,
   `LOAD / SUMMARIZE / RETRIEVE_LATER / NEVER_LOAD 四种加载策略`, … These are concepts.
2. **model over-declares `literal`** for very long enumerations.

No `Context Runtime` / `KnowledgeCompiler` / `Progressive Loading` mis-kind was found in
the sampled set (they were kept as entities).

## 4. Metrics table

| 指标 | Before | After | Diff | 人工判断 |
|---|---:|---:|---:|---|
| Entity | 65 | 34 | −31 | ~17 整篇中止(进程失败) + ~8 真结构降噪 + 1 明确漏杀 + 5 待定 |
| Claim | 101 | 62 | −39 | 18 中止 + 21 合约/波动（含一篇 +23） |
| KEEP Entity | — | 31 | — | eligibility 生效 |
| REVIEW Entity | — | 3 | — | 无 Claim 支撑被隔离 |
| Concept | 40* | 13 | −27 | 关键词 concept → LLM concept (*Before 未存 object_classes，取自 Step 9 报告) |
| Literal | — | 40 | — | 其中 **≥14 为 slash guardrail 误判** |

## 5. The 10 questions

1. **31 Entity 消失原因** — 17 来自整篇中止的 `KNOWLEDGE-OPERATIONS`（进程失败）；8 为文档结构/路径/关系名；1 明确漏杀 `ClaimStateResolver`；其余为 `unclear`（本体名词、设计概念）。
2. **39 Claim 消失原因** — 18 来自中止文档；21 为合约收紧 + 模型重抽波动（`context-runtime` 反而 +23）。
3. **真噪声减少** — 结构类 8 个名字（标题/路径/关系名/注册表名）确定移除；Object Linking 噪声 42→0；Duplicate 候选 16→7。
4. **真合法知识减少** — 12 个标 `keep` 的移除名字中 **11 个来自中止文档**，仅 `ClaimStateResolver` 是过滤所致；另有 15 个 `unclear` 需人工确认。
5. **Entity Recall** — 无法给出严格 recall：35 个移除名中 keep=12 / remove=8 / unclear=15。若把 `unclear` 折半计入，仍以“结构化噪声”为主，但 `ClaimStateResolver` 是确定的漏杀。
6. **Claim Recall** — 无法逐条计算（快照无 claim 明细）。需一次带 claim 捕获的运行才能给出。
7. **object_kind 最大问题** — slash guardrail 把含 `/` 的枚举判成 literal（14 例），以及模型对超长枚举过度声明 literal。
8. **是否明确 recall loss** — **是**：中止文档丢 17 Entity / 18 Claim（进程级）；`ClaimStateResolver` 为过滤级。→ 因此不能 VALIDATED。
9. **是否需改 Prompt/Skill** — 不急。首要问题是**全有或全无的持久化**（中止丢整篇），属 Step 11；object_kind 的 slash guardrail 是确定性小修，可一并做。
10. **是否可进入 Step 11** — 可以，但需先补一次 claim 级快照捕获（0-token 计划见下），否则 Claim Recall 永远只能靠计数。

## 6. Known limitations of this audit

- Claim-level audit is **incomplete** (artifact gap, stated in `claim_recall_audit.json`).
- The failure fixture has the error but **not** the raw model output / repair output.
- Before/After are separate runs, so small deltas carry variance.
- `object_kind` per-document capture and the persisted-claim capture come from different
  code paths; only the persisted distribution is authoritative.

## 7. Recommended next (0-token steps)

1. Extend `scripts/semantic_boundary_experiment.py` to persist, per document, the raw
   extraction envelope **and**, on failure, the first + repair outputs → turns the abort
   case into a permanent replay fixture (`tests/fixtures/llm/`), captured on the **next**
   permitted live run only.
2. Then Step 11 (Claim Failure Isolation / partial commit), which removes the largest
   single cause of “lost knowledge” found here.

## Status

**IMPLEMENTED** — audit complete and traceable; **not VALIDATED**, because (a) claim-level
evidence is incomplete, and (b) a clear recall loss exists (`ClaimStateResolver` by
filter; 17 entities / 18 claims by the aborted document).
