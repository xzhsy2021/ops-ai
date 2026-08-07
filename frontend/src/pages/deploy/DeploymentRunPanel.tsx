import { OperationTimeline, StatusBadge } from '../../components/ui'
import { LogConsole } from '../../components/LogConsole'
import { DeploymentRunHeader } from './DeploymentRunHeader'
import { DeploymentServerGrid } from './DeploymentServerGrid'
import type { DeploymentLogEntry as LogEntry, DeploymentReport } from '../../types/deploy'
import { useState, useMemo } from 'react'

function ConfigDiff({ captured, live }: { captured: Record<string, any> | null; live: Record<string, any> | null }) {
  const [open, setOpen] = useState(false)
  if (!captured || !live) return null
  const capturedKeys = Object.keys(captured)
  const liveKeys = Object.keys(live)
  const allKeys = [...new Set([...capturedKeys, ...liveKeys])].sort()
  const hasDiff = allKeys.some((k) => JSON.stringify(captured[k]) !== JSON.stringify(live[k]))
  if (!hasDiff) return null

  return (
    <div style={{ display: 'grid', gap: 4 }}>
      <button
        className="btn btn-subtle"
        onClick={() => setOpen(!open)}
        style={{ fontSize: 11, padding: '1px 6px', justifySelf: 'start' }}
      >
        {open ? '收起配置漂移' : '展开配置漂移'}
      </button>
      {open && (
        <table style={{ fontSize: 11, borderCollapse: 'collapse', width: '100%' }}>
          <thead>
            <tr style={{ color: 'var(--text-muted)' }}>
              <th style={{ textAlign: 'left', padding: '2px 6px' }}>字段</th>
              <th style={{ textAlign: 'left', padding: '2px 6px' }}>部署时值</th>
              <th style={{ textAlign: 'left', padding: '2px 6px' }}>当前值</th>
            </tr>
          </thead>
          <tbody>
            {allKeys.map((k) => {
              const cv = JSON.stringify(captured[k])
              const lv = JSON.stringify(live[k])
              const drifted = cv !== lv
              return (
                <tr key={k} style={drifted ? { background: 'var(--warning-surface)' } : undefined}>
                  <td style={{ padding: '2px 6px', fontFamily: 'monospace' }}>{k}</td>
                  <td style={{ padding: '2px 6px', fontFamily: 'monospace', color: drifted ? 'var(--danger)' : 'var(--text-secondary)' }}>{cv}</td>
                  <td style={{ padding: '2px 6px', fontFamily: 'monospace', color: drifted ? 'var(--success)' : 'var(--text-secondary)' }}>{lv}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}

interface DeploymentRunPanelProps {
  taskId: string
  deploymentId: string
  status: string
  logs: LogEntry[]
  taskDetails: any
  serverExecutionRows: Array<{ name: string; status: string; last: string }>
  timelineSteps: any[]
  onCancel: () => void
  onRefreshTask: () => void
  reportMarkdownUrl?: string
  reportTextUrl?: string
  report?: DeploymentReport | null
}

export default function DeploymentRunPanel({
  taskId,
  deploymentId,
  status,
  logs,
  taskDetails,
  serverExecutionRows,
  timelineSteps,
  onCancel,
  onRefreshTask,
  reportMarkdownUrl,
  reportTextUrl,
  report,
}: DeploymentRunPanelProps) {
  const failedServers = report?.failed_servers || []
  const reportSuggestions = report?.suggestions || []

  const isFailed = ['failed', 'partial_failed'].includes(status)
  const failureReasons = useMemo(() => {
    const reasons: Array<{ source: string; message: string }> = []
    // 从 report 提取
    if (report?.failure_analysis) {
      reasons.push({ source: '分析', message: report.failure_analysis.message || report.summary_text || '' })
    }
    if (report?.summary_text && !reasons.length) {
      reasons.push({ source: '摘要', message: report.summary_text })
    }
    // 从 serverExecutionRows 提取失败服务器
    serverExecutionRows.filter((r) => r.status === 'failed').forEach((r) => {
      reasons.push({ source: `服务器: ${r.name}`, message: r.last || '未知错误' })
    })
    // 从 taskDetails 提取失败步骤
    if (taskDetails?.step_tasks) {
      taskDetails.step_tasks
        .filter((st: any) => st.status === 'failed' || st.status === 'error')
        .forEach((st: any) => {
          const key = st.server_name ? `${st.server_name}/${st.step_name}` : st.step_name
          if (!reasons.some((r) => r.message === (st.message || ''))) {
            reasons.push({ source: key, message: st.message || '执行失败' })
          }
        })
    }
    // 从 failedServers (report) 补充
    failedServers.forEach((fs: any) => {
      const key = fs.server_name || '未知'
      const msg = fs.message || fs.status || ''
      if (msg && !reasons.some((r) => r.source.includes(key) && r.message === msg)) {
        reasons.push({ source: `服务器: ${key}`, message: msg })
      }
    })
    return reasons
  }, [report, serverExecutionRows, taskDetails, failedServers])

  const copyMarkdownReport = () => {
    const summary = report?.summary || {}
    const lines = [
      `# 发布报告 ${report?.id || deploymentId || taskId}`,
      '',
      `- 系统：${summary.system || report?.system || '-'}`,
      `- 服务：${summary.service || report?.service || '-'}`,
      `- 环境：${summary.environment || report?.environment || '-'}`,
      `- 状态：${summary.status || report?.status || status || '-'}`,
      `- 版本：${summary.version || report?.version || '-'}`,
      `- 操作人：${summary.operator || report?.created_by || '-'}`,
      `- 耗时：${summary.duration_seconds ?? report?.duration_seconds ?? '-'} 秒`,
      '',
      '## 摘要',
      report?.summary_text || '-',
      '',
      '## 失败服务器',
      ...(failedServers.length ? failedServers.map((x: any) => `- ${x.server_name || '-'}：${x.message || x.status || '-'}`) : ['- 无']),
      '',
      '## 建议',
      ...(reportSuggestions.length ? reportSuggestions.map((x: string) => `- ${x}`) : ['- 无']),
    ]
    void navigator.clipboard?.writeText(lines.join('\n'))
  }

  const statusLabel =
    status === 'running' ? '执行中'
    : status === 'success' ? '已完成'
    : status === 'failed' ? '失败'
    : status === 'partial_failed' ? '部分失败'
    : status === 'cancelled' || status === 'canceled' ? '已取消'
    : status === 'pending' ? '等待中'
    : status || '-'

  const hasSnapshotConfig =
    taskDetails?.step_tasks?.some((t: any) => t.captured_config) ||
    taskDetails?.steps?.some((s: any) => s.captured_config)

  const successServerCount = serverExecutionRows.filter((r) => r.status === 'success').length
  const failedServerCount = serverExecutionRows.filter((r) => r.status === 'failed').length

  const runStats = [
    { label: '状态', value: statusLabel, tone: (status === 'success' ? 'success' : status === 'failed' || status === 'partial_failed' ? 'danger' : 'default') as any },
    { label: '任务 ID', value: taskId ? `#${taskId.slice(0, 8)}` : '-' },
    { label: '部署 ID', value: deploymentId ? deploymentId.slice(0, 8) : '-' },
    { label: '服务器', value: `${serverExecutionRows.length} 台` },
    ...(serverExecutionRows.length > 0
      ? [
          { label: '成功', value: `${successServerCount} 台`, tone: (successServerCount > 0 ? 'success' : 'default') as any },
          { label: '失败', value: `${failedServerCount} 台`, tone: (failedServerCount > 0 ? 'danger' : 'default') as any },
        ]
      : []),
    { label: '耗时', value: report?.duration_seconds != null ? `${report.duration_seconds}s` : '-' },
    {
      label: '可回滚',
      value: report?.rollback_available ? '是' : '否',
      tone: (report?.rollback_available ? 'success' : 'default') as any,
    },
    ...(hasSnapshotConfig
      ? [{
          label: 'Pipeline',
          value: '快照隔离',
          tone: 'warning' as const,
        }]
      : []),
  ]

  const serverStatuses = serverExecutionRows.map((row) => ({
    name: row.name,
    status: row.status as 'pending' | 'running' | 'success' | 'failed',
    detail: row.last,
  }))

  return (
    <div className="card" style={{ display: 'grid', gap: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0 }}>发布流程 #{taskId} {deploymentId ? `(Deployment: ${deploymentId.slice(0, 8)})` : ''}</h3>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {deploymentId && ['running', 'pending'].includes(status) && (
            <button className="btn" onClick={onCancel} style={{ background: 'var(--danger-surface)', color: 'var(--danger)' }}>终止发布</button>
          )}
          {deploymentId && (
            <>
              {reportMarkdownUrl && <a className="btn" href={reportMarkdownUrl} target="_blank" rel="noreferrer" style={{ background: 'var(--border-strong)', color: 'var(--text-primary)', textDecoration: 'none' }}>报告MD</a>}
              {reportTextUrl && <a className="btn" href={reportTextUrl} target="_blank" rel="noreferrer" style={{ background: 'var(--border-strong)', color: 'var(--text-primary)', textDecoration: 'none' }}>报告TXT</a>}
              {report && <button className="btn" onClick={copyMarkdownReport} style={{ background: 'var(--action-soft)', color: 'var(--text-primary)' }}>复制报告</button>}
            </>
          )}
        </div>
      </div>

      <DeploymentRunHeader stats={runStats} />

      {serverExecutionRows.length > 0 && (
        <DeploymentServerGrid servers={serverStatuses} />
      )}

      {/* 失败归因 — 始终显示 */}
      {isFailed && failureReasons.length > 0 && (
        <div style={{ border: '1px solid var(--danger)', borderRadius: '10px', padding: '12px', background: 'color-mix(in srgb, var(--danger) 8%, transparent)', display: 'grid', gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
            <h4 style={{ margin: 0, color: 'var(--danger)' }}>失败归因摘要</h4>
            <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>{failureReasons.length} 条失败记录</span>
          </div>
          {failureReasons.slice(0, 8).map((r, idx) => (
            <div key={idx} style={{ fontSize: 12, display: 'grid', gridTemplateColumns: '140px 1fr', gap: 8, alignItems: 'start' }}>
              <span style={{ color: 'var(--text-muted)', fontFamily: 'monospace', fontSize: 11, wordBreak: 'break-all' }}>{r.source}</span>
              <span style={{ color: 'var(--danger)', wordBreak: 'break-word' }}>{r.message}</span>
            </div>
          ))}
          {failureReasons.length > 8 && (
            <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>… 还有 {failureReasons.length - 8} 条</div>
          )}
        </div>
      )}

      {report && (
        <div style={{ border: '1px solid var(--border-strong)', borderRadius: '10px', padding: '10px', background: 'var(--bg-page)', display: 'grid', gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <h4 style={{ margin: 0 }}>发布摘要</h4>
            <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>耗时 {report.duration_seconds ?? '-'} 秒 · 可回滚 {report.rollback_available ? '是' : '否'}</span>
          </div>
          <div style={{ color: report.failure_analysis?.status === 'failed' ? 'var(--danger)' : 'var(--text-secondary)' }}>{report.summary_text || '-'}</div>
          {failedServers.length > 0 && (
            <div style={{ display: 'grid', gap: 6 }}>
              <strong style={{ fontSize: 13 }}>失败服务器</strong>
              {failedServers.slice(0, 6).map((item: any, idx: number) => (
                <div key={`${item.server_name}-${idx}`} style={{ fontSize: 12, background: 'var(--danger-surface)', color: 'var(--danger)', borderRadius: 8, padding: '6px 8px' }}>
                  {item.server_name || '-'}：{item.message || item.status || '-'}
                </div>
              ))}
            </div>
          )}
          {reportSuggestions.length > 0 && (
            <div style={{ display: 'grid', gap: 4 }}>
              <strong style={{ fontSize: 13 }}>处理建议</strong>
              {reportSuggestions.map((item: string, idx: number) => <div key={idx} style={{ color: 'var(--text-muted)', fontSize: 12 }}>• {item}</div>)}
            </div>
          )}
        </div>
      )}

      {taskDetails?.distributions?.length > 0 && (
        <div style={{ border: '1px solid var(--border-strong)', borderRadius: '10px', padding: '10px', background: 'var(--bg-page)' }}>
          <h4 style={{ margin: '0 0 8px' }}>发布包分发</h4>
          <div style={{ display: 'grid', gap: 6 }}>
            {taskDetails.distributions.map((d: any) => (
              <div key={d.id} style={{ display: 'grid', gridTemplateColumns: '1.2fr .8fr .6fr 2fr', gap: 8, fontSize: 12 }}>
                <span style={{ fontFamily: 'monospace' }}>{d.server_name}</span>
                <StatusBadge value={d.status} />
                <span>{d.reused ? '复用' : '上传'}</span>
                <span style={{ color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.remote_path}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {taskDetails?.step_tasks?.length > 0 && (
        <div style={{ border: '1px solid var(--border-strong)', borderRadius: '10px', padding: '10px', background: 'var(--bg-page)' }}>
          <h4 style={{ margin: '0 0 8px' }}>步骤执行树</h4>
          <div style={{ display: 'grid', gap: 6, maxHeight: 320, overflow: 'auto' }}>
            {taskDetails.step_tasks.map((st: any) => (
              <div key={st.id} style={{ display: 'grid', gap: 4 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr .7fr 2fr', gap: 8, fontSize: 12 }}>
                  <span style={{ fontFamily: 'monospace' }}>{st.server_name || '-'}</span>
                  <span>{st.step_name}</span>
                  <StatusBadge value={st.status} />
                  <span style={{ color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{st.message || '-'}</span>
                </div>
                {st.config_drift && (
                  <ConfigDiff captured={st.captured_config} live={st.live_config} />
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <OperationTimeline steps={timelineSteps} />

      <LogConsole
        title="详细日志"
        entries={logs}
        maxRows={1000}
        maxHeight={420}
        onRefresh={onRefreshTask}
        onDownload={deploymentId ? undefined : undefined}
      />
    </div>
  )
}
