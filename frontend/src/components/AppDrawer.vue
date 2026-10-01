<script setup lang="ts">
/* 右侧抽屉 —— 内部实现迁移到 shadcn Sheet（Reka UI）。
 * 对外 API 不变：`open` / `title` props + `close` 事件。
 * 白送：焦点圈定、Esc 关闭、滑入滑出动画、无障碍标注。
 * 注意：关闭时内容会卸载（旧实现只是滑出视口）——所有调用方都以
 * `:open="!!xxx"` 驱动且内容用可选链访问数据，卸载语义更干净。 */
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'

defineProps<{ open: boolean; title: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()
</script>

<template>
  <Sheet :open="open" @update:open="(v: boolean) => { if (!v) emit('close') }">
    <SheetContent side="right" class="w-[430px] overflow-y-auto sm:max-w-[94vw]">
      <SheetHeader>
        <SheetTitle>{{ title }}</SheetTitle>
        <!-- 视觉隐藏的描述：满足 aria-describedby，不占版面 -->
        <SheetDescription class="sr-only">{{ title }}</SheetDescription>
      </SheetHeader>
      <div class="mt-2">
        <slot />
      </div>
    </SheetContent>
  </Sheet>
</template>
