import { useState } from 'react'
import { RiskConfirmDialog } from '../../components/ui'
interface RetentionPolicyPanelProps {
  retentionPolicy: Record<string, any>
  retentionPreview: any
  retentionLoading: boolean
  retentionMessage: string
  retentionError: string
  loadRetention: () => void | Promise<void>
  previewRetention: () => void | Promise<void>
  saveRetention: () => void | Promise<void>
  cleanupRetention: () => void | Promise<void>
  updateRetentionField: (key: string, value: any) => void
}

const retentionFields = [
  ['deploy_success_keep_days', '成功发布保留天数', '非生产成功发布历史'],
  ['deploy_prod_success_keep_days', '生产成功保留天数', '生产发布建议保留更久'],
  ['deploy_failed_keep_days', '失败发布保留天数', '失败 / error 发布'],
  ['deploy_canceled_keep_days', '取消发布保留天数', 'canceled / cancelled 发布'],
  ['deploy_rollback_keep_days', '回滚记录保留天数', '回滚相关发布'],
  ['deploy_keep_max', '发布历史最大条数', '超过后从最旧记录清理'],
  ['audit_keep_days', '普通审计保留天数', '一般操作审计'],
  ['audit_high_risk_keep_days', '高风险审计保留天数', '发布、回滚、SQL、密钥、Token 等'],
  ['audit_keep_max', '审计最大条数', '超过后从最旧记录清理'],
  ['tool_call_keep_days', 'AI 工具调用保留天数', 'HTTP/MCP Tool 调用记录'],
  ['tool_call_keep_max', 'AI 工具调用最大条数', '超过后从最旧记录清理'],
  ['tool_plan_keep_days', 'AI 工具计划保留天数', '发布计划、配置变更计划'],
  ['tool_plan_keep_max', 'AI 工具计划最大条数', '超过后从最旧记录清理'],
  ['min_keep_days', '最短保护天数', '该天数内记录不清理'],
]

function RetentionSummary({ retentionPreview }: { retentionPreview: any }) {
  const summary = retentionPreview?.summary || {}
  const counts = retentionPreview?.candidate_counts || {}
  const deleted = retentionPreview?.deleted_counts || {}
  const archivePaths = retentionPreview?.archive_file_paths || []
  const skipped = retentionPreview?.skipped_reasons || []
  const rows = [
    ['发布历史', 'deployments'],
    ['旧版发布记录', 'deployment_records'],
    ['审计日志', 'audit_logs'],
    ['兼容审计记录', 'audit_records'],
    ['AI 工具调用', 'tool_call_logs'],
    ['AI 工具计划', 'tool_plans'],
  ]
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '8px', marginBottom: '12px' }}>
        {rows.map(([label, key]) => (
          <div key={key} style={{ border: '1px solid var(--border-strong)', borderRadius: '10px', padding: '10px', background: 'var(--bg-page)' }}>
            <div style={{ color: 'var(--text-muted)', fontSize: '12px' }}>{label}</div>
            <div style={{ marginTop: '4px', fontSize: '22px', fontWeight: 800, color: Number(counts[key] || 0) > 0 ? 'var(--warning)' : 'var(--success)' }}>{counts[key] || 0}</div>
            <div style={{ color: 'var(--text-muted)', fontSize: '11px' }}>总量 {summary[key]?.total ?? '-'}</div>
            {deleted[key] !== undefined && (
              <div style={{ color: 'var(--success)', fontSize: '11px', marginTop: '2px' }}>已清理 {deleted[key]}</div>
            )}
          </div>
        ))}
      </div>
      {archivePaths.length > 0 && (
        <details style={{ marginBottom: '8px' }}>
          <summary style={{ cursor: 'pointer', color: 'var(--primary)', fontSize: '13px' }}>归档文件路径 ({archivePaths.length})</summary>
          <div style={{ marginTop: '6px', maxHeight: '160px', overflow: 'auto', background: 'var(--bg-surface)', borderRadius: '8px', padding: '8px 12px', fontSize: '12px' }}>
            {archivePaths.map((p: string, i: number) => (
              <div key={i} style={{ fontFamily: 'monospace', color: 'var(--text-secondary)' }}>{p}</div>
            ))}
          </div>
        </details>
      )}
      {skipped.length > 0 && (
        <details style={{ marginBottom: '8px' }}>
          <summary style={{ cursor: 'pointer', color: 'var(--warning)', fontSize: '13px' }}>跳过明细 ({skipped.length})</summary>
          <div style={{ marginTop: '6px', background: 'var(--bg-surface)', borderRadius: '8px', padding: '8px 12px' }}>
            {skipped.map((r: any, i: number) => (
              <div key={i} style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '2px' }}>
                <span style={{ fontWeight: 600 }}>{r?.key || r?.table || '-'}</span>: {r?.reason || '-'}
              </div>
            ))}
          </div>
        </details>
      )}
    </>
  )
}

export default function RetentionPolicyPanel({
  retentionPolicy,
  retentionPreview,
  retentionLoading,
  retentionMessage,
  retentionError,
  loadRetention,
  previewRetention,
  saveRetention,
  cleanupRetention,
  updateRetentionField,
}: RetentionPolicyPanelProps) {
  const [cleanupDialogOpen, setCleanupDialogOpen] = useState(false)
  const cleanupCounts = retentionPreview?.candidate_counts || {}
  const cleanupTotal = Object.values(cleanupCounts).reduce((sum: number, value: any) => sum + Number(value || 0), 0)
  const confirmCleanup = async () => {
    setCleanupDialogOpen(false)
    await cleanupRetention()
  }

  return (
    <div className="card" style={{ marginBottom: '16px', borderColor: 'var(--primary-soft)', background: 'linear-gradient(135deg, rgba(59,130,246,.04), rgba(14,165,233,.03))' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px', marginBottom: '12px' }}>
        <div>
          <h3 style={{ margin: 0 }}>发布历史 / 审计日志清理策略</h3>
          <p style={{ margin: '4px 0 0', color: 'var(--text-muted)', fontSize: '13px' }}>
            按天数和最大条数清理发布历史、运行日志、包分发记录、审计日志、AI 工具调用与操作计划。清理前请先预览。
          </p>
        </div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <button className="btn" onClick={loadRetention} disabled={retentionLoading}>{retentionLoading ? '加载中...' : '刷新策略'}</button>
          <button className="btn" onClick={previewRetention} disabled={retentionLoading}>清理预览</button>
          <button className="btn primary" onClick={saveRetention} disabled={retentionLoading}>保存策略</button>
          <button className="btn" onClick={() => setCleanupDialogOpen(true)} disabled={retentionLoading} style={{ background: 'var(--danger-surface)', color: 'var(--danger)' }}>按策略清理</button>
        </div>
      </div>
      {retentionError && <div className="alert alert-error" style={{ marginBottom: 10 }}>{retentionError}</div>}
      {retentionMessage && <div className="alert alert-success" style={{ marginBottom: 10 }}>{retentionMessage}</div>}
      <div className="form-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', marginBottom: '12px' }}>
        {retentionFields.map(([key, label, hint]) => (
          <label key={key}>{label}
            <input
              type="number"
              min={0}
              value={retentionPolicy[key] ?? ''}
              onChange={(e: any) => updateRetentionField(key, Number(e.target.value || 0))}
            />
            <span style={{ display: 'block', color: 'var(--text-muted)', fontSize: '11px', marginTop: '4px' }}>{hint}</span>
          </label>
        ))}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px', color: 'var(--text-secondary)', fontSize: '13px' }}>
        <input type="checkbox" checked={Boolean(retentionPolicy.dry_run)} onChange={(e: any) => updateRetentionField('dry_run', e.target.checked)} />
        默认只做 dry-run 预览。真实清理必须点击“按策略清理”并二次确认。
      </div>
      {retentionPreview ? <RetentionSummary retentionPreview={retentionPreview} /> : <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>暂无清理预览，点击“清理预览”查看命中记录数量。</div>}
      {retentionPreview?.summary?.deployments?.sample?.length > 0 && (
        <details style={{ marginTop: '12px' }}>
          <summary style={{ cursor: 'pointer', color: 'var(--primary)' }}>查看发布历史清理样例</summary>
          <pre style={{ marginTop: '8px', maxHeight: '220px', overflow: 'auto', background: 'var(--bg-surface)', borderRadius: '10px', padding: '10px' }}>{JSON.stringify(retentionPreview.summary.deployments.sample, null, 2)}</pre>
        </details>
      )}
      <RiskConfirmDialog
        open={cleanupDialogOpen}
        title="确认按策略清理发布与审计记录"
        description="将按当前保留策略清理发布历史、审计日志和 AI 工具调用记录。建议先执行清理预览。"
        target={`预计清理 ${cleanupTotal} 条记录`}
        confirmText={`CLEANUP RETENTION ${cleanupTotal}`}
        value=""
        onValueChange={() => {}}
        onCancel={() => setCleanupDialogOpen(false)}
        onConfirm={confirmCleanup}
        riskLevel={cleanupTotal > 0 ? 'high' : 'medium'}
        details={[
          { label: '发布历史', value: cleanupCounts.deployments || 0 },
          { label: '审计日志', value: cleanupCounts.audit_logs || 0 },
          { label: 'AI 工具调用', value: cleanupCounts.tool_call_logs || 0 },
          { label: 'AI 工具计划', value: cleanupCounts.tool_plans || 0 },
        ]}
        confirmButtonLabel="确认清理"
        confirmMode="one-click"
      />
    </div>
  )
}
