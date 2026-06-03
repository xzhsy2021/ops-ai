import PreflightPanel from './PreflightPanel'

interface DeployPrecheckStepProps {
  precheckResult: any
  resolveResult: any
  prechecking: boolean
  resolving: boolean
  handlePrecheck: () => void
  handleResolve: () => void
}

export default function DeployPrecheckStep({
  precheckResult,
  resolveResult,
  prechecking,
  resolving,
  handlePrecheck,
  handleResolve,
}: DeployPrecheckStepProps) {
  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div style={{ display: 'flex', gap: 12 }}>
        <button className="btn" onClick={handlePrecheck} disabled={prechecking}
          style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
          {prechecking ? '预检中...' : '执行预检'}
        </button>
        <button className="btn" onClick={handleResolve} disabled={resolving}
          style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>
          {resolving ? '解析中...' : '解析预览'}
        </button>
      </div>

      {resolveResult && (
        <div style={{ background: 'var(--bg-page)', borderRadius: 8, padding: 12 }}>
          <div style={{ display: 'flex', gap: 16, marginBottom: 8, fontSize: 13 }}>
            <span style={{ color: resolveResult.ready ? 'var(--success)' : 'var(--danger)', fontWeight: 'bold' }}>
              {resolveResult.ready ? '✅ 所有变量已解析' : '❌ 存在未解析变量'}
            </span>
            <span style={{ color: 'var(--text-muted)' }}>{resolveResult.trace?.length || 0} 条追踪记录</span>
          </div>
          {resolveResult.errors?.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              {resolveResult.errors.map((e: any, ei: number) => (
                <div key={ei} style={{ color: 'var(--danger)', fontSize: 12, marginBottom: 2 }}>{e.message}</div>
              ))}
            </div>
          )}
          {resolveResult.trace?.length > 0 && (
            <div style={{ maxHeight: 200, overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--bg-surface)', textAlign: 'left' }}>
                    <th style={{ padding: 4, color: 'var(--text-muted)' }}>步骤</th>
                    <th style={{ padding: 4, color: 'var(--text-muted)' }}>字段</th>
                    <th style={{ padding: 4, color: 'var(--text-muted)' }}>值</th>
                    <th style={{ padding: 4, color: 'var(--text-muted)' }}>来源</th>
                  </tr>
                </thead>
                <tbody>
                  {resolveResult.trace.map((t: any, ti: number) => (
                    <tr key={ti} style={{ borderBottom: '1px solid var(--bg-page)' }}>
                      <td style={{ padding: 4, color: 'var(--text-secondary)' }}>{t.step_name}</td>
                      <td style={{ padding: 4, color: 'var(--brand-soft)', fontFamily: 'monospace' }}>{t.field}</td>
                      <td style={{ padding: 4, color: 'var(--text-primary)', fontFamily: 'monospace', fontSize: 11 }}>{String(t.value).substring(0, 30)}</td>
                      <td style={{ padding: 4 }}>
                        <span style={{ padding: '1px 6px', borderRadius: 4, fontSize: 10, background: t.source_type === 'literal' ? 'var(--bg-surface)' : 'var(--purple-surface)', color: t.source_type === 'literal' ? 'var(--text-muted)' : 'var(--purple-text)' }}>{t.source_type}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <PreflightPanel result={precheckResult} />
    </div>
  )
}