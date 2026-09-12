/**
 * 时间显示：全站唯一的「UTC 存储 → 上海时区展示」转换点。
 *
 * 后端约定（app/db.py）：时间戳由 SQLite `CURRENT_TIMESTAMP` 写入，是 **naive UTC**
 * 字符串，形如 "2026-09-11 01:35:56"，不带时区标记。
 * 浏览器把 `new Date("2026-09-11 01:35:56")` 按 **本地时区** 解析，直接用会显示错，
 * 所以这里先补 'Z' 按 UTC 解析，再按 Asia/Shanghai 格式化输出。
 *
 * 注意：文档/事件里由 LLM 抽取的日期（如 event.time.start = "2026-07-10"）是文本中的
 * 日历日期而非时刻，不要走这里转换。
 */
const TZ = 'Asia/Shanghai'
const HAS_TZ = /(?:Z|[+-]\d{2}:?\d{2})$/i

type TimeInput = string | number | Date | null | undefined

function toDate(value: TimeInput): Date | null {
  if (value === null || value === undefined || value === '') return null
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value
  if (typeof value === 'number') return new Date(value)
  const s = value.trim()
  if (!s) return null
  const iso = HAS_TZ.test(s) ? s : s.replace(' ', 'T') + 'Z'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d
}

const dateTimeFmt = new Intl.DateTimeFormat('zh-CN', {
  timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
})
const dateFmt = new Intl.DateTimeFormat('zh-CN', {
  timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit',
})

/** "2026-09-11 09:35:56"（上海时间）。空值或非法值返回 "—"。 */
export function fmtDateTime(value: TimeInput): string {
  const d = toDate(value)
  return d ? dateTimeFmt.format(d).replace(/\//g, '-') : '—'
}

/** "2026-09-11"（上海时间）。空值或非法值返回 "—"（文件名等场景可用 fmtDate(new Date())）。 */
export function fmtDate(value: TimeInput): string {
  const d = toDate(value)
  return d ? dateFmt.format(d).replace(/\//g, '-') : '—'
}

/**
 * 相对时间："刚刚 / N 分钟前 / N 小时前 / N 天前"，超过 7 天回落到日期。
 * 用于活动流——用户关心的是"多久之前"，不是精确到秒的时间戳。
 */
export function fmtRelative(value: TimeInput): string {
  const d = toDate(value)
  if (!d) return '—'
  const minutes = Math.floor((Date.now() - d.getTime()) / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} 天前`
  return fmtDate(d)
}
