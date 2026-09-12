import { useEffect, useState } from 'react'
import { EmptyState, RiskBadge, StatusBadge } from '../../components/ui'
import { inspection } from '../../api'
import { formatTime, riskLabel } from './inspectionHelpers'

export type OverviewTabProps = {
  overview: any
  servers: any[]
  projects: any[]
  /**
   * 打开风险问题详情。统一由父级（巡检中心）持有弹窗实例：
   * 这样"查看所属巡检详情"等跨弹窗跳转只有一个遮罩，不会出现互相遮挡。
   */
  onOpenIssue?: (issueId: string) => void
}

function runningRunLabel(r: any): string {
  if (r.job_kind === 'operation_job') return r.title || r.source_tool || '后台工具任务'
  if (r.scope_type === 'PROJECT') return `项目 ${r.project_id || '-'}`
  if (r.scope_type === 'PROJECT_COMBINED') return `综合 ${r.project_id || '-'}`
  return `服务器 ${r.server_id || '-'}`
}

export function OverviewTab({ overview, servers, projects, onOpenIssue }: OverviewTabProps) {
  const [runningRuns, setRunningRuns] = useState<any[]>(overview?.running_runs || [])

  useEffect(() => {
    let disposed = false
    const load = async () => {
      try {
        const res: any = await inspection.overview()
        if (!disposed) setRunningRuns(res.data?.running_runs || [])
      } catch {
        // transient; keep polling
      }
    }
    load()
    const t = window.setInterval(load, 4000)
    return () => { disposed = true; window.clearInterval(t) }
  }, [])

  const hasRunning = runningRuns.length > 0

  return (
        <>
          <div className="cc-stat-grid">
            <div className="cc-stat-card">
              <div className="cc-stat-icon">⬡</div>
              <div className="cc-stat-body"><span>服务器资产</span><strong>{overview.server_count || servers.length || 0}</strong></div>
            </div>
            <div className="cc-stat-card cc-stat-card--info">
              <div className="cc-stat-icon">◇</div>
              <div className="cc-stat-body"><span>项目资产</span><strong>{overview.project_count || projects.length || 0}</strong></div>
            </div>
            <div className="cc-stat-card cc-stat-card--danger">
              <div className="cc-stat-icon">!</div>
              <div className="cc-stat-body" title={`未闭环 = 待处理 ${overview.pending_issue_count ?? overview.open_issue_count ?? 0} + 处理中 ${overview.processing_issue_count ?? 0}；与「风险问题」列表默认口径一致`}><span>未闭环风险</span><strong>{overview.open_issue_count || 0}</strong></div>
            </div>
            <div className="cc-stat-card cc-stat-card--warn">
              <div className="cc-stat-icon">▲</div>
              <div className="cc-stat-body"><span>高 / 中 / 低</span><strong>{overview.high_issue_count || 0} / {overview.medium_issue_count || 0} / {overview.low_issue_count || 0}</strong></div>
            </div>
          </div>

          <section className="panel-card" style={{ marginTop: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
              <h2 style={{ margin: 0 }}>当前执行进度</h2>
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>每 4 秒自动刷新 · {hasRunning ? `${runningRuns.length} 个任务执行中` : '空闲'}</span>
            </div>
            {!hasRunning ? (
              <p className="muted" style={{ marginTop: 8 }}>当前没有执行中的巡检任务，启动服务器/项目/批量巡检后这里会实时展示进度。</p>
            ) : (
              <div style={{ display: 'grid', gap: 10, marginTop: 10 }}>
                {runningRuns.map((r: any) => {
                  const pct = Math.min(100, Math.max(0, r.progress_percent ?? 0))
                  const running = r.status === 'RUNNING' || r.status === 'PENDING'
                  return (
                    <div key={r.id} className="mini-card" style={{ margin: 0 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                          <strong>{runningRunLabel(r)}</strong>
                          <StatusBadge value={r.status} />
                          <small className="muted" style={{ fontSize: 12 }}>{r.scope_type === 'PROJECT' || r.scope_type === 'PROJECT_COMBINED' || r.job_kind === 'operation_job' ? '' : `评分 ${r.score ?? '-'}`}</small>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                          <span className="muted" style={{ fontSize: 12 }}>{formatTime(r.created_at)}</span>
                          <span className="muted" style={{ fontSize: 12 }}>已用 {(r.duration_ms ?? 0) / 1000}s</span>
                        </div>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
                        <div style={{ flex: 1, height: 8, borderRadius: 6, background: 'rgba(15,23,42,.08)', overflow: 'hidden' }}>
                          <div style={{ width: `${pct}%`, height: '100%', background: running ? 'var(--brand, #3182ce)' : 'var(--success, #2f855a)', transition: 'width .5s ease' }} />
                        </div>
                        <span style={{ fontSize: 12, width: 48, textAlign: 'right' }}>{pct}%</span>
                        <span className="muted" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{r.items_done ?? 0}/{r.items_total ?? 0} 项</span>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </section>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16, marginTop: 16 }}>
            <section className="panel-card">
              <h2>基础巡检状态</h2>
              <p className="muted">当前采用单项目轻量架构，不部署服务器侧 Agent；巡检由平台本地/SSH 只读命令完成，AI 只负责摘要、解释、建议和报告生成。</p>
              <div className="mini-card"><strong>最近服务器巡检</strong><div>{overview.latest_server_run?.summary || '暂无服务器巡检记录'}</div></div>
              <div className="mini-card" style={{ marginTop: 8 }}><strong>最近项目巡检</strong><div>{overview.latest_project_run?.summary || '暂无项目巡检记录'}</div></div>
            </section>
            <section className="panel-card">
              <h2>最近风险</h2>
              {(overview.recent_issues || []).length === 0 ? <EmptyState title="暂无待处理风险" description="执行一次服务器巡检或项目巡检后会在这里展示风险。" /> : (
                <div className="table-scroll"><table className="data-table"><thead><tr><th>等级</th><th>标题</th><th>对象</th><th>状态</th><th style={{ width: 80 }}>操作</th></tr></thead><tbody>{overview.recent_issues.map((i: any) => <tr key={i.id} style={{ cursor: 'pointer' }} onClick={() => onOpenIssue?.(i.id)}><td><RiskBadge level={i.risk_level} label={riskLabel(i.risk_level)} /></td><td>{i.title}</td><td>{i.project_id || i.server_id || '-'}</td><td><StatusBadge value={i.status} /></td><td><button className="btn btn-subtle" onClick={(e) => { e.stopPropagation(); onOpenIssue?.(i.id) }}>详情</button></td></tr>)}</tbody></table></div>
              )}
            </section>
          </div>
        </>
  )
}