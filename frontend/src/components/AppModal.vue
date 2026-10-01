<script setup lang="ts">
/* 模态框 —— 内部实现迁移到 shadcn Dialog（Reka UI）。
 * 对外 API 不变：`open` / `title` / `subtitle` props + `close` 事件。
 * 白送：焦点圈定、Esc 关闭、点击遮罩关闭、打开时锁定背景滚动、
 * 关闭按钮、进出动画与无障碍标注。 */
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'

defineProps<{ open: boolean; title: string; subtitle?: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()
</script>

<template>
  <Dialog :open="open" @update:open="(v: boolean) => { if (!v) emit('close') }">
    <DialogContent class="sm:max-w-[680px]">
      <DialogHeader>
        <DialogTitle>{{ title }}</DialogTitle>
        <!-- 无副标题时也要有 description（视觉隐藏），否则 Reka UI 会报 aria-describedby 缺失 -->
        <DialogDescription :class="{ 'sr-only': !subtitle }">{{ subtitle || title }}</DialogDescription>
      </DialogHeader>
      <slot />
    </DialogContent>
  </Dialog>
</template>
