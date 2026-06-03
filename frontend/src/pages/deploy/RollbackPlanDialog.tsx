interface RollbackPlanDialogProps {
  planData: any
  confirmText: string
  submitting: boolean
  onConfirmTextChange: (value: string) => void
  onClose: () => void
  onConfirm: () => void
}

function riskColor(level: string | undefined) {
  if (level === 'high') return 'var(--danger)'
  if (level === 'medium') return 'var(--warning)'
  return 'var(--success)'
}

export default function RollbackPlanDialog({
  planData,
  confirmText,
  submitting,
  onConfirmTextChange,
  onClose,
  onConfirm,
}: RollbackPlanDialogProps) {
  if (!planData) return null

  const plan = planData.plan || {}
  const precheck = planData.precheck || {}
  const candidates = planData.candidates || []
  const blockers = precheck.blockers || []
  const warnings = precheck.warnings || []
  const recommendations = precheck.recommendations || []
  const healthChecks = precheck.health_checks || []
  const requiresConfirmation = Boolean(precheck.requires_confirmation && precheck.confirm_text)
  const confirmationOk = !requiresConfirmation || confirmText === precheck.confirm_text
  const canSubmit = blockers.length === 0 && confirmationOk && !submitting

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.45)', zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}>
      <div className="card" style={{ width: 'min(760px, 96vw)', maxHeight: '88vh', overflow: 'auto', boxShadow: '0 18px 60px rgba(0,0,0,.35)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'flex-start', marginBottom: '12px' }}>
          <div>
            <h2 style={{ margin: 0 }}>回滚执行确认</h2>
            <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginTop: '4px' }}>后端实时生成的回滚计划；提交前会再次校验阻断项。</div>
          </div>
          <button className="btn" onClick={onClose} disabled={submitting} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>关闭</button>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '8px', fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
          <div>当前版本：<b style={{ color: 'var(--text-primary)' }}>{planData.current?.version || '-'}</b></div>
          <div>模式：<b style={{ color: 'var(--text-primary)' }}>{plan.mode || '-'}</b></div>
          <div>风险：<b style={{ color: riskColor(precheck.risk_level || plan.risk_level) }}>{precheck.risk_level || plan.risk_level || '-'}</b></div>
          <div style={{ gridColumn: '1 / -1' }}>说明：<span style={{ color: 'var(--text-primary)' }}>{plan.description || '-'}</span></div>
          <div style={{ gridColumn: '1 / -1' }}>服务器：<span style={{ color: 'var(--text-primary)', fontFamily: 'monospace' }}>{(precheck.servers || plan.servers || []).join(', ') || '-'}</span></div>
        </div>

        {candidates.length > 0 && (
          <div style={{ marginBottom: '12px', background: 'var(--bg-page)', borderRadius: '8px', padding: '10px' }}>
            <strong style={{ fontSize: '13px' }}>最近成功版本候选</strong>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '8px' }}>
              {candidates.slice(0, 5).map((item: any) => (
                <span key={item.id || item.version} style={{ fontSize: '12px', color: 'var(--text-secondary)', background: 'var(--bg-surface)', padding: '4px 8px', borderRadius: '999px' }}>
                  {item.version || item.id} {item.started_at ? `· ${String(item.started_at).slice(0, 19)}` : ''}
                </span>
              ))}
            </div>
          </div>
        )}

        {healthChecks.length > 0 ? (
          <div style={{ marginBottom: '12px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            回滚后健康检查：{healthChecks.map((x: any) => x.type || x.name || '-').join(', ')}
          </div>
        ) : (
          <div style={{ marginBottom: '12px', fontSize: '12px', color: 'var(--warning)' }}>回滚后健康检查：未配置</div>
        )}

        {blockers.length > 0 && (
          <div style={{ marginBottom: '12px', background: 'var(--danger-surface)', borderRadius: '8px', padding: '10px', color: 'var(--danger)', fontSize: '13px' }}>
            {blockers.map((item: string, i: number) => <div key={i}>阻断：{item}</div>)}
          </div>
        )}
        {warnings.length > 0 && (
          <div style={{ marginBottom: '12px', background: 'var(--warning-surface)', borderRadius: '8px', padding: '10px', color: 'var(--warning)', fontSize: '13px' }}>
            {warnings.map((item: string, i: number) => <div key={i}>警告：{item}</div>)}
          </div>
        )}
        {recommendations.length > 0 && (
          <div style={{ marginBottom: '12px', background: 'var(--bg-page)', borderRadius: '8px', padding: '10px', color: 'var(--text-secondary)', fontSize: '13px' }}>
            {recommendations.map((item: string, i: number) => <div key={i}>建议：{item}</div>)}
          </div>
        )}

        {requiresConfirmation && (
          <label style={{ display: 'grid', gap: '6px', marginBottom: '12px', fontSize: '13px', color: 'var(--text-secondary)' }}>
            生产/高风险回滚确认文本：请输入 <b style={{ color: 'var(--danger)', fontFamily: 'monospace' }}>{precheck.confirm_text}</b>
            <input
              value={confirmText}
              onChange={(e) => onConfirmTextChange(e.target.value)}
              placeholder={precheck.confirm_text}
              style={{ padding: '8px 10px', borderRadius: '6px', border: '1px solid var(--border-strong)', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}
            />
          </label>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
          <button className="btn" onClick={onClose} disabled={submitting} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>取消</button>
          <button className="btn" onClick={onConfirm} disabled={!canSubmit} style={{ background: canSubmit ? 'var(--warning)' : 'var(--bg-surface)', color: canSubmit ? 'white' : 'var(--text-muted)' }}>
            {submitting ? '提交中...' : blockers.length ? '存在阻断项' : '确认回滚'}
          </button>
        </div>
      </div>
    </div>
  )
}
