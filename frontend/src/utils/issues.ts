/**
 * 操作之后的「这次改动弄坏了什么」——统一在这里说一次。
 *
 * 合并、研究采纳、纠正都会产生或移动知识，也就都可能让两条本来各说各话的陈述突然
 * 互相矛盾。以前每个页面自己写 `if (conflict) navigate(...)`，结果是同一个发现有三套
 * 说法、三个去向，而且其中一条指向的界面根本不会列出那条冲突。
 *
 * 现在后端用同一种形状回答（IntegrityIssue），前端也只在一个地方决定怎么讲、去哪看。
 * 页面要做的只是把操作返回的 `issues` 递进来。
 *
 * 两条规则：
 *
 * * **指向真的会列出它的界面。** 合并产生的冲突是「单值关系上出现两个取值」，它不在
 *   冲突中心（那里收极性相反的），而在审核页的知识变化清单里——送错地方比不提示更糟。
 * * **没消息就是好消息。** `issues` 为空时不提示任何东西：一次正常的写入不该也弹一句
 *   「没有发现问题」，那会让这个提示变得可以忽略。
 */
export interface IntegrityIssue {
  kind: string
  severity: string
  title: string
  description: string
  affected_ids: string[]
  available_actions: string[]
}

/** 需要的最小接口，而不是整个 store / router —— 调用方不必为了报告一句话而满足一堆类型。 */
interface ToastHost {
  toast: (msg: string, action?: { label: string; run: () => unknown }) => void
}
interface Destination {
  push: (to: string) => unknown
}

/** 每一类问题能被处置的地方。键是后端的 `kind`，不是页面自己想出来的名字。 */
const DESTINATIONS: Record<string, { path: string; label: string }> = {
  claim_conflict: { path: '/review', label: '处理冲突' },
  duplicate_entity: { path: '/review', label: '查看' },
  object_link: { path: '/review', label: '查看' },
}

const FALLBACK = { path: '/review', label: '查看' }

/** 报告一次操作带回来的问题。返回是否报告了——调用方偶尔需要知道。 */
export function reportIssues(
  issues: IntegrityIssue[] | undefined | null,
  store: ToastHost,
  router: Destination,
): boolean {
  const list = issues || []
  if (!list.length) return false

  const first = list[0]
  const where = DESTINATIONS[first.kind] || FALLBACK
  // 这句话只在操作已经成功之后才可能出现，所以先说「已完成」——否则用户会以为
  // 刚才那一步失败了，而它其实成功了，只是顺带暴露了一个问题。
  const head = list.length === 1
    ? '已完成，但发现 1 个需要你处理的问题'
    : `已完成，但发现 ${list.length} 个需要你处理的问题`
  store.toast(`${head}：${first.title}`, {
    label: where.label,
    run: () => router.push(where.path),
  })
  return true
}
