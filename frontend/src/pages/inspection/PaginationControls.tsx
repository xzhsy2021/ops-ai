export const PAGE_SIZE_OPTIONS = [10, 20, 50, 100, 200]
export const DEFAULT_PAGE_SIZE = 10

export type PaginationControlsProps = {
  total: number
  limit: number
  offset: number
  onChange: (nextOffset: number) => void
  onPageSizeChange?: (next: number) => void
  pageSizeOptions?: number[]
}

export function PaginationControls({
  total,
  limit,
  offset,
  onChange,
  onPageSizeChange,
  pageSizeOptions = PAGE_SIZE_OPTIONS,
}: PaginationControlsProps) {
  const safeTotal = Math.max(0, Number(total || 0))
  const safeLimit = Math.max(1, Number(limit || DEFAULT_PAGE_SIZE))
  const safeOffset = Math.max(0, Number(offset || 0))
  const page = Math.floor(safeOffset / safeLimit) + 1
  const pages = Math.max(1, Math.ceil(safeTotal / safeLimit))
  const from = safeTotal === 0 ? 0 : safeOffset + 1
  const to = Math.min(safeOffset + safeLimit, safeTotal)

  return (
    <div className="pagination-bar" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginTop: 12, flexWrap: 'wrap' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <small className="muted">第 {page}/{pages} 页 · 显示 {from}-{to} / 共 {safeTotal} 条</small>
        {onPageSizeChange && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--muted, #666)' }}>
            每页
            <select
              value={safeLimit}
              onChange={(e) => {
                const next = Number(e.target.value)
                onPageSizeChange(next)
                onChange(0)
              }}
              style={{ padding: '2px 6px' }}
            >
              {pageSizeOptions.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            条
          </label>
        )}
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn btn-subtle" disabled={safeOffset <= 0} onClick={() => onChange(0)}>首页</button>
        <button className="btn btn-subtle" disabled={safeOffset <= 0} onClick={() => onChange(Math.max(0, safeOffset - safeLimit))}>上一页</button>
        <button className="btn btn-subtle" disabled={safeOffset + safeLimit >= safeTotal} onClick={() => onChange(safeOffset + safeLimit)}>下一页</button>
        <button className="btn btn-subtle" disabled={safeOffset + safeLimit >= safeTotal} onClick={() => onChange(Math.max(0, (pages - 1) * safeLimit))}>末页</button>
      </div>
    </div>
  )
}
