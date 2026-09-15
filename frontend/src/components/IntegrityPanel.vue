<script setup lang="ts">
/**
 * 知识体检：发现问题 → 解释影响 → 确认 → 执行 → 报告结果。
 *
 * 它刻意不做四件事：不自动合并、不替你决定哪条知识对、不隐藏自己引起的新问题、
 * 没事的时候不出现。
 *
 * 最后一条尤其重要。合并只改变「这条知识挂在谁名下」，所以它最好的结果是让一个
 * 原本隐藏的分歧变得可见——那就把它说出来，并把处置交给已经存在的冲突中心，
 * 而不是在这里顺手挑一个赢家。
 *
 * 面板里的「字面量未接入主体」分两层，语气必须不同：名称精确命中的可以直接批量接入
 * （那是一次查表结果），而多个主体声明了同一名称、或只是名称相近的那些，系统明确
 * 不挑——候选摆出来，由人点一下。少了这一层，扫描出来的疑问就永远停在后台没人看见。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, del, post } from '../api/client'
import { useAppStore } from '../stores/app'

const emit = defineEmits<{ (e: 'changed'): void }>()
const router = useRouter()
const store = useAppStore()

const scan = ref<any>(null)
const busy = ref(false)
/** The pair being previewed. Which name survives is the user's decision, so the
    direction can be swapped instead of being decided by creation order. */
const preview = ref<{ keep: any; drop: any } | null>(null)
const impact = ref<any>(null)
/** What actually happened — including the conflicts the merge created. */
const result = ref<any>(null)
/** Curation decisions already made, so they can be read back and taken back. */
const decisions = ref<any[]>([])
/** Suggestions the user has dismissed — a *preference*, not a decision about the
    world, and the reason it can be taken back at any time. */
const dismissed = ref<any[]>([])
/** The pair just ruled out, for the confirmation the user should see. */
const saved = ref('')

const duplicates = computed(() => scan.value?.duplicate_entities || [])
const unlinked = computed(() => scan.value?.unlinked_claims || [])
/** Only the ones the registry's own comparison settles: a lookup, not a guess. */
const exactLinks = computed(() => unlinked.value.filter((p: any) => p.confidence === 'high' && p.target))
/** What the system refuses to decide by itself — several entities claim the text, or
    one only resembles it. It arrives with candidates and **no chosen target**, so
    somebody has to pick; until they can, the whole tier is invisible and the system
    looks like it found nothing while sitting on a queue of open questions. */
const suggestions = computed(() =>
  unlinked.value.filter((p: any) => p.confidence === 'medium' && (p.candidates || []).length))
/** Decisions and dismissals keep the panel reachable: the only way to undo one is to
    be able to see it. */
const visible = computed(() =>
  duplicates.value.length > 0 || exactLinks.value.length > 0
  || suggestions.value.length > 0 || decisions.value.length > 0
  || dismissed.value.length > 0)

async function load() {
  try { scan.value = await api<any>('/api/integrity/scan') } catch { scan.value = null }
}
async function loadDecisions() {
  try { decisions.value = await api<any[]>('/api/integrity/curation?limit=20') } catch { decisions.value = [] }
  try { dismissed.value = await api<any[]>('/api/integrity/suppressions?limit=20') } catch { dismissed.value = [] }
}
onMounted(() => { void load(); void loadDecisions() })

/** Dry run: the numbers come from a real simulation, not a warning sentence. */
async function openImpact(pair: any, keepFirst = true) {
  const keep = keepFirst ? pair.entity_a : pair.entity_b
  const drop = keepFirst ? pair.entity_b : pair.entity_a
  busy.value = true
  result.value = null
  try {
    impact.value = await api<any>(
      `/api/integrity/merge-impact?keep=${keep.id}&drop=${drop.id}`)
    preview.value = { keep, drop }
  } catch (e: any) { store.toast(e.message) } finally { busy.value = false }
}

function swap() {
  if (!preview.value) return
  const { keep, drop } = preview.value
  void openImpact({ entity_a: drop, entity_b: keep }, true)
}

function cancel() { preview.value = null; impact.value = null }

async function confirmMerge() {
  if (!preview.value) return
  busy.value = true
  try {
    result.value = await post<any>('/api/integrity/merge',
      { keep_id: preview.value.keep.id, drop_id: preview.value.drop.id })
    preview.value = null
    impact.value = null
    await load()
    // Search must show the new state at once: a fix the user cannot see is
    // indistinguishable from a fix that did not happen.
    emit('changed')
  } catch (e: any) { store.toast(e.message) } finally { busy.value = false }
}

async function applyLinks() {
  busy.value = true
  try {
    const body = await post<any>('/api/integrity/object-links', { min_confidence: 'high' })
    store.toast(body.applied ? `已接入 ${body.applied} 条知识` : '没有可自动接入的条目')
    await load()
    emit('changed')
  } catch (e: any) { store.toast(e.message) } finally { busy.value = false }
}

/** 确认一条建议：把「这段文字指的就是这个主体」落下来。
 *
 * 系统在这一层刻意不挑，所以这里必须由人指定候选；后端不会再按文本重算一遍，
 * 否则等于把人刚刚给出的那个信息丢掉。确认后重跑扫描——建议从队列里消失本身
 * 就是反馈。 */
async function confirmLink(claimId: string, entityId: string) {
  busy.value = true
  try {
    const body = await post<any>('/api/integrity/object-links/confirm',
      { claim_id: claimId, entity_id: entityId })
    store.toast(body.linked ? `已接入「${body.entity_name}」` : '这条已经接入过，未重复写入')
    await load()
    emit('changed')
  } catch (e: any) { store.toast(e.message) } finally { busy.value = false }
}

/** 忽略一条建议：这是「这条别再提醒我」的偏好，不是关于世界的判断。
 *
 *  所以它记在 maintenance_suppressions 而不是策展决定里——策展决定要一直成立，
 *  而这条本来就该能随时收回。忽略不改变任何知识：字面量仍然是字面量，只是不再
 *  反复占用你的注意力。 */
async function dismiss(proposal: any) {
  busy.value = true
  try {
    const recorded = await post<any>('/api/integrity/suppressions',
      { claim_id: proposal.claim_id, object_text: proposal.object_text })
    store.toast('已忽略这条建议', { label: '撤销', run: () => undoDismissal(recorded.id) })
    await Promise.all([load(), loadDecisions()])
  } catch (e: any) { store.toast(e.message) } finally { busy.value = false }
}

/** 恢复一条被忽略的建议。它重新成为一条普通候选，没有被记住过任何事情。 */
async function undoDismissal(id: string) {
  busy.value = true
  try {
    await del('/api/integrity/suppressions/' + id)
    await Promise.all([load(), loadDecisions()])
    store.toast('已恢复提示')
  } catch (e: any) { store.toast(e.message) } finally { busy.value = false }
}

/** 不是同一个：一次策展决定，不是一条知识。
 *
 * 记下来之后，这两个主体不会再出现在可能重复的提示里。它是可撤销的——因为这是
 * 一次判断，不是永久真理。 */
async function markNotSame() {
  if (!preview.value) return
  busy.value = true
  try {
    await post('/api/integrity/curation',
      { entity_id_a: preview.value.keep.id, entity_id_b: preview.value.drop.id })
    saved.value = `${preview.value.keep.name} 与 ${preview.value.drop.name}`
    preview.value = null
    impact.value = null
    await Promise.all([load(), loadDecisions()])
  } catch (e: any) { store.toast(e.message) } finally { busy.value = false }
}

async function undoDecision(id: string) {
  busy.value = true
  try {
    await del('/api/integrity/curation/' + id)
    await Promise.all([load(), loadDecisions()])
  } catch (e: any) { store.toast(e.message) } finally { busy.value = false }
}

/** 冲突的处置是既有能力的事，这里只负责把用户送到那里——但「那里」必须是**真的
 *  会列出这条冲突**的界面。
 *
 *  合并产生的是「单值关系上出现了两个不同取值」，两条断言的极性相同；而冲突中心
 *  收的是「同一断言被说成又真又假」（极性相反）。把用户送去冲突中心，他会在一个
 *  空列表上找刚才被告知的那条冲突。它在审核页的知识变化清单里，取代 / 两者都保留 /
 *  撤销都在那儿，所以送到那里。 */
function openConflicts() { router.push('/review') }
</script>

<template>
  <div v-if="visible || result" class="panel pad ipanel">
    <div class="row">
      <span class="warn">⚠</span>
      <b>知识体检</b>
      <span class="faint" style="font-size:9.5px">发现的问题由你确认，系统不自动改知识</span>
      <div class="grow"></div>
      <button class="btn sm ghost" :disabled="busy" @click="load">重新检查</button>
    </div>

    <!-- 1. 发现 -->
    <div v-if="duplicates.length || exactLinks.length || suggestions.length" class="ipbody">
      <div v-for="p in duplicates" :key="p.entity_a.id + p.entity_b.id" class="iprow">
        <span class="tag amber">可能重复的主体</span>
        <b>{{ p.entity_a.name }}</b><span class="faint">· {{ p.entity_a.claims }} 条知识</span>
        <span class="faint">与</span>
        <b>{{ p.entity_b.name }}</b><span class="faint">· {{ p.entity_b.claims }} 条知识</span>
        <div class="grow"></div>
        <button class="btn sm" :disabled="busy" @click="openImpact(p)">查看影响</button>
      </div>

      <div v-for="p in exactLinks" :key="p.claim_id" class="iprow">
        <span class="tag blue">字面量未接入主体</span>
        <span>{{ p.subject_label }} {{ p.predicate_label || '' }}</span>
        <b>「{{ p.object_text }}」</b>
        <span class="faint">→ 已有主体</span><b>{{ p.target.name }}</b>
      </div>
      <div v-if="exactLinks.length" class="ipactions">
        <button class="btn sm" :disabled="busy" @click="applyLinks">
          接入这 {{ exactLinks.length }} 条（只连接，不新建主体）
        </button>
      </div>

      <!-- 需要人来选的那些：系统给出候选，但不挑。 -->
      <div v-for="p in suggestions" :key="'s' + p.claim_id" class="iprow">
        <span class="tag blue">字面量未接入主体</span>
        <span>{{ p.subject_label }} {{ p.predicate_label || '' }}</span>
        <b>「{{ p.object_text }}」</b>
        <span class="faint">{{ p.matched_by === 'ambiguous' ? '指向其中哪一个？' : '是不是指' }}</span>
        <button v-for="c in p.candidates" :key="c.id" class="btn sm" :disabled="busy"
                :title="p.matched_by === 'ambiguous'
                  ? '多个主体都声明了这个名称，需要你选一个'
                  : `名称相近 ${Math.round((c.similarity || 0) * 100)}%`"
                @click="confirmLink(p.claim_id, c.id)">{{ c.name }}</button>
        <button class="btn sm ghost" :disabled="busy" title="不再提示这一条（随时可恢复）"
                @click="dismiss(p)">忽略</button>
      </div>
      <p v-if="suggestions.length" class="faint" style="font-size:9.5px;margin:8px 4px 0">
        这几条系统不替你做决定——要么多个主体声明了同一个名称，要么只是名称相近。确认后才会接入；
        接入的是已有的主体，不新建、也不合并。
      </p>
    </div>

    <!-- 2. 解释影响 —— 合并之前先把后果算出来 -->
    <div v-if="impact" class="ipdialog">
      <div class="ipdhead">
        <b>可能是同一个主体</b>
        <div class="grow"></div>
        <button class="btn sm ghost" @click="cancel">取消</button>
      </div>
      <div class="ipdnames"><b>{{ preview?.keep.name }}</b> 与 <b>{{ preview?.drop.name }}</b></div>
      <p class="faint" style="font-size:10px;margin:6px 0 0">
        合并后，{{ preview?.drop.name }} 的 {{ impact.claims_moved }} 条知识会归到
        {{ preview?.keep.name }} 名下；两边原有的名称都会保留为别名，以后提到哪个都能找到。
      </p>
      <div class="ipstats">
        <span>涉及知识 <b>{{ impact.affected_claims }}</b></span>
        <span>移动 <b>{{ impact.claims_moved }}</b></span>
        <span>证据 <b>{{ impact.affected_evidence }}</b></span>
        <span>关系 <b>{{ impact.affected_relations }}</b></span>
      </div>
      <!-- 这句是整个过程里最该被看到的一句：合并不解决事实冲突。
           提前说出来，用户才知道自己在做什么，而不是合并完之后才发现。 -->
      <div v-if="impact.new_conflicts.length" class="ipwarn">
        <b>合并不会解决事实冲突——它可能产生 {{ impact.new_conflicts.length }} 个新冲突：</b>
        <div v-for="(c, i) in impact.new_conflicts" :key="i" class="ipconflict">
          {{ c.subject_name }} {{ c.predicate_label || c.predicate }}：
          {{ c.objects.join(' 与 ') }}
          <span v-if="c.functional" class="tag amber">单值关系</span>
        </div>
        <p class="faint" style="font-size:9.5px;margin:5px 0 0">
          合并后它们会出现在冲突中心，由你决定哪条成立。旧知识不会被删掉。
        </p>
      </div>
      <div v-else class="ipok">这一步不会产生新的冲突。</div>
      <div v-if="impact.type_mismatch" class="ipwarn">
        两个主体声明的类型不同——如果这确实是同一个对象，合并后类型会取较全的一个。
      </div>
      <div class="ipactions">
        <button class="btn primary" :disabled="busy" @click="confirmMerge">
          合并到「{{ preview?.keep.name }}」
        </button>
        <button class="btn sm" :disabled="busy" @click="markNotSame">不是同一个</button>
        <button class="btn sm ghost" :disabled="busy" @click="swap">保留「{{ preview?.drop.name }}」</button>
      </div>
    </div>

    <!-- 已记住：用户做过一次判断以后，系统不该再问第二遍 -->
    <div v-if="saved" class="ipok ipdialog">
      ✓ 已记住：{{ saved }} 不是同一个。以后不会再提示这两个主体。
    </div>
    <details v-if="decisions.length" class="ipmem">
      <summary>已记住 {{ decisions.length }} 条判断</summary>
      <div v-for="d in decisions" :key="d.id" class="iprow">
        <span class="tag">不是同一个</span>
        <b>{{ d.entity_a_name }}</b><span class="faint">≠</span><b>{{ d.entity_b_name }}</b>
        <span v-if="d.reason" class="faint">· {{ d.reason }}</span>
        <div class="grow"></div>
        <button class="btn sm ghost" :disabled="busy" @click="undoDecision(d.id)">撤销</button>
      </div>
    </details>

    <!-- 已忽略的建议：和「已记住的判断」分开列，因为两者不是一回事——
         一个是关于知识库的判断，一个是关于这条提醒的偏好。 -->
    <details v-if="dismissed.length" class="ipmem">
      <summary>已忽略 {{ dismissed.length }} 条建议</summary>
      <div v-for="d in dismissed" :key="d.id" class="iprow">
        <span class="tag">已忽略</span>
        <b>{{ d.subject_name }}</b>
        <span class="faint">记着</span><b>「{{ d.object_text }}」</b>
        <div class="grow"></div>
        <button class="btn sm ghost" :disabled="busy" @click="undoDismissal(d.id)">恢复提示</button>
      </div>
      <p class="faint" style="font-size:9.5px;margin:6px 4px 0">
        忽略的是「这条别再提醒我」，不是对知识的判断——所以它随时可以恢复，也没有改变任何一条知识。
      </p>
    </details>

    <!-- 3. 结果 —— 包括它引起的后果 -->
    <div v-if="result" class="ipdialog">
      <div class="ipdhead"><b>✓ 已合并「{{ result.merged.drop_name }}」到「{{ result.merged.keep_name }}」</b></div>
      <p class="faint" style="font-size:10px;margin:6px 0 0">
        {{ result.merged.moved_claims }} 条知识已归到 {{ result.merged.keep_name }} 名下。
      </p>
      <div v-if="result.recheck.conflicts.length" class="ipwarn">
        <b>因此产生了 {{ result.recheck.conflicts.length }} 个知识冲突：</b>
        <div v-for="(c, i) in result.recheck.conflicts" :key="i" class="ipconflict">
          {{ c.subject_name }} {{ c.predicate_label || c.predicate }}：{{ c.objects.join(' 与 ') }}
        </div>
        <div class="ipactions">
          <button class="btn primary" @click="openConflicts">立即处理冲突 →</button>
        </div>
      </div>
      <div v-else class="ipok">没有产生新的冲突。</div>
    </div>
  </div>
</template>

<style scoped>
.ipanel{margin-bottom:16px}
.warn{color:var(--amber)}
.ipbody{margin-top:10px;display:grid;gap:2px}
.iprow{display:flex;align-items:center;gap:8px;padding:8px 4px;font-size:11.5px;border-bottom:1px solid var(--hair);flex-wrap:wrap}
.iprow:last-child{border-bottom:none}
.ipactions{display:flex;align-items:center;gap:8px;margin-top:10px;flex-wrap:wrap}
/* 影响预览：一张独立的卡片语气，因为这是「要动手了」的界面 */
.ipdialog{margin-top:12px;padding:12px 13px;border:1px solid var(--hair);border-radius:12px;background:var(--surface2)}
.ipdhead{display:flex;align-items:center;gap:8px;font-size:12px}
.ipdnames{margin-top:7px;font-size:14px;letter-spacing:-.01em}
.ipstats{display:flex;flex-wrap:wrap;gap:12px;margin-top:9px;font-size:10px;color:var(--sub)}
.ipstats b{font-size:12.5px;color:var(--text)}
.ipwarn{margin-top:10px;padding:9px 11px;border-radius:10px;background:#fdf4e6;font-size:10.5px;color:#8a6220}
.ipconflict{margin-top:5px;font-size:11.5px;color:var(--text)}
.ipok{margin-top:10px;font-size:10.5px;color:#2f7d57}
/* 已记住的判断：平时是一行，需要时才是完整清单 */
.ipmem{margin-top:12px;border-top:1px solid var(--hair);padding-top:10px}
.ipmem summary{cursor:pointer;font-size:10px;color:var(--sub);font-weight:600}
</style>
