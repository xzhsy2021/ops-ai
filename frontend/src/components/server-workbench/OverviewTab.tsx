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

type DeploymentMode = 'docker_compose' | 'pm2' | 'process_keyword' | 'unknown' | ''

const MODE_LABELS: Record<string, string> = {
  docker_compose: 'Docker Compose 服务',
  pm2: 'PM2 进程',
  process_keyword: '进程',
  unknown: 'PM2 进程',
  '': 'PM2 进程',
}

export default function OverviewTab({ name }: OverviewTabProps) {
  const [info, setInfo] = useState<any>(null)
  const [processes, setProcesses] = useState<any[]>([])
  const [mode, setMode] = useState<DeploymentMode>('')
  const [services, setServices] = useState<any[]>([])
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
      setMode((procRes.data?.deployment_mode as DeploymentMode) || 'unknown')
      setServices(procRes.data?.services || [])
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
      await serverWorkbench.processAction(name, process, action, mode || undefined)
      notify(`已${action === 'start' ? '启动' : action === 'stop' ? '停止' : '重启'}: ${process || '全部服务'}`, true)
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
  const modeLabel = MODE_LABELS[mode] || '服务进程'
  const isDocker = mode === 'docker_compose'
  const isPM2 = mode === 'pm2' || mode === 'unknown' || mode === ''
  const isProcessKeyword = mode === 'process_keyword'

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
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
          <h3 style={{ margin: '0' }}>
            {modeLabel} ({processes.length})
            {services.length > 0 && (
              <span style={{ color: 'var(--text-muted)', fontSize: '12px', marginLeft: '8px', fontWeight: 'normal' }}>
                · {services.map(s => s.display_name || s.name).join(' / ')}
              </span>
            )}
          </h3>
          {isDocker && (
            <span style={{ fontSize: '11px', color: 'var(--action-text)', background: 'var(--action-bg)', padding: '2px 8px', borderRadius: '4px' }}>
              Docker Compose
            </span>
          )}
          {isPM2 && services.length === 0 && (
            <span style={{ fontSize: '11px', color: 'var(--text-muted)', background: 'var(--bg-surface)', padding: '2px 8px', borderRadius: '4px' }}>
              PM2
            </span>
          )}
          {isProcessKeyword && (
            <span style={{ fontSize: '11px', color: 'var(--text-muted)', background: 'var(--bg-surface)', padding: '2px 8px', borderRadius: '4px' }}>
              process_keyword
            </span>
          )}
        </div>

        {isDocker ? (
          <DockerProcessTable
            processes={processes}
            actionBusy={actionBusy}
            onAction={requestAction}
          />
        ) : isProcessKeyword ? (
          <KeywordProcessTable
            processes={processes}
            actionBusy={actionBusy}
            onAction={requestAction}
          />
        ) : (
          <PM2ProcessTable
            processes={processes}
            actionBusy={actionBusy}
            onAction={requestAction}
          />
        )}
      </div>

      <button className="btn" onClick={load} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)', justifySelf: 'start' }}>刷新</button>

      <RiskConfirmDialog
        open={Boolean(pendingAction)}
        title={pendingAction?.action === 'stop' ? `确认停止 ${modeLabel}` : `确认重启 ${modeLabel}`}
        description={pendingAction?.action === 'stop'
          ? '服务停止后将不可用，直到再次启动。'
          : '重启会短暂中断服务。'}
        target={`${name} / ${pendingAction?.process || ''}`}
        confirmText={`${pendingAction?.action?.toUpperCase() || ''} ${pendingAction?.process || ''}`}
        value={confirmValue}
        onValueChange={setConfirmValue}
        riskLevel={pendingAction?.action === 'stop' ? 'high' : 'medium'}
        details={[
          { label: '服务器', value: name },
          { label: '目标', value: pendingAction?.process || '-' },
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

function PM2ProcessTable({ processes, actionBusy, onAction }: {
  processes: any[]
  actionBusy: string
  onAction: (process: string, action: 'start' | 'stop' | 'restart') => void
}) {
  if (processes.length === 0) {
    return <div style={{ color: 'var(--text-muted)' }}>暂无 PM2 进程</div>
  }
  return (
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
                <StatusBadge status={p.status} />
              </td>
              <td style={{ padding: '6px', color: 'var(--text-secondary)' }}>{p.cpu}%</td>
              <td style={{ padding: '6px', color: 'var(--text-secondary)' }}>{(p.memory / 1024 / 1024).toFixed(1)} MB</td>
              <td style={{ padding: '6px', color: 'var(--text-muted)' }}>{p.restarts}</td>
              <td style={{ padding: '6px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                <ActionButtons
                  target={target}
                  online={online}
                  busy={actionBusy}
                  onAction={onAction}
                />
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function DockerProcessTable({ processes, actionBusy, onAction }: {
  processes: any[]
  actionBusy: string
  onAction: (process: string, action: 'start' | 'stop' | 'restart') => void
}) {
  if (processes.length === 0) {
    return <div style={{ color: 'var(--text-muted)' }}>暂无运行中的 Docker Compose 服务</div>
  }
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
      <thead>
        <tr style={{ borderBottom: '1px solid var(--border-strong)', textAlign: 'left' }}>
          <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>服务</th>
          <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>镜像</th>
          <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>状态</th>
          <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>运行时长</th>
          <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>端口</th>
          <th style={{ padding: '6px', color: 'var(--text-secondary)', textAlign: 'right' }}>动作</th>
        </tr>
      </thead>
      <tbody>
        {processes.map((p: any, idx: number) => {
          const running = (p.status || '').includes('running') || (p.status || '').includes('up')
          const target = p.name || ''
          return (
            <tr key={target + idx} style={{ borderBottom: '1px solid var(--bg-surface)' }}>
              <td style={{ padding: '6px', color: 'var(--brand)' }}>{p.name}</td>
              <td style={{ padding: '6px', color: 'var(--text-secondary)', maxWidth: '260px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={p.image}>{p.image}</td>
              <td style={{ padding: '6px' }}>
                <StatusBadge status={p.status} />
              </td>
              <td style={{ padding: '6px', color: 'var(--text-muted)' }}>{p.uptime}</td>
              <td style={{ padding: '6px', color: 'var(--text-secondary)', fontFamily: 'monospace', fontSize: '11px' }}>{typeof p.ports === 'string' ? p.ports : ''}</td>
              <td style={{ padding: '6px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                <ActionButtons
                  target={target}
                  online={running}
                  busy={actionBusy}
                  onAction={onAction}
                />
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function KeywordProcessTable({ processes, actionBusy, onAction }: {
  processes: any[]
  actionBusy: string
  onAction: (process: string, action: 'start' | 'stop' | 'restart') => void
}) {
  if (processes.length === 0) {
    return <div style={{ color: 'var(--text-muted)' }}>暂无匹配进程</div>
  }
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
      <thead>
        <tr style={{ borderBottom: '1px solid var(--border-strong)', textAlign: 'left' }}>
          <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>名称</th>
          <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>PID</th>
          <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>状态</th>
          <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>CPU</th>
          <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>内存</th>
          <th style={{ padding: '6px', color: 'var(--text-secondary)' }}>运行时长</th>
        </tr>
      </thead>
      <tbody>
        {processes.map((p: any, idx: number) => (
          <tr key={p.name + p.pid + idx} style={{ borderBottom: '1px solid var(--bg-surface)' }}>
            <td style={{ padding: '6px', color: 'var(--brand)' }}>{p.name}</td>
            <td style={{ padding: '6px', color: 'var(--text-secondary)' }}>{p.pid}</td>
            <td style={{ padding: '6px' }}>
              <StatusBadge status={p.status} />
            </td>
            <td style={{ padding: '6px', color: 'var(--text-secondary)' }}>{p.cpu}%</td>
            <td style={{ padding: '6px', color: 'var(--text-secondary)' }}>{p.memory}%</td>
            <td style={{ padding: '6px', color: 'var(--text-muted)' }}>{p.uptime}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function StatusBadge({ status }: { status: string }) {
  const s = (status || '').toLowerCase()
  const online = s.includes('online') || s.includes('running') || s.includes('up')
  return (
    <span style={{
      color: online ? 'var(--success)' : 'var(--danger)',
      background: online ? 'var(--success-surface)' : 'var(--danger-surface)',
      padding: '1px 6px', borderRadius: '4px', fontSize: '12px',
    }}>{status}</span>
  )
}

function ActionButtons({ target, online, busy, onAction }: {
  target: string
  online: boolean
  busy: string
  onAction: (process: string, action: 'start' | 'stop' | 'restart') => void
}) {
  return (
    <>
      <button className="btn" onClick={() => onAction(target, 'start')}
        disabled={online || busy === `${target}:start` || !target}
        title={online ? '服务已运行' : '启动'}
        style={{ padding: '2px 8px', fontSize: '12px', marginRight: '4px', background: 'var(--success-border)', color: 'var(--success)', opacity: online ? 0.5 : 1 }}>
        {busy === `${target}:start` ? '...' : '启动'}
      </button>
      <button className="btn" onClick={() => onAction(target, 'restart')}
        disabled={busy === `${target}:restart` || !target}
        title="重启"
        style={{ padding: '2px 8px', fontSize: '12px', marginRight: '4px', background: 'var(--action-bg)', color: 'var(--action-text)' }}>
        {busy === `${target}:restart` ? '...' : '重启'}
      </button>
      <button className="btn" onClick={() => onAction(target, 'stop')}
        disabled={!online || busy === `${target}:stop` || !target}
        title={online ? '停止' : '服务已停止'}
        style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--danger-surface)', color: 'var(--danger)', opacity: online ? 1 : 0.5 }}>
        {busy === `${target}:stop` ? '...' : '停止'}
      </button>
    </>
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
