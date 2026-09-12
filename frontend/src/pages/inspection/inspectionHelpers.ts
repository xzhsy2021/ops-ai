import { formatTime as formatBackendTime } from '../../utils/datetime.js'

/** 后端落库时间戳（naive UTC）→ 本地时间展示；纯 "HH:MM:SS" 之类回退原文 */
export function formatTime(value?: string | null) {
  return formatBackendTime(value)
}

export function riskLabel(level?: string) {
  const map: Record<string, string> = { HIGH: '高危', MEDIUM: '中危', LOW: '低危', NONE: '无风险' }
  return map[(level || '').toUpperCase()] || level || '-'
}

export function scoreTone(score?: number) {
  const s = Number(score || 0)
  if (s >= 90) return 'success'
  if (s >= 70) return 'warning'
  return 'danger'
}

export function normalizeServerStatus(s: any) {
  const raw = String(s?.status ?? (s?.enabled === false ? 'disabled' : 'online')).toLowerCase()
  if (['disabled', 'disable', 'stopped', 'stop', 'inactive', '停用'].includes(raw)) return 'disabled'
  if (['offline', 'down', '离线'].includes(raw)) return 'offline'
  return 'online'
}

export function isServerInspectable(s: any) {
  return s?.inspectable !== false && normalizeServerStatus(s) === 'online'
}

export function serverStatusText(s: any) {
  const status = normalizeServerStatus(s)
  if (status === 'disabled') return '停用'
  if (status === 'offline') return '离线'
  return '在线/启用'
}

export function extractShellCommand(raw?: string | null, command?: string | null) {
  if (command) return command
  const text = String(raw || '')
  if (!text) return ''
  if (text.startsWith('$ ')) {
    const idx = text.indexOf('\nexit=')
    return idx > 0 ? text.slice(2, idx).trim() : text.slice(2).split('\n#')[0].trim()
  }
  return ''
}

export function formatExecutionEntry(l: any) {
  const command = extractShellCommand(l.raw_output, l.command)
  const raw = String(l.raw_output || '')
  const output = raw && raw !== command ? raw : ''
  return [
    `[${formatTime(l.time || l.created_at)}] ${l.category || ''} / ${l.item_name || ''} / ${l.status || ''} / ${l.risk_level || ''}`,
    l.message || '',
    command ? `可直接验证的 Shell 命令:\n${command}` : '',
    output ? `执行输出:\n${output}` : '',
  ].filter(Boolean).join('\n')
}
