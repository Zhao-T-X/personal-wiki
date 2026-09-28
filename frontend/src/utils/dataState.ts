/**
 * LoadState —— 之前缺失的那一个状态。
 *
 * 全前端每个数据面都只有两种状态：「这里有数据」和「这里没有」。一次失败的请求掉进
 * 第二种里，于是**请求坏了**和**知识库是空的**长得一模一样。对知识库产品来说这不是
 * 观感问题：它对用户宣布「你的知识没了」或「审核已经做完了」，而两句话都不是真的。
 *
 * 所以加载现在是一个显式状态机，并且 `error` 永远不允许被当成 `empty`：
 *
 *   idle / loading  → 还没读到
 *   error           → 读不到（可重试）
 *   success         → 读到了；此时才轮到「空」表示"确实没有"
 *
 * 渲染由 `LoadBoundary.vue` 唯一决定，页面只负责数据，不再各自发明措辞。
 *
 * 这个文件是纯函数（不 import vue），所以它可以被 `node --test` 直接运行。
 */

export type LoadState = 'idle' | 'loading' | 'success' | 'error'

export interface AsyncState<T> {
  /** 已成功读到的最后一份数据。失败时保留，绝不因为一次失败而清空。 */
  data: T
  status: LoadState
  /** 已消毒的短原因；为空表示只能给通用文案（见 safeErrorText）。 */
  error: string
}

/** 失败时给人的通用说明。原因不明时不编造原因，只说明发生了什么。 */
export const FALLBACK_ERROR_TEXT = '暂时没能读取这些内容。'

/**
 * 过滤掉不该出现在界面上的东西：HTML、堆栈、node_modules 路径、浏览器/HTTP
 * 层错误。它们对用户没有意义，只会让人以为是自己弄坏了什么。
 *
 * 返回空字符串表示「这条原因不能安全展示」——此时界面只显示通用文案，
 * 而不是把原始错误硬塞给人。
 */
const UNSAFE_ERROR = /<[a-z!/]|traceback|stack trace|node_modules|axios|econn|enotfound|failed to fetch|networkerror|internal server error|err_connection/i

export function safeErrorText(e: unknown, max = 120): string {
  const raw = (e as { message?: unknown } | null | undefined)?.message ?? e
  let text = String(raw ?? '')
  if (!/\S/.test(text)) return ''
  if (UNSAFE_ERROR.test(text)) return ''
  text = text.replace(/\s+/g, ' ').trim()
  return text.length > max ? text.slice(0, max - 1) + '…' : text
}

/** 首次加载前的状态。`idle` 与 `loading` 在界面上同义（都还没读到东西）。 */
export function initial<T>(data: T): AsyncState<T> {
  return { data, status: 'idle', error: '' }
}

/** 开始读取。保留已有数据，这样重试失败也不会连带丢掉用户正在看的内容。 */
export function begin<T>(s: AsyncState<T>): AsyncState<T> {
  return { data: s.data, status: 'loading', error: '' }
}

export function succeed<T>(data: T): AsyncState<T> {
  return { data, status: 'success', error: '' }
}

/**
 * 读取失败。**保留 `s.data`**：一次失败的刷新不该顺手清掉已经读到的东西，
 * 否则失败会被放大成"数据没了"。
 */
export function fail<T>(s: AsyncState<T>, e: unknown): AsyncState<T> {
  return { data: s.data, status: 'error', error: safeErrorText(e) }
}

/** 只有「成功且为空」才允许被当成空。这是整个模块存在的理由。 */
export function isEmpty<T>(s: AsyncState<T>, isEmptyValue: (data: T) => boolean): boolean {
  return s.status === 'success' && isEmptyValue(s.data)
}
