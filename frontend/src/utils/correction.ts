/**
 * Correction 的起点：**当前那条知识**，而且只有一种构造方式。
 *
 * 之前每个入口各自决定纠正框里填什么：答案旁填的是用户刚问的问题，知识卡里填的是
 * 原文引用。两者都不是"可以被改成另一句话"的陈述，于是用户打开纠正框的第一步就
 * 走偏——他得先自己想清楚"我到底在改什么"。
 *
 * 所以这里只回答一个问题：把一条知识写成人话，长什么样。答案在
 * `buildCorrectionSeed()`，QA / 知识卡 / 对象详情全部走它。
 *
 * 纯函数，不 import vue，可被 `node --test` 直接运行。
 */

/** 构造一句话所需的最小输入——任何 claim 形状都能映射进来。 */
export interface SeedClaim {
  subject?: string
  predicate?: string
  /** 注册表给的人话谓词（`labelOf`）。缺省时退回 `predicate` 本身。 */
  predicateLabel?: string
  object?: string
  /** 只在拿不到 subject/object 时才用的兜底文本。 */
  content?: string
}

/**
 * 一条知识的人话说法：`Apple 的首席执行官 John Ternus`。
 *
 * 只是把三元组读通顺，不新增任何断言——事实已经由 subject / predicate / object
 * 表达完了，这里不发明谓语动词，也不做推断。
 */
export function claimSentence(claim: SeedClaim | null | undefined): string {
  if (!claim) return ''
  const subject = String(claim.subject || '').trim()
  const predicate = String(claim.predicateLabel || claim.predicate || '').trim()
  const object = String(claim.object || '').trim()
  if (!subject && !object) return String(claim.content || '').trim()
  if (!predicate) return [subject, object].filter(Boolean).join(' ').trim()
  return [subject, predicate, object].filter(Boolean).join(' ').trim()
}

export interface CorrectionSeed {
  /** 当前知识（人话），显示为「当前知识：…」。它是要被改掉的东西，不是输入内容。 */
  existing: string
  /** 输入框初始内容：用户可以直接改写的一句话。 */
  seed: string
  /** 起点来自哪里，便于调用方区分处理与测试断言。 */
  from: 'claim' | 'none'
}

/**
 * 纠正的起点。
 *
 * 优先级刻意只有一级：**当前选中的那条知识**。并且刻意**不包含"我刚才问了什么"**——
 * 一个问题句不是可以被改成另一句话的陈述，把它预填进纠正框就是让用户去修改一个他
 * 并不想改的东西。拿不到 claim 时宁可留空、由 placeholder 说明该写什么，也不拿问题凑数。
 */
export function buildCorrectionSeed(claim?: SeedClaim | null): CorrectionSeed {
  const existing = claimSentence(claim)
  if (!existing) return { existing: '', seed: '', from: 'none' }
  return { existing, seed: existing, from: 'claim' }
}
