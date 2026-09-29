export async function api<T = unknown>(path: string, opt?: RequestInit): Promise<T> {
  let r: Response
  try {
    r = await fetch(path, opt)
  } catch {
    // fetch 只在网络层失败时 reject（后端未启动 / 连接被拒 / 断网），拿不到任何响应。
    // 浏览器给的原文是「Failed to fetch」——它只告诉用户"坏了"，不告诉用户能做什么。
    throw new Error('无法连接到服务，请确认后端已启动后重试')
  }
  const text = await r.text()
  let j: unknown = null
  try { j = text ? JSON.parse(text) : null } catch { j = null }
  if (!r.ok) {
    const detail =
      (j && typeof j === 'object' && ((j as any).detail || (j as any).message)) ||
      text || `HTTP ${r.status}`
    throw new Error(String(detail))
  }
  if (j === null && text) throw new Error(`Non-JSON response: ${text.slice(0, 300)}`)
  return j as T
}

export const post = <T = any>(p: string, body?: unknown) =>
  api<T>(p, {
    method: 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })

export const postForm = <T = any>(p: string, form: FormData) => api<T>(p, { method: 'POST', body: form })

export const put = <T = unknown>(p: string, body: unknown) =>
  api<T>(p, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const patchJson = <T = unknown>(p: string, body: unknown) =>
  api<T>(p, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const del = (p: string) => api(p, { method: 'DELETE' })
