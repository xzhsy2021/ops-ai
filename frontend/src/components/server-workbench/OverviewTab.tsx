import { useState, useEffect } from 'react'
import { serverWorkbench } from '../../api'
import { RiskConfirmDialog } from '../ui'

interface OverviewTabProps {
  name: string
}

type PendingAction = {
  process: string
  action: 'start' | 'stop' | 'restart'
}

export default function OverviewTab({ name }: OverviewTabProps) {
  const [info, setInfo] = useState<any>(null)
  const [processes, setProcesses] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [actionBusy, setActionBusy] = useState<string>('')
  const [flash, setFlash] = useState<{ text: string; ok: boolean } | null>(null)
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null)
  const [confirmValue, setConfirmValue] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      const [infoRes, procRes]: any[] = await Promise.all([
        serverWorkbench.info(name),
        serverWorkbench.processes(name),
      ])
      setInfo(infoRes.data)
      setProcesses(procRes.data?.processes || [])
    } catch (e) {
      console.error(e)
    }
    setLoading(false)
  }

  useEffect(() => { load() }, [name])

  const notify = (text: string, ok: boolean) => {
    setFlash({ text, ok })
    setTimeout(() => setFlash(null), 3500)
  }

  const runAction = async (process: string, action: 'start' | 'stop' | 'restart') => {
    const key = `${process}:${action}`
    setActionBusy(key)
    try {
      await serverWorkbench.processAction(name, process, action)
      notify(`已${action === 'start' ? '启动' : action === 'stop' ? '停止' : '重启'}: ${process}`, true)
      await load()
    } catch (e: any) {
      const msg = typeof e === 'string' ? e : e?.response?.data?.detail || e?.message || `${action} 失败`
      notify(msg, false)
    }
    setActionBusy('')
  }

  const requestAction = (process: string, action: 'start' | 'stop' | 'restart') => {
    if (action === 'start') {
      void runAction(process, action)
      return
    }
    setConfirmValue('')
    setPendingAction({ process, action })
  }

  const confirmAction = async () => {
    if (!pendingAction) return
    const action = pendingAction
    setPendingAction(null)
    setConfirmValue('')
    await runAction(action.process, action.action)
  }

  const closeConfirm = () => {
    setPendingAction(null)
    setConfirmValue('')
  }

  if (loading) return <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>连接服务器...</div>

  const hc = info?.hop_context

  return (
    <div style={{ display: 'grid', gap: '20px' }}>
      {hc?.is_proxied && (
        <div className="card" style={{ background: 'var(--brand-surface)', borderColor: 'var(--brand-border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px' }}>
            <span style={{ color: 'var(--action-text)' }}>跳板机链 ({hc.hop_count} 跳):</span>
            {hc.hops.map((h: any, i: number) => (
              <span key={i} style={{ color: 'var(--text-primary)' }}>
                {h.name}{i < hc.hops.length - 1 ? ' →' : ''}
              </span>
            ))}
            <span style={{ color: 'var(--text-muted)', marginLeft: 'auto' }}>
              → {info?.host}
            </span>
          </div>
        </div>
      )}

      {info && (
        <div className="card">
          <h3 style={{ margin: '0 0 12px 0' }}>系统资源</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px' }}>
            <StatBlock label="磁盘" lines={info.disk} />
            <StatBlock label="内存" lines={info.mem} />
            <StatBlock label="负载" lines={info.load} />
          </div>
          {info.duration_ms != null && (
            <div style={{ color: 'var(--text-muted)', fontSize: '11px', marginTop: '12px' }}>
              响应时间: {info.duration_ms}ms
            </div>
          )}
        </div>
      )}

      {flash && (
        <div className="card" style={{
          background: flash.ok ? 'var(--success-surface)' : 'var(--danger-surface)',
          borderColor: flash.ok ? 'var(--success-border)' : 'var(--danger-border)',
          color: flash.ok ? 'var(--success)' : 'var(--danger)',
          fontSize: '13px',
        }}>
          {flash.text}
        </div>
      )}

      <div className="card">
        <h3 style={{ margin: '0 0 12px 0' }}>PM2 进程 ({processes.length})</h3>
        {processes.length === 0 ? (
          <div style={{ color: 'var(--text-muted)' }}>暂无 PM2 进程</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-strong)', textAlign: 'left' }}>
                <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>名称</th>
                <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>PID</th>
                <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>状态</th>
                <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>CPU</th>
                <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>内存</th>
                <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>重启</th>
                <th style={{ padding: '6px', color: 'var(--text-secondary)', textAlign: 'right' }}>动作</th>
              </tr>
            </thead>
            <tbody>
              {processes.map((p: any) => {
                const online = p.status === 'online'
                const target = p.name || String(p.pid || '')
                return (
                <tr key={target + p.pid} style={{ borderBottom: '1px solid var(--bg-surface)' }}>
                  <td style={{ padding: '6px', color: 'var(--brand)' }}>{p.name}</td>
                  <td style={{ padding: '6px', color: 'var(--text-secondary)' }}>{p.pid}</td>
                  <td style={{ padding: '6px' }}>
                    <span style={{
                      color: online ? 'var(--success)' : 'var(--danger)',
                      background: online ? 'var(--success-surface)' : 'var(--danger-surface)',
                      padding: '1px 6px', borderRadius: '4px', fontSize: '12px',
                    }}>{p.status}</span>
                  </td>
                  <td style={{ padding: '6px', color: 'var(--text-secondary)' }}>{p.cpu}%</td>
                  <td style={{ padding: '6px', color: 'var(--text-secondary)' }}>{(p.memory / 1024 / 1024).toFixed(1)} MB</td>
                  <td style={{ padding: '6px', color: 'var(--text-muted)' }}>{p.restarts}</td>
                  <td style={{ padding: '6px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <button className="btn" onClick={() => requestAction(target, 'start')}
                      disabled={online || actionBusy === `${target}:start` || !target}
                      title={online ? '进程已在线' : '启动'}
                      style={{ padding: '2px 8px', fontSize: '12px', marginRight: '4px', background: 'var(--success-border)', color: 'var(--success)', opacity: online ? 0.5 : 1 }}>
                      {actionBusy === `${target}:start` ? '...' : '启动'}
                    </button>
                    <button className="btn" onClick={() => requestAction(target, 'restart')}
                      disabled={actionBusy === `${target}:restart` || !target}
                      title="重启"
                      style={{ padding: '2px 8px', fontSize: '12px', marginRight: '4px', background: 'var(--action-bg)', color: 'var(--action-text)' }}>
                      {actionBusy === `${target}:restart` ? '...' : '重启'}
                    </button>
                    <button className="btn" onClick={() => requestAction(target, 'stop')}
                      disabled={!online || actionBusy === `${target}:stop` || !target}
                      title={online ? '停止' : '进程已停止'}
                      style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--danger-surface)', color: 'var(--danger)', opacity: online ? 1 : 0.5 }}>
                      {actionBusy === `${target}:stop` ? '...' : '停止'}
                    </button>
                  </td>
                </tr>
              )})}
            </tbody>
          </table>
        )}
      </div>

      <button className="btn" onClick={load} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)', justifySelf: 'start' }}>刷新</button>

      <RiskConfirmDialog
        open={Boolean(pendingAction)}
        title={pendingAction?.action === 'stop' ? '确认停止 PM2 进程' : '确认重启 PM2 进程'}
        description={pendingAction?.action === 'stop' ? '进程停止后服务将不可用，直到再次启动。' : '重启会短暂中断服务，PM2 会自动接管。'}
        target={`${name} / ${pendingAction?.process || ''}`}
        confirmText={`${pendingAction?.action?.toUpperCase() || ''} ${pendingAction?.process || ''}`}
        value={confirmValue}
        onValueChange={setConfirmValue}
        riskLevel={pendingAction?.action === 'stop' ? 'high' : 'medium'}
        details={[
          { label: '服务器', value: name },
          { label: '进程', value: pendingAction?.process || '-' },
          { label: '动作', value: pendingAction?.action === 'stop' ? '停止' : '重启' },
        ]}
        confirmButtonLabel={pendingAction?.action === 'stop' ? '确认停止' : '确认重启'}
        onCancel={closeConfirm}
        onConfirm={confirmAction}
        confirmMode="one-click"
      />
    </div>
  )
}

function StatBlock({ label, lines }: { label: string; lines?: string[] }) {
  if (!lines || lines.length === 0) return null
  return (
    <div style={{ background: 'var(--bg-page)', padding: '12px', borderRadius: '8px' }}>
      <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginBottom: '4px' }}>{label}</div>
      <div style={{ fontFamily: 'monospace', fontSize: '13px', whiteSpace: 'pre-wrap', color: 'var(--text-secondary)' }}>
        {lines.join('\n')}
      </div>
    </div>
  )
}
