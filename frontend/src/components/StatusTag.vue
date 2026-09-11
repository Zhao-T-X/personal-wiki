<script setup lang="ts">
const props = defineProps<{ status: string }>()

const GREEN = new Set(['success', 'verified', 'processed', 'completed', 'valid', 'grounded', 'ok', 'resolved', 'implemented', 'accepted', 'answered'])
const AMBER = new Set(['candidate', 'open', 'queued', 'review', 'partial', 'partially_answered', 'planned', 'ongoing'])
const RED = new Set(['failed', 'invalid', 'missing', 'rejected'])
const BLUE = new Set(['running', 'processing', 'started', 'draft'])
// 'unknown' / empty fall through to the neutral gray tag.

const cls = () => props.status && GREEN.has(props.status) ? 'green'
  : AMBER.has(props.status) ? 'amber'
  : RED.has(props.status) ? 'red'
  : BLUE.has(props.status) ? 'blue' : ''
</script>

<template>
  <span class="tag" :class="cls()">{{ status }}</span>
</template>
