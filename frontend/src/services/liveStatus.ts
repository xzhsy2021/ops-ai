/**
 * OPS live status bridge — 站点实时态势与只读探针
 * 由原 Neural Lab 的 liveBridge 迁移而来，去除渲染/constellation 依赖，
 * 供 Dashboard SiteStatusPanel 与 Diagnostics ProbeDropdown 复用。
 */
import { systemHealth, taskCenter, serverManagement, capabilityTools, inspection } from '../api'

export type RiskLevel = 'info' | 'warn' | 'alert'

export type LiveSnapshot = {
  score: number
  servers: number
  running: number
  failed: number
  blocked: number
  pendingApprovals: number
  mcpHighRisk: number
  latencyMs: number
  generatedAt?: string
  recentTasks: Array<{
    kind: string
    id: string
    title: string
    status: string
    operator?: string
    target?: string
    started_at?: string
  }>
  toolsCount: number
  openIssues: number
}

export type NodeActionResult = {
  ok: boolean
  result: 'OK' | 'QUEUED' | 'FAIL' | 'SKIP'
  summary: string
  href?: string
}

/** Probe id → navigation path when user wants to jump */
export const NODE_ROUTES: Record<string, string> = {
  'metrics.read': '/',
  'deploy.start': '/deploy',
  'inspect.run': '/inspection',
  'server.list': '/servers',
  'mcp.debug': '/tools',
  'audit.export': '/audit',
  'backup.now': '/maintenance',
  'config.get': '/systems',
}

export function riskFromStatus(status?: string): RiskLevel {
  const s = String(status || '').toLowerCase()
  if (['failed', 'error', 'blocked'].includes(s)) return 'alert'
  if (['running', 'executing', 'pending_approval', 'queued'].includes(s)) return 'warn'
  return 'info'
}

export async function fetchLiveSnapshot(): Promise<LiveSnapshot> {
  const t0 = performance.now()
  const empty: LiveSnapshot = {
    score: 0,
    servers: 0,
    running: 0,
    failed: 0,
    blocked: 0,
    pendingApprovals: 0,
    mcpHighRisk: 0,
    latencyMs: 0,
    recentTasks: [],
    toolsCount: 0,
    openIssues: 0,
  }

  try {
    const [dashRes, taskRes, toolsRes, issueRes] = await Promise.allSettled([
      systemHealth.dashboard(),
      taskCenter.list({ limit: 20, offset: 0 }),
      capabilityTools.list({ limit: 50, include_schema: false }),
      inspection.issues({ status: 'OPEN,PROCESSING', limit: 20, offset: 0 }),
    ])

    const latencyMs = Math.round(performance.now() - t0)
    const snap: LiveSnapshot = { ...empty, latencyMs }

    if (dashRes.status === 'fulfilled') {
      const d: any = (dashRes.value as any)?.data ?? dashRes.value
      snap.score = Number(d?.score ?? d?.health?.score ?? 0)
      snap.servers = Number(d?.metrics?.servers ?? d?.servers?.total ?? 0)
      snap.running = Number(d?.metrics?.running_work ?? d?.deployments?.tasks_by_status?.running ?? 0)
      snap.failed = Number(d?.deployments?.tasks_by_status?.failed ?? 0)
      snap.blocked = Number(d?.deployments?.tasks_by_status?.blocked ?? d?.metrics?.blocked ?? 0)
      snap.pendingApprovals = Number(d?.metrics?.pending_approvals ?? d?.approvals?.total ?? 0)
      snap.mcpHighRisk = Number(d?.metrics?.mcp_high_risk_calls ?? 0)
      snap.generatedAt = d?.generated_at
    }

    if (taskRes.status === 'fulfilled') {
      const t: any = (taskRes.value as any)?.data ?? taskRes.value
      const items = t?.items || t?.tasks || []
      snap.recentTasks = items.map((it: any) => ({
        kind: it.kind,
        id: it.id,
        title: it.title || it.id,
        status: it.status,
        operator: it.operator,
        target: it.target,
        started_at: it.started_at,
      }))
      if (!snap.running) {
        snap.running = items.filter((i: any) => ['running', 'executing'].includes(String(i.status || '').toLowerCase())).length
      }
      if (!snap.failed) {
        snap.failed = items.filter((i: any) => ['failed', 'error'].includes(String(i.status || '').toLowerCase())).length
      }
      if (!snap.blocked) {
        snap.blocked = items.filter((i: any) => ['blocked', 'pending_approval'].includes(String(i.status || '').toLowerCase())).length
      }
    }

    if (toolsRes.status === 'fulfilled') {
      const t: any = (toolsRes.value as any)?.data ?? toolsRes.value
      snap.toolsCount = Number(t?.total ?? t?.tools?.length ?? t?.items?.length ?? 0)
    }

    if (issueRes.status === 'fulfilled') {
      const t: any = (issueRes.value as any)?.data ?? issueRes.value
      snap.openIssues = Number(t?.total ?? t?.items?.length ?? 0)
    }

    return snap
  } catch {
    return { ...empty, latencyMs: Math.round(performance.now() - t0) }
  }
}

/** Execute a read-only probe for the given node */
export async function runNodeProbe(nodeId: string): Promise<NodeActionResult> {
  try {
    switch (nodeId) {
      case 'metrics.read': {
        const res: any = await systemHealth.dashboard()
        const d = res?.data ?? res
        return {
          ok: true,
          result: 'OK',
          summary: `健康分 ${d?.score ?? '—'} · 服务器 ${d?.metrics?.servers ?? '—'} · 运行中 ${d?.metrics?.running_work ?? 0}`,
          href: '/',
        }
      }
      case 'deploy.start': {
        const res: any = await taskCenter.list({ kind: 'deploy', limit: 5, offset: 0 })
        const d = res?.data ?? res
        const n = d?.total ?? d?.items?.length ?? 0
        return {
          ok: true,
          result: n > 0 ? 'OK' : 'SKIP',
          summary: `最近发布任务 ${n} 条`,
          href: '/deploy',
        }
      }
      case 'inspect.run': {
        const res: any = await inspection.issues({ status: 'OPEN,PROCESSING', limit: 5, offset: 0 })
        const d = res?.data ?? res
        const n = Number(d?.total ?? d?.items?.length ?? 0)
        return {
          ok: true,
          result: n > 0 ? 'QUEUED' : 'OK',
          summary: `未闭环风险 ${n} 项`,
          href: '/inspection',
        }
      }
      case 'server.list': {
        const res: any = await serverManagement.list()
        const d = res?.data ?? res
        const items = d?.items || d?.servers || (Array.isArray(d) ? d : [])
        const n = items.length || Number(d?.total || 0)
        return {
          ok: true,
          result: 'OK',
          summary: `资产服务器 ${n} 台`,
          href: '/servers',
        }
      }
      case 'mcp.debug': {
        const res: any = await capabilityTools.list({ limit: 30, include_schema: false })
        const d = res?.data ?? res
        const n = Number(d?.total ?? d?.tools?.length ?? 0)
        return {
          ok: true,
          result: 'OK',
          summary: `已注册 MCP/工具 ${n} 个`,
          href: '/tools',
        }
      }
      case 'audit.export': {
        return {
          ok: true,
          result: 'OK',
          summary: '审计导出入口已就绪（只读跳转）',
          href: '/audit',
        }
      }
      case 'backup.now': {
        return {
          ok: true,
          result: 'QUEUED',
          summary: '备份/维护需在维护页确认执行（人在回路）',
          href: '/maintenance',
        }
      }
      case 'config.get': {
        return {
          ok: true,
          result: 'OK',
          summary: '配置中心可查看项目/服务/环境',
          href: '/systems',
        }
      }
      default:
        return { ok: false, result: 'SKIP', summary: `未知节点 ${nodeId}` }
    }
  } catch (e: any) {
    return {
      ok: false,
      result: 'FAIL',
      summary: e?.message || String(e),
    }
  }
}
