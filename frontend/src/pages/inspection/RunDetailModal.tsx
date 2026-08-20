import { RiskBadge, StatusBadge } from '../../components/ui'
import { extractShellCommand, formatExecutionEntry, riskLabel } from './inspectionHelpers'

export type RunDetailModalProps = {
  result: any
  onClose: () => void
}

export function RunDetailModal({ result, onClose }: RunDetailModalProps) {
  const run = result?.run || {}
  const running = run.status === 'RUNNING' || run.status === 'PENDING'
  const target = run.server_id || run.project_id || '-'
  return (
    <div className="inspection-modal-overlay" onClick={onClose}>
      <div className="panel-card inspection-run-detail-modal" onClick={(e) => e.stopPropagation()}>
        <div className="inspection-modal-header">
          <div>
            <h2 style={{ margin: 0 }}>巡检详情</h2>
            <p className="muted" style={{ margin: '4px 0 0' }}>
              {run.id ? `运行 ID: ${run.id} · ` : ''}{run.scope_type ? `${run.scope_type} · ` : ''}对象：{target}
            </p>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <StatusBadge value={run.status || '-'} />
            <button className="btn btn-subtle" onClick={onClose}>关闭</button>
          </div>
        </div>
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <strong>{run.summary || result?.summary || '-'}</strong>
          </div>
          {running && <div className="alert alert-info">巡检执行中，页面每 2 秒自动刷新执行过程。已完成 {(result?.items || []).length} 项。</div>}
          {result?.ledger && <div className="stats-grid"><div className="stat-card"><span>巡检项</span><strong>{result.ledger.item_count}</strong><small>已入台账</small></div><div className="stat-card"><span>未闭环</span><strong>{result.ledger.open_issue_count}</strong><small>待处理/处理中</small></div><div className="stat-card"><span>已闭环</span><strong>{result.ledger.closed_issue_count}</strong><small>已修复/验证/忽略</small></div><div className="stat-card"><span>执行耗时</span><strong>{result.ledger.duration_text}</strong><small>{result.ledger.trigger_type || '-'}</small></div></div>}
          {(result?.category_summary || []).length > 0 && <div className="table-scroll"><table className="data-table"><thead><tr><th style={{ minWidth: 120 }}>分类</th><th style={{ width: 80 }}>总数</th><th style={{ width: 80 }}>通过</th><th style={{ width: 80 }}>风险</th><th style={{ width: 80 }}>错误</th><th style={{ width: 100 }}>高/中/低</th></tr></thead><tbody>{(result.category_summary || []).map((c: any) => <tr key={c.category}><td><span className="ellipsis" style={{ maxWidth: 180 }} title={c.category}>{c.category}</span></td><td>{c.total}</td><td>{c.pass}</td><td>{c.risk}</td><td>{c.error}</td><td>{c.high}/{c.medium}/{c.low}</td></tr>)}</tbody></table></div>}
          <div className="table-scroll"><table className="data-table"><thead><tr><th style={{ width: 120 }}>分类</th><th style={{ minWidth: 160 }}>巡检项</th><th style={{ width: 80 }}>等级</th><th style={{ minWidth: 200 }}>结果</th><th style={{ minWidth: 220 }}>可验证 Shell 命令</th><th style={{ minWidth: 200 }}>建议</th></tr></thead><tbody>{(result?.items || []).length === 0 ? <tr><td colSpan={6}>暂无巡检项结果；如果状态为 RUNNING，请等待执行输出。</td></tr> : (result.items || []).map((i: any) => <tr key={i.id}><td>{i.category}</td><td>{i.item_name}</td><td><RiskBadge level={i.risk_level} label={riskLabel(i.risk_level)} /></td><td><div style={{ wordBreak: 'break-word' }}>{i.message}{i.parsed_facts?.summary ? <><br /><small className="muted" style={{ color: 'var(--accent, #3182ce)' }}>📊 {i.parsed_facts.summary}</small></> : ''}</div></td><td><pre className="inspection-command-cell">{extractShellCommand(i.raw_output, i.command) || '-'}</pre></td><td><div style={{ wordBreak: 'break-word' }}>{i.suggestion}</div></td></tr>)}</tbody></table></div>
          <div>
            <strong>执行过程</strong>
            <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 320, overflow: 'auto', background: 'rgba(15,23,42,.05)', borderRadius: 12, padding: 12 }}>
{(result?.logs || result?.items || []).map((l: any) => formatExecutionEntry(l)).join('\n\n---\n\n') || '暂无执行输出'}
            </pre>
          </div>
        </div>
        <div className="inspection-modal-actions">
          <button className="btn primary" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}