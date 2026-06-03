interface ReleaseConfirmationPanelProps {
  confirmation: any
}

function toneColor(level: string | undefined) {
  if (level === 'high') return 'var(--danger)'
  if (level === 'medium') return 'var(--warning)'
  return 'var(--success)'
}

function Pill({ children }: { children: any }) {
  return <span style={{ border: '1px solid var(--border)', background: 'var(--bg-surface)', borderRadius: '999px', padding: '3px 8px', color: 'var(--text-secondary)', fontSize: '12px' }}>{children}</span>
}

export default function ReleaseConfirmationPanel({ confirmation }: ReleaseConfirmationPanelProps) {
  if (!confirmation) return null
  const impact = confirmation.impact || {}
  const riskLevel = confirmation.risk_level || 'low'
  return (
    <div style={{ marginTop: '12px', background: confirmation.ready ? 'var(--success-surface)' : 'var(--danger-surface)', border: `1px solid ${confirmation.ready ? 'var(--success-border)' : 'var(--danger-border)'}`, borderRadius: '12px', padding: '14px', display: 'grid', gap: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
        <strong style={{ color: confirmation.ready ? 'var(--success)' : 'var(--danger)' }}>
          {confirmation.ready ? '发布确认已通过' : '发布确认未通过'} · 风险级别 <span style={{ color: toneColor(riskLevel) }}>{riskLevel}</span>
        </strong>
        <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>确认信息来自后端最终解析，和实际执行一致</span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: '8px', fontSize: '13px', color: 'var(--text-secondary)' }}>
        <div>系统：<b style={{ color: 'var(--text-primary)' }}>{confirmation.summary?.system || '-'}</b></div>
        <div>服务：<b style={{ color: 'var(--text-primary)' }}>{confirmation.summary?.service || '-'}</b></div>
        <div>环境：<b style={{ color: 'var(--text-primary)' }}>{confirmation.summary?.environment || '-'}</b></div>
        <div>包：<b style={{ color: 'var(--text-primary)' }}>{confirmation.summary?.file_name || '-'}</b></div>
        <div>服务器：<b style={{ color: 'var(--text-primary)' }}>{impact.server_count ?? confirmation.summary?.servers?.length ?? 0} 台</b></div>
        <div>步骤：<b style={{ color: 'var(--text-primary)' }}>{impact.step_count ?? confirmation.steps?.length ?? 0} 个</b></div>
        <div>写操作步骤：<b style={{ color: 'var(--text-primary)' }}>{impact.write_step_count ?? '-'}</b></div>
        <div>回滚：<b style={{ color: impact.rollback_safe ? 'var(--success)' : 'var(--warning)' }}>{impact.rollback_safe ? '可自动回滚' : '需手工准备'}</b></div>
        <div style={{ gridColumn: '1 / -1' }}>服务器：<span style={{ fontFamily: 'monospace', color: 'var(--text-primary)' }}>{confirmation.summary?.servers?.join(', ') || '-'}</span></div>
        {impact.target_paths?.length > 0 && <div style={{ gridColumn: '1 / -1' }}>影响路径：<span style={{ fontFamily: 'monospace', color: 'var(--text-primary)' }}>{impact.target_paths.join(' · ')}</span></div>}
        <div style={{ gridColumn: '1 / -1' }}>回滚方案：<span style={{ color: confirmation.rollback_plan?.safe ? 'var(--success)' : 'var(--warning)' }}>{confirmation.rollback_plan?.description || '-'}</span></div>
      </div>

      {confirmation.risk_reasons?.length > 0 && (
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          {confirmation.risk_reasons.map((x: string, i: number) => <Pill key={i}>风险原因：{x}</Pill>)}
        </div>
      )}

      {confirmation.execution_plan?.length > 0 && (
        <div style={{ background: 'var(--bg-surface)', borderRadius: '10px', padding: '10px', display: 'grid', gap: '7px' }}>
          <strong style={{ color: 'var(--text-primary)', fontSize: '13px' }}>执行计划</strong>
          {confirmation.execution_plan.map((step: any, i: number) => (
            <div key={`${step.name}-${i}`} style={{ display: 'grid', gridTemplateColumns: '24px 140px minmax(0, 1fr)', gap: '8px', alignItems: 'start', fontSize: '12px', color: 'var(--text-secondary)' }}>
              <span style={{ color: 'var(--cyan)', fontWeight: 800 }}>{i + 1}</span>
              <span style={{ color: 'var(--text-primary)' }}>{step.name || step.type}</span>
              <span style={{ fontFamily: String(step.description || '').includes('/') ? 'monospace' : undefined }}>{step.description || '-'}</span>
            </div>
          ))}
        </div>
      )}

      {confirmation.operator_checklist?.length > 0 && (
        <div style={{ background: 'var(--bg-surface)', borderRadius: '10px', padding: '10px', display: 'grid', gap: '6px' }}>
          <strong style={{ color: 'var(--text-primary)', fontSize: '13px' }}>发布前人工确认</strong>
          {confirmation.operator_checklist.map((item: any, i: number) => (
            <div key={i} style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>□ {item.label}</div>
          ))}
        </div>
      )}

      {confirmation.blockers?.length > 0 && (
        <div style={{ color: 'var(--danger)', fontSize: '13px' }}>
          {confirmation.blockers.map((x: string, i: number) => <div key={i}>阻断：{x}</div>)}
        </div>
      )}
      {confirmation.warnings?.length > 0 && (
        <div style={{ color: 'var(--warning)', fontSize: '13px' }}>
          {confirmation.warnings.map((x: string, i: number) => <div key={i}>警告：{x}</div>)}
        </div>
      )}
    </div>
  )
}
