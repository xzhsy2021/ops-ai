import type { Dispatch, SetStateAction } from 'react'

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  draft: { bg: 'var(--border-strong)', color: 'var(--text-secondary)' },
  ready: { bg: 'var(--brand-surface)', color: 'var(--brand-soft)' },
  pending_approval: { bg: 'var(--warning-surface)', color: 'var(--warning)' },
  approved: { bg: 'var(--success-surface)', color: 'var(--success)' },
  running: { bg: 'var(--brand-surface)', color: 'var(--brand-soft)' },
  paused: { bg: 'var(--warning-surface)', color: 'var(--warning)' },
  completed: { bg: 'var(--success-surface)', color: 'var(--success)' },
  failed: { bg: 'var(--danger-surface)', color: 'var(--danger)' },
  cancelled: { bg: 'var(--border-strong)', color: 'var(--text-secondary)' },
}

const RISK_COLORS: Record<string, { bg: string; color: string }> = {
  low: { bg: 'var(--success-surface)', color: 'var(--success)' },
  medium: { bg: 'var(--warning-surface)', color: 'var(--warning)' },
  high: { bg: 'var(--danger-surface)', color: 'var(--danger)' },
}

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  ready: '就绪',
  pending_approval: '待复核',
  approved: '已复核',
  running: '运行中',
  paused: '已暂停',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const RISK_LABELS: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
}

function Badge({ label, variant, value }: { label: string; variant: 'status' | 'risk'; value: string }) {
  const colors = variant === 'status' ? STATUS_COLORS[value] : RISK_COLORS[value]
  if (!colors) return <span>{label}</span>
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: '4px',
      fontSize: '12px',
      fontWeight: 500,
      background: colors.bg,
      color: colors.color,
      animation: value === 'running' ? 'pulse 2s infinite' : undefined,
    }}>
      {label}
    </span>
  )
}

interface CleanupJobDetailProps {
  jobDetail: any
  dryRunResult: any
  dryRunLoading: boolean
  startPlan: any
  setConfirmText: Dispatch<SetStateAction<string>>
  actionLoading: boolean
  isAdmin: boolean
  jobBatches: any[]
  jobEvents: any[]
  onBack: () => void
  onDryRun: () => void
  onJobAction: (action: string) => void
}

export default function CleanupJobDetail({
  jobDetail,
  dryRunResult,
  dryRunLoading,
  startPlan,
  setConfirmText,
  actionLoading,
  isAdmin,
  jobBatches,
  jobEvents,
  onBack,
  onDryRun,
  onJobAction,
}: CleanupJobDetailProps) {
  if (!jobDetail) return null
  const status = jobDetail.status
  const progressTotal = Number(jobDetail.matched_rows || dryRunResult?.matched_rows || 0)
  const progressDone = Number(jobDetail.deleted_rows || 0)
  const progressPercent = progressTotal > 0 ? Math.min(100, Math.round(progressDone / progressTotal * 100)) : 0
  const riskTips = [
    dryRunResult?.risk_level === 'high' ? '高风险：匹配行数较多、无保护阈值或缺少索引时，建议拆分窗口并确认已有备份。' : '',
    dryRunResult && !dryRunResult.has_index ? `索引提醒：${jobDetail.date_column} 未检测到索引，建议补充索引或缩小评估时间范围。` : '',
    dryRunResult?.matched_rows > (jobDetail.max_delete_rows || Number.MAX_SAFE_INTEGER) ? '匹配行数超过参考阈值，建议复核评估条件。' : '',
  ].filter(Boolean)
  const finalConfirmText = startPlan?.confirm_text || `CLEAN ${jobDetail.table_name} BEFORE ${jobDetail.cutoff_time}`
  const startBlockers = startPlan?.blockers || []
  const startWarnings = startPlan?.warnings || []
  const startSummary = startPlan?.summary || {}

  return (
    <div style={{ display: 'grid', gap: '16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
          <button className="btn" onClick={onBack}
            style={{ background: 'var(--border-strong)', color: 'var(--text-primary)', padding: '6px 14px' }}>
            ← 返回
          </button>
          <h3 style={{ margin: 0 }}>{jobDetail.name}</h3>
          <Badge label={STATUS_LABELS[status] || status} variant="status" value={status} />
          {jobDetail.risk_level && <Badge label={RISK_LABELS[jobDetail.risk_level] || jobDetail.risk_level} variant="risk" value={jobDetail.risk_level} />}
        </div>
      </div>

      <div className="card">
        <h4 style={{ margin: '0 0 12px 0' }}>任务信息</h4>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px 24px', fontSize: '13px' }}>
          {[
            ['环境', jobDetail.environment],
            ['连接', jobDetail.connection_name],
            ['数据库', jobDetail.database_name],
            ['表名', jobDetail.table_name],
            ['日期列', jobDetail.date_column],
            ['截止时间', jobDetail.cutoff_time],
            ['批次大小', jobDetail.batch_size],
            ['批次间隔', `${jobDetail.batch_interval_seconds}s`],
            ['参考风险阈值', jobDetail.max_delete_rows || '未设置'],
            ['需要复核', jobDetail.approval_required ? '是' : '否'],
            ['创建人', jobDetail.created_by],
            ['创建时间', jobDetail.created_at],
          ].map(([label, value]) => (
            <div key={label as string}>
              <span style={{ color: 'var(--text-muted)' }}>{label}: </span>
              <span style={{ color: 'var(--text-primary)' }}>{value || '-'}</span>
            </div>
          ))}
        </div>
      </div>

      {(status === 'running' || status === 'paused' || status === 'completed') && (
        <div className="card">
          <h4 style={{ margin: '0 0 12px 0' }}>执行进度</h4>
          <div style={{ height: '10px', background: 'var(--bg-page)', borderRadius: '999px', overflow: 'hidden', marginBottom: '8px' }}>
            <div style={{ width: `${progressPercent}%`, height: '100%', background: progressPercent === 100 ? 'var(--success-solid)' : 'var(--brand)' }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--text-secondary)', fontSize: '13px' }}>
            <span>已处理 {progressDone.toLocaleString()} 行</span>
            <span>{progressTotal ? `匹配 ${progressTotal.toLocaleString()} 行 · ${progressPercent}%` : '等待 Dry Run 或执行统计'}</span>
          </div>
        </div>
      )}

      <div className="card">
        <h4 style={{ margin: '0 0 12px 0' }}>操作</h4>
        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'center' }}>
          {status === 'draft' && (
            <button className="btn" onClick={onDryRun} disabled={dryRunLoading}
              style={{ background: 'var(--brand-surface)', color: 'var(--brand-soft)' }}>
              {dryRunLoading ? '执行中...' : 'Dry Run'}
            </button>
          )}
          {status === 'ready' && (
            <button className="btn" onClick={() => onJobAction('submit')} disabled={actionLoading}
              style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>
              提交复核
            </button>
          )}
          {status === 'pending_approval' && isAdmin && (
            <>
              <button className="btn" onClick={() => onJobAction('approve')} disabled={actionLoading}
                style={{ background: 'var(--success-surface)', color: 'var(--success)' }}>
                复核通过
              </button>
              <button className="btn" onClick={() => onJobAction('reject')} disabled={actionLoading}
                style={{ background: 'var(--danger-surface)', color: 'var(--danger)' }}>
                拒绝
              </button>
            </>
          )}
          {status === 'approved' && (
            <div style={{ display: 'grid', gap: '12px', width: '100%' }}>
              <div style={{ border: '1px solid var(--border-strong)', borderRadius: '10px', padding: '12px', background: 'var(--bg-page)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', marginBottom: '10px' }}>
                  <strong style={{ color: 'var(--text-primary)' }}>执行预检结果</strong>
                  <span style={{ color: startBlockers.length ? 'var(--danger)' : 'var(--success)', fontSize: 13, fontWeight: 700 }}>
                    {startBlockers.length ? '已阻断写操作' : '可执行'}
                  </span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '8px', marginBottom: '10px', fontSize: 13 }}>
                  <div><span style={{ color: 'var(--text-muted)' }}>匹配：</span>{(startSummary.matched_rows ?? jobDetail.matched_rows ?? 0).toLocaleString?.() || startSummary.matched_rows || jobDetail.matched_rows || 0} 行</div>
                  <div><span style={{ color: 'var(--text-muted)' }}>参考阈值：</span>{(startSummary.protected_delete_rows ?? dryRunResult?.protected_delete_rows ?? '-').toLocaleString?.() || startSummary.protected_delete_rows || '-'}</div>
                  <div><span style={{ color: 'var(--text-muted)' }}>剩余参考额度：</span>{startSummary.remaining_delete_cap ?? '不限制'}</div>
                  <div><span style={{ color: 'var(--text-muted)' }}>预计执行批次：</span>{startSummary.estimated_batches_to_limit ?? jobDetail.estimated_batches ?? '-'}</div>
                </div>
                {(startBlockers.length > 0 || startWarnings.length > 0 || (startPlan?.recommendations || []).length > 0) && (
                  <div style={{ fontSize: 13, lineHeight: 1.7 }}>
                    {startBlockers.map((x: string, i: number) => <div key={`b-${i}`} style={{ color: 'var(--danger)' }}>阻断：{x}</div>)}
                    {startWarnings.map((x: string, i: number) => <div key={`w-${i}`} style={{ color: 'var(--warning)' }}>警告：{x}</div>)}
                    {(startPlan?.recommendations || []).map((x: string, i: number) => <div key={`r-${i}`} style={{ color: 'var(--text-secondary)' }}>建议：{x}</div>)}
                  </div>
                )}
              </div>
              <div style={{ display: 'grid', gap: '10px' }}>
                <div className="risk-confirm-oneclick-note">
                  <strong>一键确认执行</strong>
                  <span>确认后将按批次执行 DELETE。请核对连接、表名、截止时间、匹配行数和保护阈值。</span>
                  <code>{finalConfirmText}</code>
                </div>
                <button className="btn" onClick={() => { setConfirmText(finalConfirmText); onJobAction('start') }} disabled={actionLoading || startBlockers.length > 0}
                  style={{ background: 'var(--danger-surface)', color: 'var(--danger)', width: 'fit-content' }}>
                  {actionLoading ? '执行中...' : '开始分批清理'}
                </button>
              </div>
            </div>
          )}
          {status === 'running' && (
            <button className="btn" onClick={() => onJobAction('pause')} disabled={actionLoading}
              style={{ background: 'var(--warning-surface)', color: 'var(--warning)' }}>
              暂停
            </button>
          )}
          {status === 'paused' && (
            <>
              <button className="btn" onClick={() => onJobAction('resume')} disabled={actionLoading}
                style={{ background: 'var(--success-surface)', color: 'var(--success)' }}>
                继续执行
              </button>
              <button className="btn" onClick={() => onJobAction('cancel')} disabled={actionLoading}
                style={{ background: 'var(--danger-surface)', color: 'var(--danger)' }}>
                取消
              </button>
            </>
          )}
        </div>
      </div>

      {dryRunResult && (
        <div className="card">
          <h4 style={{ margin: '0 0 12px 0' }}>Dry Run 结果</h4>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: '12px', marginBottom: '12px' }}>
            {[
              ['匹配行数', dryRunResult.matched_rows],
              ['参考阈值行数', dryRunResult.protected_delete_rows ?? dryRunResult.matched_rows],
              ['预计批次', dryRunResult.estimated_batches_to_limit ?? dryRunResult.estimated_batches],
              ['风险等级', dryRunResult.risk_level],
              ['有索引', dryRunResult.has_index ? '是' : '否'],
              ['采样批次', dryRunResult.effective_first_batch_size ?? jobDetail.batch_size],
              ['超过参考阈值', dryRunResult.will_stop_at_max_delete_rows ? '是' : '否'],
              ['当前执行窗口', dryRunResult.execution_window?.currently_allowed === false ? '不允许' : '允许'],
            ].map(([label, value]) => (
              <div key={label as string} style={{ background: 'var(--bg-page)', padding: '12px', borderRadius: '6px', textAlign: 'center' }}>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '4px' }}>{label}</div>
                <div style={{ fontSize: '16px', fontWeight: 'bold', color: 'var(--text-primary)' }}>
                  {label === '风险等级' && value ? <Badge label={RISK_LABELS[value as string] || value as string} variant="risk" value={value as string} /> : String(value ?? '-')}
                </div>
              </div>
            ))}
          </div>
          {(riskTips.length > 0 || (dryRunResult.recommendations || []).length > 0) && (
            <div className={`risk-panel risk-panel--${dryRunResult.risk_level || 'medium'}`} style={{ marginBottom: '12px' }}>
              <div style={{ color: 'var(--text-strong)', fontWeight: 700, marginBottom: '6px' }}>风险解释与建议</div>
              <ul style={{ margin: 0, paddingLeft: '18px', color: 'var(--text-secondary)', fontSize: '13px', lineHeight: 1.7 }}>
                {[...riskTips, ...(dryRunResult.recommendations || [])].map((tip, i) => <li key={i}>{tip}</li>)}
              </ul>
            </div>
          )}
          {(dryRunResult.first_row_time || dryRunResult.batch_boundary_time || (dryRunResult.index_names || []).length > 0) && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12, fontSize: 13 }}>
              <div><span style={{ color: 'var(--text-muted)' }}>最早待删数据：</span>{dryRunResult.first_row_time || '-'}</div>
              <div><span style={{ color: 'var(--text-muted)' }}>首批边界时间：</span>{dryRunResult.batch_boundary_time || '-'}</div>
              <div><span style={{ color: 'var(--text-muted)' }}>命中索引：</span>{(dryRunResult.index_names || []).join(', ') || '-'}</div>
            </div>
          )}
          {dryRunResult.generated_sql && (
            <div style={{ background: 'var(--bg-page)', padding: '12px', borderRadius: '6px', fontFamily: 'monospace', fontSize: '13px', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
              {dryRunResult.generated_sql}
            </div>
          )}
        </div>
      )}

      {jobBatches.length > 0 && (
        <div className="card">
          <h4 style={{ margin: '0 0 12px 0' }}>批次执行日志</h4>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                  {['批次', '影响行数', '耗时(ms)', '状态', '错误信息', '开始时间'].map(h => (
                    <th key={h} style={{ padding: '8px 10px', textAlign: 'left', color: 'var(--text-secondary)', fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {jobBatches.map((b: any) => (
                  <tr key={b.batch_no} style={{ borderBottom: '1px solid var(--bg-surface)' }}>
                    <td style={{ padding: '8px 10px', color: 'var(--text-primary)' }}>{b.batch_no}</td>
                    <td style={{ padding: '8px 10px', color: 'var(--text-secondary)' }}>{b.affected_rows}</td>
                    <td style={{ padding: '8px 10px', color: 'var(--text-secondary)' }}>{b.duration_ms}</td>
                    <td style={{ padding: '8px 10px' }}>
                      <Badge label={STATUS_LABELS[b.status] || b.status} variant="status" value={b.status} />
                    </td>
                    <td style={{ padding: '8px 10px', color: 'var(--danger)', fontSize: '12px', maxWidth: '300px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.error_message || '-'}</td>
                    <td style={{ padding: '8px 10px', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{b.started_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {jobEvents.length > 0 && (
        <div className="card">
          <h4 style={{ margin: '0 0 12px 0' }}>事件日志</h4>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                  {['事件类型', '操作人', '消息', '时间'].map(h => (
                    <th key={h} style={{ padding: '8px 10px', textAlign: 'left', color: 'var(--text-secondary)', fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {jobEvents.map((ev: any, i: number) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--bg-surface)' }}>
                    <td style={{ padding: '8px 10px', color: 'var(--text-primary)' }}>{ev.event_type}</td>
                    <td style={{ padding: '8px 10px', color: 'var(--text-secondary)' }}>{ev.operator}</td>
                    <td style={{ padding: '8px 10px', color: 'var(--text-secondary)' }}>{ev.message}</td>
                    <td style={{ padding: '8px 10px', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{ev.created_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
