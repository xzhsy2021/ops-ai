/**
 * 首页健康度派生逻辑（纯函数，便于单测）。
 *
 * 背景（2026-09-12 复盘第 9 轮）：首页用
 *   `const score = backendOnline ? (dashboard?.score ?? 92) : 60`
 *   `const statusText = ... : backendOnline ? '运行正常' : '后端离线'`
 * 把"取数失败/无数据"渲染成了 **92% + 在线 + 运行正常 + 一切正常** 的全绿假象：
 * 只要进程在监听（/health 通），而 /dashboard 接口 500、DB 异常或权限不足，
 * 运维看板就会给出一片绿色，这是比"报错"更危险的展示缺陷。
 *
 * 本模块因此把"有没有真实数据"和"分数是多少"分开表达：
 *   - available=false 时 score 一律为 null，界面显示 '—'，绝不编造数字；
 *   - degraded=true 时给出明确原因（接口失败 / 评分缺失），色调用 warn 而非 ok。
 */

/** 与 DashboardPage 的 HealthTone 保持一致 */
export const TONES = ['ok', 'warn', 'danger', 'neutral']

/** 无数据时的分数占位符（不要用 0/92 之类的数字冒充健康度） */
export const UNKNOWN_SCORE_TEXT = '—'

export function toneForScore(score, backendOnline = true) {
  if (!backendOnline) return 'danger'
  if (typeof score !== 'number' || !Number.isFinite(score)) return 'warn'
  if (score >= 90) return 'ok'
  if (score >= 70) return 'warn'
  return 'danger'
}

/** 服务端 status 字段 → 中文结论（仅在拿到 dashboard 时使用） */
export function statusTextFor(serverStatus) {
  const s = String(serverStatus || '').toLowerCase()
  if (s === 'critical') return '需要处理'
  if (s === 'attention') return '需要关注'
  if (s === 'ok' || s === 'healthy' || s === 'normal') return '运行正常'
  return ''
}

/**
 * @param {{ dashboard?: any, backendOnline?: boolean, error?: string, loading?: boolean }} input
 * @returns {{
 *   available: boolean, degraded: boolean, score: number | null, scoreText: string,
 *   orbPct: number, tone: string, statusText: string, note: string, tags: string[]
 * }}
 */
export function deriveDashboardStatus({ dashboard, backendOnline = true, error = '', loading = false } = {}) {
  const base = {
    available: false,
    degraded: false,
    score: null,
    scoreText: UNKNOWN_SCORE_TEXT,
    orbPct: 0,
    tone: 'neutral',
    statusText: '',
    note: '',
    tags: [],
  }

  if (loading) {
    return { ...base, statusText: '加载中', note: '正在获取工作台数据' }
  }
  if (!backendOnline) {
    return { ...base, tone: 'danger', statusText: '后端离线', note: '后端服务不可达', tags: ['后端离线'] }
  }

  if (!dashboard) {
    return {
      ...base,
      degraded: true,
      tone: 'warn',
      statusText: '数据不可用',
      note: error || '未能获取工作台数据',
      tags: ['工作台数据获取失败'],
    }
  }

  const raw = dashboard.score
  const hasScore = typeof raw === 'number' && Number.isFinite(raw)
  const serverText = statusTextFor(dashboard.status)
  if (!hasScore) {
    return {
      ...base,
      degraded: true,
      tone: 'warn',
      statusText: serverText || '数据不完整',
      note: '后端未返回健康评分',
      tags: ['健康评分缺失'],
    }
  }

  return {
    ...base,
    available: true,
    score: raw,
    scoreText: `${raw}%`,
    orbPct: raw,
    tone: toneForScore(raw, true),
    statusText: serverText || '运行正常',
    note: '',
  }
}
