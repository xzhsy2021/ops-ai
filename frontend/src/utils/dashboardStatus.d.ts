export type HealthTone = 'ok' | 'warn' | 'danger' | 'neutral'

export type DashboardStatus = {
  /** 是否拿到了可用的健康评分（false 时 score 必为 null，界面显示 '—'） */
  available: boolean
  /** 后端在线但数据缺失/接口失败：需要显式提示，而不是显示绿色 */
  degraded: boolean
  score: number | null
  scoreText: string
  orbPct: number
  tone: HealthTone
  statusText: string
  note: string
  tags: string[]
}

export const TONES: string[]
export const UNKNOWN_SCORE_TEXT: string

export function toneForScore(score: number | null | undefined, backendOnline?: boolean): HealthTone
export function statusTextFor(serverStatus?: string | null): string
export function deriveDashboardStatus(input?: {
  dashboard?: any
  backendOnline?: boolean
  error?: string
  loading?: boolean
}): DashboardStatus
