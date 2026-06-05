import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ROUTES } from '../routes'
import { adminMaintenance, systemHealth } from '../api'
import { PageHeader } from '../components/ui'

type Check = Record<string, any>

function statusText(status: string) {
  if (status === 'ok') return '正常'
  if (status === 'warn') return '警告'
  if (status === 'error') return '异常'
  return status || '-'
}

function statusColor(status: string) {
  if (status === 'ok') return 'var(--success)'
  if (status === 'warn') return 'var(--warning)'
  if (status === 'error') return 'var(--danger)'
  return 'var(--text-muted)'
}


function severityColor(severity: string) {
  if (severity === 'high' || severity === 'critical') return 'var(--danger)'
  if (severity === 'medium') return 'var(--warning)'
  return 'var(--success)'
}

function RecommendationPanel({ items }: { items: any[] }) {
  if (!items?.length) return null
  return (
    <div className="card" style={{ display: 'grid', gap: '10px' }}>
      <div>
        <h3 style={{ margin: 0 }}>问题建议</h3>
        <p style={{ margin: '6px 0 0 0', color: 'var(--text-muted)', fontSize: '13px' }}>根据当前健康检查生成的处理建议，优先处理高风险和中风险项。</p>
      </div>
      {items.slice(0, 5).map((item) => (
        <div key={item.key} style={{ background: 'var(--bg-page)', borderRadius: '10px', padding: '10px', border: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '10px' }}>
            <strong>{item.title}</strong>
            <span style={{ color: severityColor(item.severity), fontWeight: 700 }}>{item.severity || 'low'}</span>
          </div>
          <div style={{ color: 'var(--text-muted)', marginTop: '4px', fontSize: '13px' }}>{item.reason}</div>
          {item.actions?.length > 0 && (
            <ol style={{ margin: '8px 0 0 18px', padding: 0, color: 'var(--text-secondary)', fontSize: '13px' }}>
              {item.actions.slice(0, 3).map((action: string, index: number) => <li key={index} style={{ marginBottom: '3px' }}>{action}</li>)}
            </ol>
          )}
        </div>
      ))}
    </div>
  )
}

function CheckCard({ name, check }: { name: string; check: Check }) {
  const items = check.items && typeof check.items === 'object' ? Object.entries(check.items) : []
  return (
    <div className="card" style={{ minHeight: '150px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'flex-start' }}>
        <div>
          <h3 style={{ margin: 0 }}>{name}</h3>
          <p style={{ margin: '8px 0 0 0', color: 'var(--text-muted)', fontSize: '13px' }}>{check.message || '无附加说明'}</p>
        </div>
        <span style={{ color: statusColor(check.status), fontWeight: 700 }}>{statusText(check.status)}</span>
      </div>

      <div style={{ marginTop: '12px', display: 'grid', gap: '6px', fontSize: '13px', color: 'var(--text-secondary)' }}>
        {check.path && <div>路径：<span style={{ fontFamily: 'monospace', color: 'var(--text-primary)' }}>{check.path}</span></div>}
        {check.size_mb !== undefined && <div>大小：{check.size_mb} MB</div>}
        {check.free_gb !== undefined && <div>磁盘：{check.free_gb} GB 可用 / {check.total_gb} GB，使用率 {check.percent}%</div>}
        {check.latest_file && <div>最近备份：<span style={{ fontFamily: 'monospace', color: 'var(--text-primary)' }}>{check.latest_file}</span></div>}
        {check.latest_at && <div>备份时间：{check.latest_at}，约 {check.latest_age_hours} 小时前</div>}
        {check.started_at && <div>启动时间：{check.started_at}</div>}
        {check.configured !== undefined && <div>已配置：{check.configured ? '是' : '否'}</div>}
      </div>

      {items.length > 0 && (
        <div style={{ marginTop: '12px', display: 'grid', gap: '6px' }}>
          {items.map(([key, value]: [string, any]) => (
            <div key={key} style={{ background: 'var(--bg-page)', borderRadius: '8px', padding: '8px', fontSize: '12px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px' }}>
                <strong>{key}</strong>
                <span style={{ color: statusColor(value.status) }}>{statusText(value.status)}</span>
              </div>
              <div style={{ color: 'var(--text-muted)', marginTop: '4px', wordBreak: 'break-all' }}>{value.path || value.message}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function SystemStatusPage() {
  const [health, setHealth] = useState<any>(null)
  const [backups, setBackups] = useState<any[]>([])
  const [buildInfo, setBuildInfo] = useState<any>(null)
  const [snapshot, setSnapshot] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')

  const refresh = async () => {
    setLoading(true)
    try {
      const [healthRes, backupsRes, buildRes, snapRes]: any = await Promise.all([
        systemHealth.health(),
        adminMaintenance.backups({ verify_latest: true }).catch(() => ({ data: [] })),
        systemHealth.buildInfo().catch(() => ({ data: null })),
        systemHealth.snapshot().catch(() => ({ data: null })),
      ])
      setHealth(healthRes.data || healthRes)
      setBackups(backupsRes.data || [])
      setBuildInfo(buildRes?.data || buildRes || null)
      setSnapshot(snapRes?.data || snapRes || null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const createBackup = async () => {
    setLoading(true)
    try {
      const res: any = await adminMaintenance.backupDb()
      const file = res?.data?.file
      setMessage(file ? `备份已创建并校验：${file}` : '备份已创建并校验')
      await refresh()
      setTimeout(() => setMessage(''), 3000)
    } catch (e) {
      setMessage('备份失败，请到维护页面查看详情')
    } finally {
      setLoading(false)
    }
  }


  const copySystemReport = async () => {
    const text = JSON.stringify({ health, backups: backups.slice(0, 10), build_info: buildInfo }, null, 2)
    try {
      await navigator.clipboard?.writeText(text)
      setMessage('系统诊断信息已复制')
      setTimeout(() => setMessage(''), 3000)
    } catch {
      setMessage('复制失败，请打开安装诊断导出报告')
    }
  }

  const checks = health?.checks || {}
  const summary = health?.summary || {}
  const recommendations = health?.recommendations || []

  return (
    <div style={{ display: 'grid', gap: '20px' }}>
      <PageHeader
        title="系统状态"
        description="本地运行环境、密钥、目录、备份、发布 Worker 与磁盘状态"
        actions={
          <>
            <button className="btn" onClick={refresh} disabled={loading}>
              {loading ? '刷新中...' : '刷新'}
            </button>
            <Link className="btn" to={ROUTES.diagnostics}>安装诊断</Link>
            <Link className="btn btn-primary" to={ROUTES.inspection}>进入巡检中心</Link>
            <button className="btn" onClick={copySystemReport} disabled={!health}>复制诊断信息</button>
            <button className="btn btn-primary" onClick={createBackup} disabled={loading}>立即备份数据库</button>
          </>
        }
      />

      {message && <div className="card" style={{ color: message.includes('失败') ? 'var(--danger)' : 'var(--success)' }}>{message}</div>}

      {health ? (
        <>
          <div className="system-status-summary">
            <div className="stat-card">
              <span className="stat-card-label">总体状态</span>
              <strong style={{ color: health.status === 'healthy' ? 'var(--success)' : health.status === 'degraded' ? 'var(--warning)' : 'var(--danger)' }}>
                {health.status === 'healthy' ? '健康' : health.status === 'degraded' ? '降级' : '异常'}
              </strong>
            </div>
            <div className="stat-card"><span className="stat-card-label">检查项</span><strong>{summary.checks || 0}</strong></div>
            <div className="stat-card"><span className="stat-card-label">警告</span><strong style={{ color: summary.warnings ? 'var(--warning)' : 'var(--success)' }}>{summary.warnings || 0}</strong></div>
            <div className="stat-card"><span className="stat-card-label">异常</span><strong style={{ color: summary.errors ? 'var(--danger)' : 'var(--success)' }}>{summary.errors || 0}</strong></div>
          </div>

          <div className="card system-status-info">
            <div className="system-status-info-item">
              <span className="label">前端构建</span>
              <strong style={{ color: statusColor(buildInfo?.status || 'ok') }}>{statusText(buildInfo?.status || 'ok')}</strong>
              <span className="detail">{buildInfo?.message || '构建信息读取中'}</span>
            </div>
            <div className="system-status-info-item">
              <span className="label">前端版本 / 构建时间</span>
              <strong>{buildInfo?.frontend?.version || '-'}</strong>
              <span className="detail">{buildInfo?.frontend?.built_at || '-'}</span>
            </div>
            <div className="system-status-info-item">
              <span className="label">后端版本 / 启动时间</span>
              <strong>{buildInfo?.backend?.version || '-'}</strong>
              <span className="detail">{buildInfo?.backend?.started_at || '-'}</span>
            </div>
            <div className="system-status-info-item">
              <span className="label">dist 状态</span>
              <strong>{buildInfo?.frontend?.js_assets ?? '-'} JS / {buildInfo?.frontend?.css_assets ?? '-'} CSS</strong>
              <span className="detail" style={{ color: buildInfo?.frontend?.dist_stale ? 'var(--warning)' : 'var(--text-muted)' }}>
                {buildInfo?.frontend?.dist_stale ? '源码较新，请重新构建' : 'dist 与源码时间正常'}
              </span>
            </div>
          </div>

          {snapshot && (
            <div className="card system-status-info">
              <div className="system-status-info-item">
                <span className="label">运行时间</span>
                <strong>{snapshot.runtime?.process?.uptime_seconds ? `${Math.floor(snapshot.runtime.process.uptime_seconds / 3600)}h ${Math.floor((snapshot.runtime.process.uptime_seconds % 3600) / 60)}m` : '-'}</strong>
                <span className="detail">PID: {snapshot.runtime?.process?.pid || '-'}</span>
              </div>
              <div className="system-status-info-item">
                <span className="label">活跃发布任务</span>
                <strong>{snapshot.runtime?.deploy_tasks_by_status?.running ?? 0}</strong>
                <span className="detail">24h 工具调用: {snapshot.runtime?.recent_tool_calls_24h ?? 0}</span>
              </div>
              <div className="system-status-info-item">
                <span className="label">托管存储</span>
                <strong>{snapshot.storage?.total_managed_size_human || '-'}</strong>
                <span className="detail">数据库: {snapshot.storage?.buckets?.database?.size_human || '-'}</span>
              </div>
              <div className="system-status-info-item">
                <span className="label">缓存命中</span>
                <strong style={{ color: snapshot.cache?.hit ? 'var(--success)' : 'var(--text-muted)' }}>{snapshot.cache?.hit ? '是' : '否'}</strong>
                <span className="detail">TTL: {snapshot.cache?.ttl_seconds ?? '-'}s</span>
              </div>
            </div>
          )}

          <RecommendationPanel items={recommendations} />

          {checks.backups?.stale && (
            <div className="card" style={{ border: '1px solid var(--warning)', color: 'var(--warning)' }}>
              最近一次备份已超过 7 天。建议点击“立即备份数据库”，或使用 scripts/backup.sh 做完整运行目录备份。
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px' }}>
            {Object.entries(checks).map(([name, check]: [string, any]) => <CheckCard key={name} name={name} check={check} />)}
          </div>

          <div className="card">
            <h3 style={{ marginTop: 0 }}>最近数据库备份</h3>
            <div style={{ display: 'grid', gap: '8px' }}>
              {backups.slice(0, 5).map((b) => (
                <div key={b.file} className="system-backup-row">
                  <span style={{ fontFamily: 'monospace' }}>{b.file}</span>
                  <span style={{ color: 'var(--text-muted)' }}>{b.created_at} · {b.size_human || `${(b.size / 1024 / 1024).toFixed(2)} MB`}</span>
                </div>
              ))}
              {backups.length === 0 && <div style={{ color: 'var(--text-muted)' }}>暂无数据库备份</div>}
            </div>
          </div>
        </>
      ) : (
        <div className="page-loading"><span className="loading-orb" /><div>加载系统状态...</div></div>
      )}
    </div>
  )
}
