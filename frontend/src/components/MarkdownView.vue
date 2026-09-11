<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'

const props = defineProps<{ content: string }>()
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const html = computed(() => md.render(props.content || ''))
</script>

<template>
  <div class="markdown" v-html="html" />
</template>
