<script setup lang="ts">
import { useAppStore } from '../stores/app'
const store = useAppStore()
</script>

<template>
  <div v-if="store.toastMsg" class="toast">{{ store.toastMsg }}</div>
</template>
