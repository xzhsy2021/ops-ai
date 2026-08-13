import { useState } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { createPortal } from 'react-dom'
import { useFavoriteStore } from '../stores/favoriteStore'
import type { RiskActionResult } from './RiskActionGuard'

export function PageHeader({
  title,
  description,
  actions,
  badge,
  breadcrumbs,
}: {
  title: string
  description?: string
  actions?: ReactNode
  badge?: ReactNode
  breadcrumbs?: Array<{ label: string; href?: string }>
}) {
  return (
    <div className="page-header">
      <div>
        {breadcrumbs && breadcrumbs.length > 0 && (
          <div className="page-header-breadcrumbs">
            {breadcrumbs.map((crumb, i) => (
              <span key={i}>
                {crumb.href ? <a href={crumb.href}>{crumb.label}</a> : crumb.label}
                {i < breadcrumbs.length - 1 && <span className="page-header-breadcrumb-sep">/</span>}
              </span>
            ))}
          </div>
        )}
        <h1>
          {title}
          {badge && <span className="page-header-badge">{badge}</span>}
        </h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="page-header-actions">{actions}</div>}
    </div>
  )
}

export function FavoriteButton({ url, label = '收藏', category = 'general' }: { url: string; label?: string; category?: string }) {
  const favorites = useFavoriteStore((s) => s.favorites)
  const addFavorite = useFavoriteStore((s) => s.addFavorite)
  const removeFavorite = useFavoriteStore((s) => s.removeFavorite)
  const isFav = favorites.some((f) => f.url === url)

  const handleToggle = () => {
    if (isFav) {
      const f = favorites.find((f) => f.url === url)
      if (f) removeFavorite(f.id)
    } else {
      addFavorite({ label, description: '', url, category, pinned: false })
    }
  }

  return (
    <button className={`favorite-btn${isFav ? ' favorited' : ''}`} onClick={handleToggle} title={isFav ? '取消收藏' : '收藏'}>
      {isFav ? '★' : '☆'}
    </button>
  )
}

export function StatCard({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  )
}

export function EmptyState({ title, description }: { title: string; description?: string }) {
  return <div className="empty-state"><strong>{title}</strong>{description && <span>{description}</span>}</div>
}

export function StatusBadge({ value, label }: { value: string; label?: string }) {
  const normalized = (value || '').toLowerCase()
  const tone = ['success', 'completed', 'ready', 'approved', 'ok', 'online'].includes(normalized) ? 'success'
    : ['failed', 'error', 'cancelled', 'offline', 'high'].includes(normalized) ? 'danger'
    : ['partial_failed'].includes(normalized) ? 'warning'
    : ['running', 'pending_approval', 'paused', 'warning', 'medium'].includes(normalized) ? 'warning'
    : 'neutral'
  return <span className={`status-badge status-badge--${tone}`}>{label || value || '-'}</span>
}

export function OperationTimeline({ steps }: { steps: Array<{ name: string; status: string; detail?: string }> }) {
  return (
    <div className="operation-timeline">
      {steps.map((step, index) => (
        <div className={`timeline-step timeline-step--${step.status}`} key={`${step.name}-${index}`}>
          <div className="timeline-dot">{index + 1}</div>
          <div>
            <div className="timeline-title">{step.name}</div>
            {step.detail && <div className="timeline-detail">{step.detail}</div>}
          </div>
        </div>
      ))}
    </div>
  )
}


export function RiskBadge({ level, label }: { level?: string; label?: string }) {
  const normalized = (level || '').toLowerCase()
  const tone = ['critical', 'high', 'blocked', 'danger'].includes(normalized) ? 'danger'
    : ['medium', 'dangerous', 'warning'].includes(normalized) ? 'warning'
    : ['low', 'safe'].includes(normalized) ? 'success'
    : 'neutral'
  const text = label || ({ critical: '严重风险', high: '高风险', medium: '中风险', low: '低风险', read: '只读', safe: '安全', dangerous: '需确认', blocked: '已拦截' } as Record<string, string>)[normalized] || level || '未分级'
  return <span className={`status-badge status-badge--${tone}`}>{text}</span>
}

export function RiskConfirmDialog({
  open,
  title,
  description,
  target,
  confirmText,
  value,
  onValueChange,
  onCancel,
  onConfirm,
  riskLevel = 'high',
  details,
  reason,
  onReasonChange,
  reasonRequired = false,
  reasonLabel = '操作原因',
  confirmButtonLabel = '确认执行',
  confirmDisabled = false,
  confirmMode = 'type',
  showConfirmTextInOneClick = true,
  result,
}: {
  open: boolean
  title: string
  description?: string
  target: string
  confirmText: string
  value: string
  onValueChange: (value: string) => void
  onCancel: () => void
  onConfirm: () => void
  riskLevel?: string
  details?: Array<{ label: string; value: ReactNode }>
  reason?: string
  onReasonChange?: (value: string) => void
  reasonRequired?: boolean
  reasonLabel?: string
  confirmButtonLabel?: string
  confirmDisabled?: boolean
  confirmMode?: 'type' | 'one-click'
  showConfirmTextInOneClick?: boolean
  result?: RiskActionResult | null
}) {
  if (!open) return null

  const reasonReady = !reasonRequired || Boolean((reason || '').trim())
  const matched = confirmMode === 'one-click' ? reasonReady : value.trim() === confirmText && reasonReady
  const showResult = Boolean(result)

  // 关键：用 createPortal 把 dialog 渲染到 body，避免被父级（表格行/card）stacking context 影响导致子元素错位
  const overlay = (
    <div className="risk-confirm-overlay" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel() }}>
      <div className="risk-confirm-dialog" role="dialog" aria-modal="true" aria-label={title}>
        {!showResult ? (
          <>
            <div className="risk-confirm-header">
              <RiskBadge level={riskLevel} />
              <strong>{title}</strong>
            </div>
            {description && <p>{description}</p>}
            <div className="risk-confirm-target">影响对象：<code>{target}</code></div>
            {details && details.length > 0 && (
              <div className="risk-confirm-details">
                {details.map((item) => (
                  <div key={item.label}><strong>{item.label}：</strong>{item.value || '-'}</div>
                ))}
              </div>
            )}
            {onReasonChange && (
              <label className="risk-confirm-label risk-confirm-reason">
                {reasonLabel}{reasonRequired ? '（必填）' : ''}
                <textarea value={reason || ''} onChange={(e) => onReasonChange(e.target.value)} placeholder="例如：恢复前验证失败 / 清理过期备份 / 常规发布" />
              </label>
            )}
            {confirmMode === 'type' ? (
              <label className="risk-confirm-label">
                输入 <code>{confirmText}</code> 确认执行
                <input autoFocus value={value} onChange={(e) => onValueChange(e.target.value)} placeholder={confirmText} />
              </label>
            ) : (
              <div className="risk-confirm-oneclick-note">
                <strong>一键确认模式</strong>
                <span>请核对影响对象和风险信息。当前页面采用快捷确认，无需手动输入确认短语。</span>
                {showConfirmTextInOneClick && confirmText && <code>{confirmText}</code>}
              </div>
            )}
            <div className="risk-confirm-actions">
              <button className="btn btn-subtle" onClick={onCancel}>取消</button>
              <button className="btn btn-danger" disabled={!matched || confirmDisabled} onClick={onConfirm}>{confirmButtonLabel}</button>
            </div>
          </>
        ) : (
          <RiskConfirmResult
            title={title}
            result={result!}
            target={target}
            onClose={onCancel}
          />
        )}
      </div>
    </div>
  )

  return createPortal(overlay, document.body)
}

function RiskConfirmResult({
  title,
  result,
  target,
  onClose,
}: {
  title: string
  result: RiskActionResult
  target: string
  onClose: () => void
}) {
  const ok = result.success !== false
  const links = result.links || []
  const ids = [
    result.auditId && { label: '审计 ID', value: result.auditId },
    result.taskId && { label: '任务 ID', value: result.taskId },
    result.reportId && { label: '报告 ID', value: result.reportId },
  ].filter(Boolean) as Array<{ label: string; value: string }>

  return (
    <>
      <div className="risk-confirm-header">
        <span className={`risk-confirm-result-icon risk-confirm-result-icon--${ok ? 'success' : 'error'}`} aria-hidden="true">
          {ok ? '✓' : '✕'}
        </span>
        <strong>{ok ? `${title} 已执行` : `${title} 失败`}</strong>
      </div>
      <div className={`risk-confirm-result-card risk-confirm-result-card--${ok ? 'success' : 'error'}`}>
        <div className="risk-confirm-result-message">{result.message || (ok ? '操作已完成' : '操作执行失败')}</div>
        {target && <div className="risk-confirm-result-target">影响对象：<code>{target}</code></div>}
        {ids.length > 0 && (
          <div className="risk-confirm-result-ids">
            {ids.map((id) => (
              <div key={id.label}><strong>{id.label}</strong><code>{id.value}</code></div>
            ))}
          </div>
        )}
        {links.length > 0 && (
          <div className="risk-confirm-result-links">
            {links.map((link) => (
              <Link key={link.to + link.label} to={link.to} className={`risk-confirm-result-link risk-confirm-result-link--${link.tone || 'neutral'}`}>
                {link.label}
                <span aria-hidden="true">→</span>
              </Link>
            ))}
          </div>
        )}
      </div>
      <div className="risk-confirm-actions">
        <button className="btn btn-subtle" onClick={onClose}>关闭</button>
        {!ok && (
          <button className="btn btn-primary" onClick={onClose}>返回重试</button>
        )}
      </div>
    </>
  )
}


export function LoadingState({ message = '加载中...' }: { message?: string }) {
  return <div className="page-loading"><span className="loading-orb" /><div>{message}</div></div>
}

export function Skeleton({ type = 'text', count = 3 }: { type?: 'text' | 'card' | 'table-row' | 'block'; count?: number }) {
  return (
    <div className={`skeleton-group skeleton-${type}`}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="skeleton-pulse" />
      ))}
    </div>
  )
}

export function ErrorState({ title = '加载失败', description, action }: { title?: string; description?: string; action?: ReactNode }) {
  return <div className="card" style={{ color: 'var(--danger)', display: 'grid', gap: '8px' }}><strong>{title}</strong>{description && <span>{description}</span>}{action}</div>
}

export function DisabledReason({ reason }: { reason?: string }) {
  if (!reason) return null
  return <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginTop: '6px' }}>不可操作原因：{reason}</div>
}

export function PageSection({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return <section className="card" style={{ display: 'grid', gap: '12px' }}><div><h3 style={{ margin: 0 }}>{title}</h3>{subtitle && <p style={{ margin: '6px 0 0', color: 'var(--text-muted)', fontSize: '13px' }}>{subtitle}</p>}</div>{children}</section>
}

export function MarkdownCopyButton({ getText, label = '复制 Markdown' }: { getText: () => string; label?: string }) {
  return <button className="btn" onClick={() => void navigator.clipboard?.writeText(getText())} style={{ background: 'var(--action-soft)', color: 'var(--text-primary)' }}>{label}</button>
}


export type DataTableColumn<T> = {
  key: string
  title: ReactNode
  width?: string | number
  align?: 'left' | 'center' | 'right'
  render?: (row: T, index: number) => ReactNode
  getValue?: (row: T) => ReactNode
}

export function DataTable<T>({ columns, rows, rowKey, loading = false, emptyTitle = '暂无数据', emptyDescription, className = '' }: {
  columns: DataTableColumn<T>[]
  rows: T[]
  rowKey: (row: T, index: number) => string
  loading?: boolean
  emptyTitle?: string
  emptyDescription?: string
  className?: string
}) {
  if (loading) return <LoadingState message="加载列表中..." />
  if (!rows.length) return <EmptyState title={emptyTitle} description={emptyDescription} />
  return (
    <div className={`table-scroll ${className}`.trim()}>
      <table className="data-table data-table--compact">
        <thead><tr>{columns.map((column) => <th key={column.key} style={{ width: column.width, textAlign: column.align || 'left' }}>{column.title}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => <tr key={rowKey(row, index)}>{columns.map((column) => <td key={column.key} style={{ textAlign: column.align || 'left' }}>{column.render ? column.render(row, index) : column.getValue ? column.getValue(row) : '-'}</td>)}</tr>)}
        </tbody>
      </table>
    </div>
  )
}

export function FilterBar({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  return <div className="filter-bar"><div className="filter-bar-fields">{children}</div>{actions && <div className="filter-bar-actions">{actions}</div>}</div>
}

export function CopyButton({ text, getText, label = '复制', copiedLabel = '已复制' }: { text?: string; getText?: () => string; label?: string; copiedLabel?: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = async () => {
    const value = getText ? getText() : text || ''
    if (!value) return
    await navigator.clipboard?.writeText(value)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1200)
  }
  return <button className="btn btn-subtle" type="button" onClick={handleCopy}>{copied ? copiedLabel : label}</button>
}

export function ConfirmDialog({ open, title, description, confirmLabel = '确认', cancelLabel = '取消', danger = false, children, onConfirm, onCancel }: {
  open: boolean
  title: string
  description?: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  children?: ReactNode
  onConfirm: () => void
  onCancel: () => void
}) {
  if (!open) return null
  return (
    <div className="confirm-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onCancel() }}>
      <div className="confirm-dialog" role="dialog" aria-modal="true" aria-label={title}>
        <h3>{title}</h3>
        {description && <p>{description}</p>}
        {children && <div className="confirm-dialog-body">{children}</div>}
        <div className="confirm-dialog-actions">
          <button className="btn btn-subtle" type="button" onClick={onCancel}>{cancelLabel}</button>
          <button className={`btn ${danger ? 'btn-danger' : 'btn-primary'}`} type="button" onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  )
}

export type LogViewerEntry = { level?: string; message?: string; created_at?: string; time?: string; step_name?: string; server_name?: string }

export function LogViewer({ entries, title = '日志', emptyText = '暂无日志', maxHeight = 360, onRefresh, extraActions }: {
  entries: LogViewerEntry[]
  title?: string
  emptyText?: string
  maxHeight?: number
  onRefresh?: () => void
  extraActions?: ReactNode
}) {
  const [keyword, setKeyword] = useState('')
  const [level, setLevel] = useState('all')
  const filtered = entries.filter((entry) => {
    const entryLevel = (entry.level || 'info').toLowerCase()
    const levelOk = level === 'all' || entryLevel === level || (level === 'warn' && entryLevel === 'warning')
    if (!levelOk) return false
    const text = `${entry.created_at || entry.time || ''} ${entry.step_name || ''} ${entry.server_name || ''} ${entry.level || ''} ${entry.message || ''}`.toLowerCase()
    return !keyword.trim() || text.includes(keyword.trim().toLowerCase())
  })
  const copyErrors = () => {
    const candidates = entries.filter((entry) => `${entry.level || ''} ${entry.message || ''}`.toLowerCase().match(/error|失败|异常/))
    const content = (candidates.length ? candidates : entries).slice(-80).map((entry) => `[${(entry.created_at || entry.time || '').slice(11, 19) || '-'}] [${entry.step_name || entry.server_name || '-'}] ${entry.level || 'info'}: ${entry.message || ''}`).join('\n')
    void navigator.clipboard?.writeText(content)
  }
  return (
    <div className="log-viewer">
      <div className="log-viewer-toolbar">
        <div><strong>{title}</strong><span>{entries.length} 行，当前显示 {filtered.length} 行</span></div>
        <div className="log-viewer-actions">
          <input value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索日志" />
          <select value={level} onChange={(event) => setLevel(event.target.value)}><option value="all">全部级别</option><option value="error">错误</option><option value="warn">警告</option><option value="info">信息</option></select>
          <button className="btn btn-subtle" type="button" onClick={copyErrors}>复制错误上下文</button>
          {onRefresh && <button className="btn btn-subtle" type="button" onClick={onRefresh}>刷新</button>}
          {extraActions}
        </div>
      </div>
      <div className="terminal log-viewer-body" style={{ maxHeight }}>
        {!entries.length && <div style={{ color: 'var(--text-muted)' }}>{emptyText}</div>}
        {entries.length > 0 && !filtered.length && <div style={{ color: 'var(--text-muted)' }}>没有匹配当前筛选条件的日志。</div>}
        {filtered.map((entry, index) => <div key={index} className={`log-${entry.level || 'info'}`}><span style={{ color: 'var(--text-muted)' }}>[{(entry.created_at || entry.time || '').slice(11, 19) || '-'}]</span> <span style={{ color: 'var(--text-muted)' }}>[{entry.step_name || entry.server_name || '-'}]</span> {entry.message || ''}</div>)}
      </div>
    </div>
  )
}
