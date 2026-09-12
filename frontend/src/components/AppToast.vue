<script setup lang="ts">
import { useAppStore } from '../stores/app'
const store = useAppStore()

/** Dismiss first, then run: the action may toast a follow-up ("已撤销") that must not be cleared. */
function act() {
  const action = store.toastAction
  store.dismissToast()
  action?.run()
}
</script>

<template>
  <div v-if="store.toastMsg" class="toast">
    <span>{{ store.toastMsg }}</span>
    <button v-if="store.toastAction" class="toastact" @click="act">{{ store.toastAction.label }}</button>
  </div>
</template>

<style scoped>
.toast{display:flex;align-items:center;gap:14px;max-width:min(420px,92vw)}
.toastact{border:0;background:transparent;color:#9db4ff;font:inherit;font-weight:700;text-decoration:underline;padding:2px 0;flex:none}
</style>
