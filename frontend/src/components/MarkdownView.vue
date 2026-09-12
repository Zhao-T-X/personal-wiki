<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'

const props = defineProps<{ content: string }>()

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

/**
 * The backend prompt asks the model to cite as `[doc:ID chunk:ID]`. Left alone
 * that is dead text, so turn it into the one hop the whole product is built
 * around: answer → evidence → source. Rewriting the text rule (rather than
 * pre-processing the string) keeps fenced and inline code untouched.
 */
const CITE = /\[doc:([A-Za-z0-9_-]+)\s+chunk:([A-Za-z0-9_-]+)\]/g

const renderText = md.renderer.rules.text ?? ((tokens, idx, options, _env, self) =>
  self.renderToken(tokens, idx, options))

md.renderer.rules.text = (tokens, idx, options, env, self) => {
  const html = renderText(tokens, idx, options, env, self)
  if (!html.includes('[doc:')) return html
  return html.replace(CITE, (_m, doc: string, chunk: string) =>
    `<a class="cite" href="#/knowledge?doc=${encodeURIComponent(doc)}&chunk=${encodeURIComponent(chunk)}"`
    + ` title="打开原文并定位到该片段">来源</a>`)
}

const html = computed(() => md.render(props.content || ''))
</script>

<template>
  <div class="markdown" v-html="html" />
</template>
