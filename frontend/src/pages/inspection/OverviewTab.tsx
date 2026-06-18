import { EmptyState, RiskBadge, StatusBadge } from '../../components/ui'
import { riskLabel } from './inspectionHelpers'

export type OverviewTabProps = {
  overview: any
  servers: any[]
  projects: any[]
}

export function OverviewTab({ overview, servers, projects }: OverviewTabProps) {
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
              <div className="cc-stat-body"><span>待处理风险</span><strong>{overview.open_issue_count || 0}</strong></div>
            </div>
            <div className="cc-stat-card cc-stat-card--warn">
              <div className="cc-stat-icon">▲</div>
              <div className="cc-stat-body"><span>高 / 中 / 低</span><strong>{overview.high_issue_count || 0} / {overview.medium_issue_count || 0} / {overview.low_issue_count || 0}</strong></div>
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
            <section className="panel-card">
              <h2>基础巡检状态</h2>
              <p className="muted">当前采用单项目轻量架构，不部署服务器侧 Agent；巡检由平台本地/SSH 只读命令完成，AI 只负责摘要、解释、建议和报告生成。</p>
              <div className="mini-card"><strong>最近服务器巡检</strong><div>{overview.latest_server_run?.summary || '暂无服务器巡检记录'}</div></div>
              <div className="mini-card" style={{ marginTop: 8 }}><strong>最近项目巡检</strong><div>{overview.latest_project_run?.summary || '暂无项目巡检记录'}</div></div>
            </section>
            <section className="panel-card">
              <h2>最近风险</h2>
              {(overview.recent_issues || []).length === 0 ? <EmptyState title="暂无待处理风险" description="执行一次服务器巡检或项目巡检后会在这里展示风险。" /> : (
                <div className="table-scroll"><table className="data-table"><thead><tr><th>等级</th><th>标题</th><th>对象</th><th>状态</th></tr></thead><tbody>{overview.recent_issues.map((i: any) => <tr key={i.id}><td><RiskBadge level={i.risk_level} label={riskLabel(i.risk_level)} /></td><td>{i.title}</td><td>{i.project_id || i.server_id || '-'}</td><td><StatusBadge value={i.status} /></td></tr>)}</tbody></table></div>
              )}
            </section>
          </div>
        </>
  )
}
