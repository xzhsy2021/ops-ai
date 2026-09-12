/** 解析后端 naive UTC 时间戳为 Date；无法识别（如纯 "HH:MM:SS"）时返回 null */
export function parseBackendTime(value?: string | null): Date | null

/** 本地时间字符串，如 2026/9/13 05:41:21（解析失败时回退原文） */
export function formatTime(value?: string | null, fallback?: string): string

/** 本地时间字符串（固定格式），如 2026-09-13 05:41 */
export function formatDateTime(value?: string | null, fallback?: string): string

/** 本地日期（YYYY-MM-DD），用于按天分组/过滤的边界比较 */
export function formatDay(value?: string | null, fallback?: string): string

/** 毫秒时间戳（排序用），解析失败返回 0 */
export function backendTimeValue(value?: string | null): number

/** 相对当前时间的描述，如 刚刚 / 12 分钟前 / 3 小时前 */
export function relativeFromNow(value?: string | null): string
