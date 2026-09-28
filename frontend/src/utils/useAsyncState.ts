/**
 * 一个页面级数据源的加载状态：`{ data, status, error, reload }`。
 *
 * 存在的理由不是少写几行，而是让「成功但为空」和「读不到」在类型上、在渲染上都
 * 无法混为一谈——`LoadBoundary` 拿到的是 `state`，页面自己的 `EmptyState` 只在
 * `success` 分支里才可能被渲染。
 *
 * 不为每个接口写一个 `useXxx()`：一个通用 composable + 一个 loader 就够了。
 */
import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { begin, fail, initial, succeed, type AsyncState, type LoadState } from './dataState'

export interface AsyncResource<T> {
  /** 传给 `LoadBoundary` 的唯一状态源。 */
  state: Ref<AsyncState<T>>
  /** 便捷读取（与 `state.data` 是同一个值，不是第二份）。 */
  data: ComputedRef<T>
  status: ComputedRef<LoadState>
  error: ComputedRef<string>
  /** 重新读取。永不抛错——失败已经变成 `status === 'error'`，而不是一个未捕获的 promise。 */
  reload: () => Promise<void>
  /** 写入数据但不改状态（例如本地删掉一行之后）。 */
  set: (data: T) => void
}

export function useAsyncState<T>(
  loader: () => Promise<T>,
  seed: T,
  onError?: (e: unknown) => void,
): AsyncResource<T> {
  const state = ref(initial(seed)) as Ref<AsyncState<T>>
  let inFlight = 0

  async function reload(): Promise<void> {
    const ticket = ++inFlight
    state.value = begin(state.value)
    try {
      const data = await loader()
      // 慢的旧请求不许覆盖新的结果。
      if (ticket !== inFlight) return
      state.value = succeed(data)
    } catch (e: unknown) {
      if (ticket !== inFlight) return
      state.value = fail(state.value, e)
      onError?.(e)
    }
  }

  return {
    state,
    data: computed(() => state.value.data),
    status: computed(() => state.value.status),
    error: computed(() => state.value.error),
    reload,
    set(data: T) { state.value = succeed(data) },
  }
}
