<script setup lang="ts">
/** 审核页：把「有多少件事在等我」按类别摊开，然后就地处理。
 *
 *  这里的数字和侧栏角标来自同一个投影（`/api/review/inbox`），所以它们不可能
 *  对不上——这正是之前出问题的地方：角标按三张表求和，漏掉了 claim 级冲突，
 *  于是角标说 2、页面列 3。两个数字各自算，迟早会各自出错。
 *
 *  数字下面就是能动手的地方，没有只用来展示的统计。
 */
import { computed, onMounted } from 'vue'
import PageHead from '../components/PageHead.vue'
import ClaimChanges from '../components/ClaimChanges.vue'
import ReviewQueue from '../components/ReviewQueue.vue'
import IntegrityPanel from '../components/IntegrityPanel.vue'
import LoadBoundary from '../components/LoadBoundary.vue'
import { api } from '../api/client'
import { useAsyncState } from '../utils/useAsyncState'

/* 数字条读不到时不能整块消失——"什么都没显示"和"没有待办"在用户眼里是同一件事。 */
const inboxRes = useAsyncState(
  () => api<{ total: number; groups: Record<string, number> }>('/api/review/inbox'),
  { total: 0, groups: {} as Record<string, number> },
)
const inbox = inboxRes.data
const loading = computed(() => inboxRes.status.value === 'loading')

/** 顺序即处理顺序：先看重复主体（一次判断能理顺好几条知识），再看事实冲突。
    对象关联建议不在这里计数——它是提醒，不是待办（见后端 app/review.py）。 */
const GROUPS: { key: string; label: string; where: string }[] = [
  { key: 'entity_duplicates', label: '可能重复主体', where: '下方「知识体检」' },
  { key: 'claim_conflicts', label: '知识冲突', where: '下方「知识变化」' },
  { key: 'entities', label: '待审实体', where: '下方「实体」' },
  { key: 'claims', label: '待审知识', where: '下方「Claims」' },
  { key: 'relations', label: '待审关系', where: '下方「关系」' },
]

const load = inboxRes.reload
onMounted(load)
</script>

<template>
  <div class="page">
    <PageHead
      title="审核"
      subtitle="系统提出建议，由你做最终判断——通过的知识会成为可信锚点，拒绝的会被检索与图谱排除。"
    >
      <template #actions><button class="btn" :disabled="loading" @click="load">刷新</button></template>
    </PageHead>

    <LoadBoundary :state="inboxRes.state.value" loading-text="正在统计需要你确认的内容…"
                  error-title="暂时无法读取待处理数量"
                  error-text="现在数不出还有多少件事在等你——这不代表数字是 0。"
                  :reload="load">
      <div class="inboxstrip">
        <div class="icell total">
          <b>{{ inbox.total }}</b>
          <span>件待处理</span>
        </div>
        <div v-for="g in GROUPS" :key="g.key" class="icell" :class="{ zero: !inbox.groups[g.key] }">
          <b>{{ inbox.groups[g.key] || 0 }}</b>
          <span>{{ g.label }}</span>
          <small>{{ g.where }}</small>
        </div>
      </div>
    </LoadBoundary>

    <!-- 重复主体与对象关联建议就地处理；其余流程在下面各自的区块里。 -->
    <IntegrityPanel @changed="load" />
    <ClaimChanges />
    <ReviewQueue />
  </div>
</template>

<style scoped>
.inboxstrip{display:grid;grid-template-columns:auto repeat(5,minmax(0,1fr));gap:10px;margin-bottom:18px}
@media (max-width:900px){.inboxstrip{grid-template-columns:repeat(2,1fr)}}
.icell{display:flex;flex-direction:column;gap:2px;padding:11px 13px;border:1px solid var(--hair);border-radius:12px;background:var(--surface)}
.icell b{font-size:19px;letter-spacing:-.02em}
.icell span{font-size:10.5px;color:var(--sub)}
.icell small{font-size:9px;color:var(--sub);opacity:.75}
/* 没事的那一类不该看起来像有事：数字仍然是数字，只是不喊。 */
.icell.zero{opacity:.5}
.icell.total{background:var(--surface2);border-color:var(--hair);justify-content:center}
.icell.total b{font-size:24px}
</style>
