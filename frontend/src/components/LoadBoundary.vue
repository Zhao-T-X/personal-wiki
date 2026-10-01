<script setup lang="ts">
/**
 * 一个数据面的三种渲染，只在一个地方决定。
 *
 * 页面把 `state` 交给它，自己的 `EmptyState` 放进默认插槽——插槽只在 `success`
 * 时才渲染，所以「读不到」在结构上就不可能退化成一个空状态。这是 P0-2 的落地方式：
 * 不是靠每处记得判断，而是让写错变得做不到。
 *
 * 失败时给「发生了什么」+「下一步怎么做」（重新加载），不显示堆栈、HTML、HTTP 状态码。
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { LoadState } from '../utils/dataState'

const { t } = useI18n()

const props = withDefaults(defineProps<{
  /** `useAsyncState().state.value`，或任何手写的 `{ status, error }`。 */
  state: { status: LoadState; error?: string }
  loadingText?: string
  errorTitle?: string
  /** 这个数据面失败意味着什么——比通用文案更具体时传它。 */
  errorText?: string
  /** 提供就渲染「重新加载」。 */
  reload?: () => unknown
}>(), {
  loadingText: '',
  errorTitle: '',
  errorText: '',
})

/** 兜底文案：父组件大多已传 i18n 文案，这里只兜住没传的情况。 */
const loadingTextR = computed(() => props.loadingText || t('loadBoundary.loading'))
const errorTitleR = computed(() => props.errorTitle || t('loadBoundary.errorTitle'))
const errorTextR = computed(() => props.errorText || t('loadBoundary.errorTitle'))

const waiting = computed(() => props.state.status === 'idle' || props.state.status === 'loading')
const failed = computed(() => props.state.status === 'error')
</script>

<template>
  <div v-if="failed" class="loaderr" role="alert">
    <span class="leico">⚠</span>
    <div class="grow">
      <b>{{ errorTitleR }}</b>
      <p class="letext">{{ errorTextR }}</p>
      <p v-if="state.error" class="lereason">{{ state.error }}</p>
    </div>
    <button v-if="reload" class="btn" @click="reload()">{{ t('loadBoundary.reload') }}</button>
  </div>

  <div v-else-if="waiting" class="loadwait" aria-busy="true">
    <span class="lwdot"></span><span>{{ loadingTextR }}</span>
  </div>

  <slot v-else />
</template>

<style scoped>
.loaderr{display:flex;align-items:flex-start;gap:11px;padding:14px 15px;border:1px solid #f3d7d9;border-radius:14px;background:#fff7f7}
.loaderr b{font-size:12px}
.leico{color:var(--red);font-size:14px;line-height:1.3;flex:none}
.letext{margin:4px 0 0;font-size:10px;color:var(--sub);line-height:1.6}
.lereason{margin:4px 0 0;font-size:9.5px;color:var(--faint);line-height:1.6;overflow-wrap:anywhere}
.loadwait{display:flex;align-items:center;gap:9px;padding:14px 15px;border:1px solid var(--hair);border-radius:14px;background:var(--surface2);font-size:10px;color:var(--sub)}
.lwdot{width:7px;height:7px;border-radius:50%;background:var(--blue);box-shadow:0 0 0 3px var(--tint-blue);animation:lwpulse 1.4s ease-in-out infinite}
@keyframes lwpulse{0%,100%{opacity:1}50%{opacity:.3}}
</style>
