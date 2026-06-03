import { OperationDrawer } from '../../components/OperationDrawer'
import { RiskBadge } from '../../components/ui'
import type { ToolInfo } from './ToolCatalogPanel'

function riskLabel(risk?: string) {
  const map: Record<string, string> = {
    critical: '严重风险', high: '高风险', medium: '中风险',
    low: '低风险', read: '只读', safe: '安全',
    dangerous: '需确认', blocked: '已拦截',
  }
  return map[risk || ''] || risk || '未分级'
}

export function ToolDetailDrawer({
  open,
  tool,
  toolDetail,
  onClose,
  onOpenPlayground,
}: {
  open: boolean
  tool: ToolInfo | null
  toolDetail: any
  onClose: () => void
  onOpenPlayground?: () => void
}) {
  if (!tool) return null

  return (
    <OperationDrawer open={open} title={tool.title || tool.name} width={540} onClose={onClose}
      footer={
        <div style={{ display: 'flex', gap: 8 }}>
          {onOpenPlayground && (
            <button className="btn btn-primary" onClick={onOpenPlayground}>
              在 Playground 中调试
            </button>
          )}
          <button className="btn btn-subtle" onClick={onClose}>关闭</button>
        </div>
      }
    >
      <div style={{ display: 'grid', gap: 16 }}>
        <div style={{ display: 'grid', gap: 8 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <code style={{ color: 'var(--text-primary)', fontSize: 14, fontWeight: 700 }}>{tool.name}</code>
            <RiskBadge level={tool.risk} label={riskLabel(tool.risk)} />
            {tool.write && <span className="tag tag-danger">写操作</span>}
            {tool.requires_confirmation && <span className="tag tag-warning">需确认</span>}
            {!tool.available && <span className="tag tag-danger">不可用</span>}
          </div>
          <p style={{ margin: 0, color: 'var(--text-secondary)', lineHeight: 1.6 }}>{tool.description}</p>
          {tool.blocked_reason && (
            <div className="alert alert-warning" style={{ margin: 0 }}>
              <strong>阻断原因：</strong>{tool.blocked_reason}
            </div>
          )}
        </div>

        <div style={{ display: 'grid', gap: 6 }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', color: 'var(--text-muted)', fontSize: 12 }}>
            <span>分类：<strong style={{ color: 'var(--text-primary)' }}>{tool.category || '-'}</strong></span>
            <span>权限范围：<strong style={{ color: 'var(--text-primary)' }}>{(tool.scopes || []).join(', ') || '-'}</strong></span>
          </div>
          {toolDetail?.recent_stats && (
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', color: 'var(--text-muted)', fontSize: 12 }}>
              <span>最近调用耗时：<strong style={{ color: 'var(--text-primary)' }}>{toolDetail.recent_stats.avg_duration_ms || '-'}ms</strong></span>
              <span>成功率：<strong style={{ color: 'var(--text-primary)' }}>{toolDetail.recent_stats.success_rate != null ? `${Math.round(toolDetail.recent_stats.success_rate * 100)}%` : '-'}</strong></span>
            </div>
          )}
        </div>

        {tool.input_schema && (
          <div>
            <strong style={{ color: 'var(--text-primary)', fontSize: 13 }}>输入 Schema</strong>
            <pre className="code-block small" style={{ marginTop: 6 }}>
              {JSON.stringify(tool.input_schema, null, 2)}
            </pre>
          </div>
        )}

        {tool.output_schema && (
          <div>
            <strong style={{ color: 'var(--text-primary)', fontSize: 13 }}>输出 Schema</strong>
            <pre className="code-block small" style={{ marginTop: 6 }}>
              {JSON.stringify(tool.output_schema, null, 2)}
            </pre>
          </div>
        )}

        {toolDetail?.recent_calls && toolDetail.recent_calls.length > 0 && (
          <div>
            <strong style={{ color: 'var(--text-primary)', fontSize: 13 }}>最近调用</strong>
            <div className="timeline-list" style={{ marginTop: 6, maxHeight: 240 }}>
              {toolDetail.recent_calls.map((call: any, i: number) => (
                <div key={i} className="timeline-item">
                  <strong>{call.tool || tool.name}</strong>
                  <small>
                    <span>{call.created_at ? new Date(call.created_at).toLocaleString() : '-'}</span>
                    <span className={`tag ${call.status === 'success' ? 'tag-success' : 'tag-danger'}`}>{call.status || '-'}</span>
                    {call.duration_ms != null && <span>{call.duration_ms}ms</span>}
                  </small>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </OperationDrawer>
  )
}