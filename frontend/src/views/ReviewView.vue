<script setup lang="ts">
import PageHead from '../components/PageHead.vue'
import ClaimChanges from '../components/ClaimChanges.vue'
import ReviewQueue from '../components/ReviewQueue.vue'
</script>

<template>
  <div class="page">
    <PageHead
      title="审核"
      subtitle="系统提出建议，由你做最终判断——通过的知识会成为可信锚点，拒绝的会被检索与图谱排除。"
    />
    <ClaimChanges />
    <ReviewQueue />
  </div>
</template>
