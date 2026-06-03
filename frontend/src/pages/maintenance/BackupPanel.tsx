interface BackupPanelProps {
  backups: any[]
  backupDownloadUrl: (file: string) => string
  onBackup: () => void
  onRefreshBackups: () => void
  onExport: () => void
  onImportClick: () => void
  onFileImport: (event: any) => void
  onRestore: (file: string) => void
  onVerify: (file: string) => void
  onDelete: (file: string) => void
}

function verificationLabel(backup: any): { text: string; color: string } {
  const status = backup?.verification?.status
  if (status === 'ok') return { text: '已校验', color: 'var(--success)' }
  if (status === 'failed') return { text: '校验失败', color: 'var(--danger)' }
  return { text: '未校验', color: 'var(--text-muted)' }
}

export default function BackupPanel({
  backups,
  backupDownloadUrl,
  onBackup,
  onRefreshBackups,
  onExport,
  onImportClick,
  onFileImport,
  onRestore,
  onVerify,
  onDelete,
}: BackupPanelProps) {
  return (
    <div className="card">
      <h3>配置导入导出</h3>
      <div style={{ display: 'flex', gap: '12px', marginTop: '12px', flexWrap: 'wrap' }}>
        <button className="btn" onClick={onExport}
          style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>导出配置</button>
        <button className="btn" onClick={onImportClick}
          style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>导入配置</button>
        <input id="importFile" type="file" accept=".json" onChange={onFileImport}
          style={{ display: 'none' }} />
      </div>

      <h3 style={{ marginTop: '20px' }}>数据库备份</h3>
      <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginTop: '4px' }}>
        恢复和删除已经接入统一风险确认弹窗与强确认短语。恢复前系统会自动创建安全备份，恢复后请重启后端进程。
      </div>
      <div style={{ display: 'flex', gap: '12px', marginTop: '12px', flexWrap: 'wrap' }}>
        <button className="btn" onClick={onBackup}
          style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>创建并校验备份</button>
        <button className="btn" onClick={onRefreshBackups}
          style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>刷新列表</button>
      </div>
      <div style={{ maxHeight: '280px', overflow: 'auto', marginTop: '12px' }}>
        {backups.map((b) => {
          const verification = verificationLabel(b)
          return (
            <div key={b.file} style={{
              display: 'grid', gridTemplateColumns: '1fr auto', gap: '10px', alignItems: 'center',
              padding: '8px', background: 'var(--bg-page)', borderRadius: '6px', marginBottom: '6px',
            }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: '13px', wordBreak: 'break-all' }}>{b.file}</div>
                <div style={{ color: 'var(--text-muted)', fontSize: '12px' }}>
                  {b.created_at} · {b.size_human || `${(b.size / 1024 / 1024).toFixed(2)} MB`} · {b.kind || 'manual'} · <span style={{ color: verification.color }}>{verification.text}</span>
                </div>
                <div style={{ color: 'var(--text-muted)', fontSize: '11px', marginTop: '2px', wordBreak: 'break-all' }}>
                  恢复确认：<code>{b.restore_confirm_text || `RESTORE ${b.file}`}</code>
                </div>
              </div>
              <div style={{ display: 'flex', gap: '6px', flexShrink: 0, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                <button className="btn" onClick={() => onVerify(b.file)}
                  style={{ padding: '2px 10px', fontSize: '12px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
                  校验
                </button>
                <a className="btn" href={backupDownloadUrl(b.file)}
                  style={{ padding: '2px 10px', fontSize: '12px', background: 'var(--border-strong)', color: 'var(--text-primary)', textDecoration: 'none' }}>
                  下载
                </a>
                <button className="btn" onClick={() => onRestore(b.file)}
                  style={{ padding: '2px 10px', fontSize: '12px', background: 'var(--warning-surface)', color: 'var(--warning)' }}>
                  恢复
                </button>
                <button className="btn" onClick={() => onDelete(b.file)}
                  style={{ padding: '2px 10px', fontSize: '12px', background: 'var(--danger-surface)', color: 'var(--danger)' }}>
                  删除
                </button>
              </div>
            </div>
          )
        })}
        {backups.length === 0 && <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>暂无备份</div>}
      </div>
    </div>
  )
}
