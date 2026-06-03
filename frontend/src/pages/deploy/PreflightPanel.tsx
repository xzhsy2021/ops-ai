import { PrecheckResultBoard } from './PrecheckResultBoard'
import type { PrecheckItem } from './PrecheckResultBoard'

interface PreflightPanelProps {
  result: any
}

function mapChecksToItems(result: any): PrecheckItem[] {
  if (!result?.checks) return []
  return result.checks.map((c: any) => {
    let level: PrecheckItem['level'] = 'pass'
    if (c.status === 'error' || c.status === 'fail') level = 'blocked'
    else if (c.status === 'warn' || c.status === 'warning') level = 'warning'
    else if (c.status === 'info' || c.status === 'suggestion') level = 'suggestion'
    return { level, message: c.name, detail: c.detail }
  })
}

export default function PreflightPanel({ result }: PreflightPanelProps) {
  if (!result) return null

  return (
    <div style={{ marginTop: 12, background: 'var(--bg-page)', borderRadius: 8, padding: 12, display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', gap: 16, fontSize: 13, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ color: result.ready ? 'var(--success)' : 'var(--danger)', fontWeight: 'bold' }}>
          {result.ready ? '✅ 可以发布' : '❌ 存在问题'}
        </span>
        <span style={{ color: 'var(--text-muted)' }}>通过: {result.passed}</span>
        <span style={{ color: 'var(--warning)' }}>警告: {result.warnings}</span>
        <span style={{ color: 'var(--danger)' }}>错误: {result.errors}</span>
      </div>

      {result.topology && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 8, fontSize: 12, color: 'var(--text-secondary)' }}>
          <div>目录：<span style={{ color: 'var(--text-primary)', fontFamily: 'monospace' }}>{result.topology.service_dir || result.topology.deploy_path || '-'}</span></div>
          <div>脚本：<span style={{ color: 'var(--text-primary)', fontFamily: 'monospace' }}>{result.topology.update_script || '-'}</span></div>
          <div>日志：<span style={{ color: 'var(--text-primary)', fontFamily: 'monospace' }}>{result.topology.log_path || '-'}</span></div>
          <div>进程：<span style={{ color: 'var(--text-primary)', fontFamily: 'monospace' }}>{result.topology.process_keyword || '-'}</span></div>
          <div style={{ gridColumn: '2 / -1' }}>服务器：<span style={{ color: 'var(--text-primary)', fontFamily: 'monospace' }}>{result.servers?.filter(Boolean).join(', ') || '-'}</span></div>
        </div>
      )}

      {result.recommendations?.length > 0 && (
        <div style={{ background: 'var(--bg-surface)', borderRadius: 8, padding: 8, fontSize: 12, color: 'var(--text-secondary)', display: 'grid', gap: 4 }}>
          {result.recommendations.map((item: string, i: number) => <div key={i}>{item}</div>)}
        </div>
      )}

      {result.remote_disks?.length > 0 && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', fontSize: 12 }}>
          {result.remote_disks.map((d: any, i: number) => (
            <span key={i} style={{ background: 'var(--bg-surface)', borderRadius: '999px', padding: '3px 8px', color: 'var(--text-secondary)' }}>
              {d.server}: 可用 {d.avail} / 使用 {d.pct}
            </span>
          ))}
        </div>
      )}

      {result.checks && <PrecheckResultBoard items={mapChecksToItems(result)} />}
    </div>
  )
}