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
import { useI18n } from 'vue-i18n'
import PageHead from '../components/PageHead.vue'
import ClaimChanges from '../components/ClaimChanges.vue'
import ReviewQueue from '../components/ReviewQueue.vue'
import IntegrityPanel from '../components/IntegrityPanel.vue'
import LoadBoundary from '../components/LoadBoundary.vue'
import { api } from '../api/client'
import { useAsyncState } from '../utils/useAsyncState'

const { t } = useI18n()

/* 数字条读不到时不能整块消失——"什么都没显示"和"没有待办"在用户眼里是同一件事。 */
const inboxRes = useAsyncState(
  () => api<{ total: number; groups: Record<string, number> }>('/api/review/inbox'),
  { total: 0, groups: {} as Record<string, number> },
)
const inbox = inboxRes.data
const loading = computed(() => inboxRes.status.value === 'loading')

/** 顺序即处理顺序：先看重复主体（一次判断能理顺好几条知识），再看事实冲突。
    对象关联建议不在这里计数——它是提醒，不是待办（见后端 app/review.py）。 */
/** 内部键稳定，标签与去向走 i18n（review.group.*）。 */
const GROUPS: { key: string }[] = [
  { key: 'entity_duplicates' },
  { key: 'claim_conflicts' },
  { key: 'entities' },
  { key: 'claims' },
  { key: 'relations' },
]
const groupLabel = (key: string) => t('review.group.' + key)
const groupWhere = (key: string) => t('review.group.' + key + 'Where')

const load = inboxRes.reload
onMounted(load)
</script>

<template>
  <div class="page">
    <PageHead
      :title="t('review.title')"
      :subtitle="t('review.subtitle')"
    >
      <template #actions><button class="btn" :disabled="loading" @click="load">{{ t('review.refresh') }}</button></template>
    </PageHead>

    <LoadBoundary :state="inboxRes.state.value" :loading-text="t('review.loading')"
                  :error-title="t('review.errorTitle')"
                  :error-text="t('review.errorText')"
                  :reload="load">
      <div class="inboxstrip">
        <div class="icell total">
          <b>{{ inbox.total }}</b>
          <span>{{ t('review.itemUnit') }}</span>
        </div>
        <div v-for="g in GROUPS" :key="g.key" class="icell" :class="{ zero: !inbox.groups[g.key] }">
          <b>{{ inbox.groups[g.key] || 0 }}</b>
          <span>{{ groupLabel(g.key) }}</span>
          <small>{{ groupWhere(g.key) }}</small>
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
