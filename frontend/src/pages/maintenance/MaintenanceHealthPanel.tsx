export default function MaintenanceHealthPanel({ health, onRefresh }: { health: any; onRefresh: () => void }) {
  return (
    <div className="card">
      <h3>健康检查</h3>
      {health ? (
        <div style={{ marginTop: '12px' }}>
          <div style={{ marginBottom: '12px' }}>
            <span style={{ fontWeight: 'bold', color: health.status === 'healthy' ? 'var(--success)' : 'var(--warning)' }}>
              {health.status === 'healthy' ? '✅ 健康' : '⚠️ 降级'}
            </span>
          </div>
          {health.checks && Object.entries(health.checks).map(([key, check]: [string, any]) => (
            <div key={key} style={{ background: 'var(--bg-page)', padding: '10px', borderRadius: '8px', marginBottom: '8px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontWeight: 'bold', fontSize: '14px' }}>{key}</span>
                <span style={{
                  color: check.status === 'ok' ? 'var(--success)' : 'var(--danger)',
                  fontSize: '12px',
                }}>{check.status}</span>
              </div>
              {check.message && (
                <div style={{ fontSize: '12px', color: check.status === 'ok' ? 'var(--text-muted)' : 'var(--danger)', marginTop: '4px', wordBreak: 'break-all' }}>
                  {check.message}
                </div>
              )}
              {check.size_check_error && (
                <div style={{ fontSize: '12px', color: 'var(--warning)', marginTop: '4px', wordBreak: 'break-all' }}>
                  大小检查提示: {check.size_check_error}
                </div>
              )}
              {check.size_mb !== undefined && (
                <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                  数据库大小: {check.size_mb} MB
                  {check.warning && <span style={{ color: 'var(--warning)', marginLeft: '8px' }}>⚠️ 超过100MB</span>}
                </div>
              )}
              {check.total_gb !== undefined && (
                <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                  磁盘: {check.free_gb} GB 可用 / {check.total_gb} GB ({check.percent}%)
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div style={{ color: 'var(--text-muted)' }}>加载中...</div>
      )}
      <button className="btn" onClick={onRefresh} style={{ marginTop: '8px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
        刷新检查
      </button>
    </div>
  )
}
