import CleanupJobForm from './CleanupJobForm'

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  draft: { bg: 'var(--border-strong)', color: 'var(--text-secondary)' },
  ready: { bg: 'var(--brand-surface)', color: 'var(--brand-soft)' },
  pending_approval: { bg: 'var(--warning-surface)', color: 'var(--warning)' },
  approved: { bg: 'var(--success-surface)', color: 'var(--success)' },
  running: { bg: 'var(--brand-surface)', color: 'var(--brand-soft)' },
  paused: { bg: 'var(--warning-surface)', color: 'var(--warning)' },
  completed: { bg: 'var(--success-surface)', color: 'var(--success)' },
  failed: { bg: 'var(--danger-surface)', color: 'var(--danger)' },
  cancelled: { bg: 'var(--border-strong)', color: 'var(--text-secondary)' },
}

const RISK_COLORS: Record<string, { bg: string; color: string }> = {
  low: { bg: 'var(--success-surface)', color: 'var(--success)' },
  medium: { bg: 'var(--warning-surface)', color: 'var(--warning)' },
  high: { bg: 'var(--danger-surface)', color: 'var(--danger)' },
}

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  ready: '就绪',
  pending_approval: '待复核',
  approved: '已复核',
  running: '运行中中',
  paused: '已暂停',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const RISK_LABELS: Record<string, string> = { low: '低', medium: '中', high: '高' }

function Badge({ label, variant, value }: { label: string; variant: 'status' | 'risk'; value: string }) {
  const colors = variant === 'status' ? STATUS_COLORS[value] : RISK_COLORS[value]
  if (!colors) return <span>{label}</span>
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: '4px', fontSize: '12px', fontWeight: 500,
      background: colors.bg, color: colors.color, animation: value === 'running' ? 'pulse 2s infinite' : undefined,
    }}>
      {label}
    </span>
  )
}

interface CleanupJobsTabProps {
  jobStats: { total: number; pending: number; running: number; failed: number }
  jobSearch: string
  statusFilter: string
  jobsLoading: boolean
  showJobForm: boolean
  jobForm: any
  connections: any[]
  tableOptions: string[]
  columnOptions: string[]
  tablesLoading: boolean
  columnsLoading: boolean
  jobSubmitting: boolean
  filteredJobs: any[]
  inputStyle: React.CSSProperties
  selectStyle: React.CSSProperties
  setJobSearch: (value: string) => void
  setStatusFilter: (value: string) => void
  setJobForm: React.Dispatch<React.SetStateAction<any>>
  loadJobs: (status?: string) => void
  onShowCreate: () => void
  onConnectionChange: (connId: string) => void
  onTableChange: (tableName: string) => void
  onSubmit: () => void
  onCancelForm: () => void
  onOpenJob: (job: any) => void
}

export default function CleanupJobsTab(props: CleanupJobsTabProps) {
  const {
    jobStats, jobSearch, statusFilter, jobsLoading, showJobForm, jobForm, connections,
    tableOptions, columnOptions, tablesLoading, columnsLoading, jobSubmitting, filteredJobs,
    inputStyle, selectStyle, setJobSearch, setStatusFilter, setJobForm, loadJobs, onShowCreate,
    onConnectionChange, onTableChange, onSubmit, onCancelForm, onOpenJob,
  } = props

  return (
    <div className="maintenance-workspace">
      <section className="maintenance-overview-grid maintenance-overview-grid--four">
        <div className="maintenance-stat-card"><span>任务总数</span><strong>{jobStats.total}</strong><small>当前筛选范围</small></div>
        <div className="maintenance-stat-card"><span>待处理</span><strong>{jobStats.pending}</strong><small>就绪 / 待复核 / 已复核</small></div>
        <div className="maintenance-stat-card"><span>运行中</span><strong>{jobStats.running}</strong><small>分批执行中</small></div>
        <div className="maintenance-stat-card"><span>失败</span><strong>{jobStats.failed}</strong><small>需要查看详情排查</small></div>
      </section>

      <section className="maintenance-toolbar glass-card">
        <div>
          <h3>数据清理任务</h3>
          <p>先配置连接，再创建清理任务。执行前必须完成 Dry Run、复核和一键确认，系统按批次删除并记录审计。</p>
        </div>
        <div className="maintenance-toolbar-actions">
          <input style={{ ...inputStyle, minWidth: 240 }} placeholder="搜索任务 / 表名 / 连接..." value={jobSearch} onChange={e => setJobSearch(e.target.value)} />
          <select style={{ ...selectStyle, width: 150 }} value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
            <option value="">全部状态</option>
            <option value="draft">草稿</option>
            <option value="ready">就绪</option>
            <option value="pending_approval">待复核</option>
            <option value="approved">已复核</option>
            <option value="running">运行中中</option>
            <option value="paused">已暂停</option>
            <option value="completed">已完成</option>
            <option value="failed">失败</option>
            <option value="cancelled">已取消</option>
          </select>
          <button className="btn" onClick={() => loadJobs(statusFilter || undefined)} disabled={jobsLoading} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
            {jobsLoading ? '刷新中...' : '刷新'}
          </button>
          <button className="btn" onClick={onShowCreate} style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>
            创建清理
          </button>
        </div>
      </section>

      {showJobForm && (
        <CleanupJobForm
          jobForm={jobForm}
          setJobForm={setJobForm}
          connections={connections}
          tableOptions={tableOptions}
          columnOptions={columnOptions}
          tablesLoading={tablesLoading}
          columnsLoading={columnsLoading}
          jobSubmitting={jobSubmitting}
          inputStyle={inputStyle}
          selectStyle={selectStyle}
          onConnectionChange={onConnectionChange}
          onTableChange={onTableChange}
          onSubmit={onSubmit}
          onCancel={onCancelForm}
        />
      )}

      {jobsLoading ? (
        <div className="glass-card empty-state"><strong>加载中...</strong><span>正在获取评估任务列表。</span></div>
      ) : filteredJobs.length === 0 ? (
        <div className="glass-card empty-state"><strong>暂无匹配任务</strong><span>调整筛选条件，或创建新的清理任务。</span></div>
      ) : (
        <section className="cleanup-job-grid">
          {filteredJobs.map((j: any) => (
            <article key={j.id} className="cleanup-job-card" onClick={() => onOpenJob(j)}>
              <div className="cleanup-job-card-head">
                <div>
                  <strong>{j.name}</strong>
                  <span>{j.connection_name} · {j.database_name}</span>
                </div>
                <Badge label={STATUS_LABELS[j.status] || j.status} variant="status" value={j.status} />
              </div>
              <div className="cleanup-job-card-body">
                <div><span>表</span><code>{j.table_name}</code></div>
                <div><span>截止</span><strong>{j.cutoff_time || '-'}</strong></div>
                <div><span>匹配</span><strong>{j.matched_rows ?? '-'}</strong></div>
                <div><span>处理</span><strong>{j.deleted_rows ? `${j.deleted_rows}` : '-'}</strong></div>
              </div>
              <div className="cleanup-job-card-foot">
                {j.risk_level && <Badge label={RISK_LABELS[j.risk_level] || j.risk_level} variant="risk" value={j.risk_level} />}
                <span>{j.created_by || '-'} · {j.created_at || '-'}</span>
                <button className="btn" onClick={e => { e.stopPropagation(); onOpenJob(j) }} style={{ padding: '3px 10px', fontSize: 12, background: 'var(--brand-surface)', color: 'var(--brand-soft)' }}>详情</button>
              </div>
            </article>
          ))}
        </section>
      )}
    </div>
  )
}
