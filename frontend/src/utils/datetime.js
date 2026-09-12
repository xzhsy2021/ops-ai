/**
 * 后端时间戳的统一解析与展示。
 *
 * 约定：后端**落库**的时间戳一律是 naive UTC（无时区标记），例如
 *   "2026-09-12 13:41:21.054406"（SQLAlchemy DateTime → SQLite 用空格分隔）
 *   "2026-09-12T13:41:21.054406"（手写 isoformat()）
 * 浏览器把这种字符串当**本地时间**解析，于是在 UTC+8 下所有页面都比真实时间少 8 小时
 * （相对时间还会把刚刚发生的事说成"8 小时前"）。这里统一补 `Z` 后再解析成本地时间展示。
 *
 * ⚠️ 不要用它处理"文件系统来源"的时间：`sftp.py` 的 `mtime`、备份文件的
 * `created_at/modified_at` 是后端用 `datetime.fromtimestamp()` / `time.localtime()`
 * 生成的**本地时间**；`keys.py` 的 `modified` 是 epoch 秒。这些保持原样解析。
 */

/** 解析后端 naive UTC 时间戳为 Date（本地时区）；无法识别时返回 null */
export function parseBackendTime(value) {
  if (!value) return null
  const text = String(value).trim()
  // 只有时间部分（如 "21:41:33"，来自 agent 日志行）不参与解析，交给调用方回退原文
  if (!/^\d{4}-\d{2}-\d{2}/.test(text)) return null
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(text)
  const normalized = hasZone ? text : `${text.replace(' ', 'T')}Z`
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

function pad(value) {
  return String(value).padStart(2, '0')
}

/** 本地时间字符串，如 2026/9/13 05:41:21（解析失败时回退原文） */
export function formatTime(value, fallback = '-') {
  const date = parseBackendTime(value)
  if (!date) return value ? String(value) : fallback
  return date.toLocaleString()
}

/** 本地时间字符串（固定格式），如 2026-09-13 05:41 */
export function formatDateTime(value, fallback = '-') {
  const date = parseBackendTime(value)
  if (!date) return value ? String(value) : fallback
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 本地日期（YYYY-MM-DD），用于按天分组/过滤的边界比较 */
export function formatDay(value, fallback = '-') {
  const date = parseBackendTime(value)
  if (!date) return value ? String(value).slice(0, 10) : fallback
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 毫秒时间戳（排序用），解析失败返回 0 */
export function backendTimeValue(value) {
  const date = parseBackendTime(value)
  return date ? date.getTime() : 0
}

/** 相对当前时间的描述，如 刚刚 / 12 分钟前 / 3 小时前 / 2 天前 / 3 个月前 / 1 年前 */
export function relativeFromNow(value) {
  const date = parseBackendTime(value)
  if (!date) return ''
  const diff = Date.now() - date.getTime()
  if (diff < 0) return ''
  const minute = 60_000
  const hour = 3_600_000
  const day = 86_400_000
  if (diff < minute) return '刚刚'
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`
  if (diff < day * 30) return `${Math.floor(diff / day)} 天前`
  if (diff < day * 365) return `${Math.floor(diff / (day * 30))} 个月前`
  return `${Math.floor(diff / (day * 365))} 年前`
}
