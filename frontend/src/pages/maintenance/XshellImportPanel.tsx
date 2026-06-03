interface XshellImportPanelProps {
  xshellLoading: boolean
  xshellResult: any
  onImport: () => void
}

export default function XshellImportPanel({ xshellLoading, xshellResult, onImport }: XshellImportPanelProps) {
  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h3 style={{ margin: '0 0 8px 0' }}>Xshell 会话导入</h3>
          <p style={{ margin: 0, color: 'var(--text-muted)', fontSize: '13px' }}>
            从本地 Xshell Sessions 目录自动扫描 SSH 会话配置，将未重复的服务器批量导入当前平台
          </p>
        </div>
        <button className="btn" onClick={onImport} disabled={xshellLoading}
          style={{ padding: '10px 24px', background: 'var(--action-bg)', color: 'var(--action-text)', fontSize: '14px', fontWeight: 'bold', whiteSpace: 'nowrap' }}>
          {xshellLoading ? '导入中...' : '从 Xshell 导入'}
        </button>
      </div>

      {xshellResult && (
        <div style={{ marginTop: '16px', borderTop: '1px solid var(--border-strong)', paddingTop: '16px' }}>
          <div style={{ display: 'flex', gap: '24px', marginBottom: '16px' }}>
            <div style={{ textAlign: 'center', background: 'var(--success-surface)', borderRadius: '8px', padding: '12px 24px', flex: 1 }}>
              <div style={{ fontSize: '24px', fontWeight: 'bold', color: 'var(--success)' }}>{xshellResult.imported}</div>
              <div style={{ fontSize: '13px', color: 'var(--success)' }}>已导入</div>
            </div>
            <div style={{ textAlign: 'center', background: 'var(--warning-surface)', borderRadius: '8px', padding: '12px 24px', flex: 1 }}>
              <div style={{ fontSize: '24px', fontWeight: 'bold', color: 'var(--warning)' }}>{xshellResult.skipped || 0}</div>
              <div style={{ fontSize: '13px', color: 'var(--warning)' }}>已跳过(重复)</div>
            </div>
          </div>

          {xshellResult.servers && xshellResult.servers.length > 0 && (
            <div>
              <div style={{ color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '8px', fontWeight: 'bold' }}>
                已导入服务器列表:
              </div>
              {xshellResult.groups && xshellResult.groups.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '10px' }}>
                  {xshellResult.groups.map((g: any, i: number) => (
                    <span key={i} style={{ fontSize: '12px', padding: '3px 10px', borderRadius: '4px', background: 'var(--brand-surface)', color: 'var(--action-text)' }}>
                      {g.name} ({g.count})
                    </span>
                  ))}
                </div>
              )}
              <div style={{ display: 'grid', gap: '6px', maxHeight: '300px', overflowY: 'auto' }}>
                {xshellResult.servers.map((s: any, i: number) => (
                  <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--bg-page)', padding: '8px 12px', borderRadius: '6px', fontSize: '13px' }}>
                    <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
                      <span style={{ color: 'var(--brand)', fontWeight: 'bold' }}>{s.name}</span>
                      <span style={{ color: 'var(--text-secondary)', fontFamily: 'monospace' }}>{s.host}:{s.port || 22}</span>
                      {s.group && (
                        <span style={{ fontSize: '11px', padding: '1px 6px', borderRadius: '4px', background: 'var(--brand-surface)', color: 'var(--action-text)' }}>
                          {s.group}
                        </span>
                      )}
                    </div>
                    <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                      <span style={{ color: 'var(--text-muted)' }}>{s.user || s.username}</span>
                      <span style={{ fontSize: '11px', padding: '1px 6px', borderRadius: '4px', background: s.key_file ? 'var(--success-surface)' : 'var(--warning-surface)', color: s.key_file ? 'var(--success)' : 'var(--warning)' }}>
                        {s.key_file ? '密钥' : '密码'}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {xshellResult.skipped_servers && xshellResult.skipped_servers.length > 0 && (
            <div style={{ marginTop: '12px' }}>
              <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginBottom: '6px' }}>
                已跳过 ({xshellResult.skipped_servers.length} 台):
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                {xshellResult.skipped_servers.map((s: any, i: number) => (
                  <span key={i} style={{ fontSize: '12px', background: 'var(--bg-surface)', color: 'var(--text-muted)', padding: '2px 8px', borderRadius: '4px' }}>{s.name}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
