import { useEffect, useState, type CSSProperties } from 'react'
import { deployment } from '../../api'
import { StatusBadge } from '../../components/ui'

interface ReleasePlanPanelProps {
  system: string
  service: string
  environment: string
}

function riskStyle(risk: string): CSSProperties {
  const normalized = String(risk || '').toLowerCase()
  if (normalized === 'critical') return { background: 'var(--danger-surface)', color: 'var(--danger)' }
  if (normalized === 'high') return { background: 'var(--warning-surface)', color: 'var(--warning)' }
  if (normalized === 'medium') return { background: 'var(--action-soft)', color: 'var(--text-primary)' }
  return { background: 'var(--bg-page)', color: 'var(--text-muted)' }
}

function gateLabel(status: string) {
  if (status === 'passed') return '通过'
  if (status === 'warning') return '警告'
  if (status === 'blocked') return '阻断'
  return status || '-'
}

export default function ReleasePlanPanel({ system, service, environment }: ReleasePlanPanelProps) {
  const [plans, setPlans] = useState<any[]>([])
  const [runbook, setRunbook] = useState<any | null>(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')

  const loadPlans = async () => {
    setLoading(true)
    setMessage('')
    try {
      const res: any = await deployment.toolPlans({
        system: system || undefined,
        service: service || undefined,
        environment: environment || undefined,
        limit: 8,
      })
      const data = res.data || res
      const items = data.items || data.data?.items || []
      setPlans(items)
      if (!runbook && items[0]?.id) {
        void loadRunbook(items[0].id)
      }
    } catch (err: any) {
      setMessage(err?.response?.data?.detail || err?.message || '加载发布计划失败')
    } finally {
      setLoading(false)
    }
  }

  const loadRunbook = async (planId: string) => {
    setMessage('')
    try {
      const res: any = await deployment.toolPlanRunbook(planId, true)
      setRunbook(res.data || res)
    } catch (err: any) {
      setMessage(err?.response?.data?.detail || err?.message || '加载运行手册失败')
    }
  }

  useEffect(() => {
    void loadPlans()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [system, service, environment])

  const gates = runbook?.quality_gates || []
  const mcpFlow = runbook?.mcp_flow || []
  const sections = runbook?.runbook_sections || []

  return (
    <div className="card" style={{ display: 'grid', gap: '14px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
        <div>
          <h3 style={{ margin: 0 }}>发布计划与 MCP 编排</h3>
          <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginTop: '4px' }}>
            展示工具生成的发布/回滚计划、质量门禁、MCP 执行链和任务中心闭环。
          </div>
        </div>
        <button className="btn btn-subtle" onClick={loadPlans} disabled={loading}>{loading ? '刷新中...' : '刷新计划'}</button>
      </div>

      {message && <div style={{ color: 'var(--danger)', fontSize: '13px' }}>{message}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, .85fr) minmax(360px, 1.15fr)', gap: '12px' }}>
        <div style={{ border: '1px solid var(--border-strong)', borderRadius: '12px', padding: '10px', background: 'var(--bg-page)', display: 'grid', gap: '8px', alignContent: 'start' }}>
          <strong style={{ fontSize: '13px' }}>最近工具计划</strong>
          {plans.length === 0 && <div style={{ color: 'var(--text-muted)', fontSize: '12px' }}>暂无工具计划。可通过 MCP 工具 ops.create_deploy_plan 创建，或在发布页完成预检后再查看。</div>}
          {plans.map((item) => (
            <button
              key={item.id}
              className="btn"
              onClick={() => loadRunbook(item.id)}
              style={{
                textAlign: 'left',
                justifyContent: 'flex-start',
                background: runbook?.plan?.id === item.id ? 'var(--action-soft)' : 'var(--bg-surface)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border-strong)',
              }}
            >
              <div style={{ display: 'grid', gap: '4px', width: '100%' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', alignItems: 'center' }}>
                  <span style={{ fontWeight: 700 }}>{item.plan_type} · {item.system}/{item.service || '-'}</span>
                  <StatusBadge value={item.status} />
                </div>
                <div style={{ color: 'var(--text-muted)', fontSize: '11px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {item.environment || '-'} · {item.package_name || '-'} · {item.id}
                </div>
              </div>
            </button>
          ))}
        </div>

        <div style={{ display: 'grid', gap: '12px' }}>
          {!runbook && <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>选择左侧计划查看运行手册。</div>}
          {runbook && (
            <>
              <div style={{ border: '1px solid var(--border-strong)', borderRadius: '12px', padding: '10px', background: 'var(--bg-page)', display: 'grid', gap: '8px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap' }}>
                  <strong>{runbook.summary?.title || '发布计划'}</strong>
                  <span style={{ ...riskStyle(runbook.summary?.risk_level), borderRadius: '999px', padding: '2px 8px', fontSize: '12px', fontWeight: 700 }}>
                    {runbook.summary?.risk_level || '-'} · {runbook.summary?.execution_is_taskized ? '任务化执行' : '同步/只读'}
                  </span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '8px', fontSize: '12px' }}>
                  <div>服务器：<strong>{runbook.summary?.server_count ?? 0}</strong></div>
                  <div>发布包：<strong>{runbook.summary?.package_name || '-'}</strong></div>
                  <div>确认短语：<code>{runbook.summary?.confirm_text || '-'}</code></div>
                  <div>门禁：<strong>{runbook.gate_summary?.status || '-'}</strong></div>
                </div>
              </div>

              <div style={{ border: '1px solid var(--border-strong)', borderRadius: '12px', padding: '10px', background: 'var(--bg-page)' }}>
                <strong style={{ fontSize: '13px' }}>质量门禁</strong>
                <div style={{ display: 'grid', gap: '6px', marginTop: '8px' }}>
                  {gates.map((gate: any) => (
                    <div key={gate.key} style={{ display: 'grid', gridTemplateColumns: '88px minmax(0, 1fr) minmax(0, 1.6fr)', gap: '8px', alignItems: 'center', fontSize: '12px' }}>
                      <span style={{ ...riskStyle(gate.status === 'blocked' ? 'critical' : gate.status === 'warning' ? 'high' : 'low'), borderRadius: '999px', padding: '2px 8px', textAlign: 'center', fontWeight: 700 }}>{gateLabel(gate.status)}</span>
                      <span>{gate.name}</span>
                      <span style={{ color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{gate.detail || '-'}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div style={{ border: '1px solid var(--border-strong)', borderRadius: '12px', padding: '10px', background: 'var(--bg-page)' }}>
                <strong style={{ fontSize: '13px' }}>MCP 执行链</strong>
                <div style={{ display: 'grid', gap: '6px', marginTop: '8px' }}>
                  {mcpFlow.map((step: any) => (
                    <div key={`${step.step}-${step.tool}`} style={{ display: 'grid', gridTemplateColumns: '36px minmax(0, 1fr) 82px 82px', gap: '8px', fontSize: '12px', alignItems: 'center' }}>
                      <span style={{ color: 'var(--text-muted)' }}>#{step.step}</span>
                      <code>{step.tool}</code>
                      <span>{step.mode}</span>
                      <span style={{ color: step.taskized ? 'var(--warning)' : 'var(--text-muted)' }}>{step.taskized ? '任务化' : '只读/计划'}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div style={{ border: '1px solid var(--border-strong)', borderRadius: '12px', padding: '10px', background: 'var(--bg-page)' }}>
                <strong style={{ fontSize: '13px' }}>运行手册</strong>
                <div style={{ display: 'grid', gap: '8px', marginTop: '8px' }}>
                  {sections.map((section: any) => (
                    <div key={section.key}>
                      <div style={{ fontWeight: 700, fontSize: '12px' }}>{section.title}</div>
                      <ul style={{ margin: '4px 0 0 18px', padding: 0, color: 'var(--text-muted)', fontSize: '12px' }}>
                        {(section.items || []).map((item: string, idx: number) => <li key={idx}>{item}</li>)}
                      </ul>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
