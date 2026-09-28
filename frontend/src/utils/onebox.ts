/**
 * One Box 的意图与「能不能执行」——从组件里抽出来，因为这两件事必须可测。
 *
 * 这里不实现任何业务：意图判断与步骤路由都在服务端（`app/intent.py` 的 `_route` 是
 * 唯一那份表），前端只做两件事：把意图翻成人话、以及**用一个谓词同时决定按钮是否存在
 * 和 run() 是否真的有事可做**。
 *
 * 上一版之所以会「点了没反应」，就是因为按钮的存在依据（`needs_confirmation`）和真的
 * 有事可做的依据（`steps.length`）是两个不同的条件。现在它们是同一个。
 *
 * 纯函数，不 import vue，可被 `node --test` 直接运行。
 */

export interface PlanStep {
  method?: string
  endpoint: string
  body?: Record<string, unknown>
}

export interface IntentPlan {
  intent: string
  confidence?: number
  reason?: string
  text?: string
  needs_confirmation?: boolean
  suggestions?: string[]
  summary?: string
  steps?: PlanStep[]
}

export const INTENT_LABEL: Record<string, string> = {
  ask: '查你的知识',
  knowledge: '记为知识',
  research: '去研究',
  correct: '纠正知识',
  unknown: '不确定',
}

/** 可以手动改成的意图。`unknown` 不是目的地，所以不在选项里。 */
export const OVERRIDABLE_INTENTS = ['ask', 'knowledge', 'research', 'correct'] as const

const OVERRIDE_LABEL: Record<string, string> = {
  ask: '其实我是想问',
  knowledge: '其实我是想记下来',
  research: '其实我是想研究',
  correct: '其实我是想纠正',
}

/**
 * 除当前意图之外的改法。
 *
 * 把「其实我是想问」显示在系统已经理解为"想问"的时候是废话，所以按当前意图过滤——
 * 用户看到的每个按钮都代表一次真实的改判。
 */
export function overrideOptions(intent: string | undefined | null): { intent: string; label: string }[] {
  return OVERRIDABLE_INTENTS
    .filter(i => i !== intent)
    .map(i => ({ intent: i, label: OVERRIDE_LABEL[i] }))
}

/**
 * 唯一决定「执行按钮是否存在」的谓词。
 *
 * 按钮与 `run()` 必须用同一个判断：只要它返回 false，按钮就不渲染，`run()` 也不会
 * 静默返回——两个条件分开写正是 no-op 的来源。
 */
export function canExecute(plan: IntentPlan | null | undefined): boolean {
  const steps = plan?.steps
  return Array.isArray(steps) && steps.length > 0 && steps.every(s => !!s?.endpoint)
}

/**
 * 改判后的重新规划请求。
 *
 * `intent` 是用户明确指定的，`steps` 仍然由服务端那张唯一的 `_route` 表给出——
 * 前端不复制一份步骤映射（那会变成第二个真相，且注定漂移）。
 */
export function overrideRequest(text: string, intent: string, contextClaimId?: string | null) {
  return { text, intent, context_claim_id: contextClaimId ?? null }
}
