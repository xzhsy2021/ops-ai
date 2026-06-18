import { useState, useMemo, useCallback, type MouseEvent } from 'react'
import type { ReactNode } from 'react'
import { ChevronUp, ChevronDown, ChevronLeft, ChevronRight, ChevronsUpDown } from 'lucide-react'
import { LoadingState, EmptyState, ErrorState } from './ui'

export type EnhancedColumn<T> = {
  key: string
  title: ReactNode
  width?: string | number
  align?: 'left' | 'center' | 'right'
  sortable?: boolean
  render?: (row: T, index: number) => ReactNode
  getValue?: (row: T) => ReactNode
  getSortValue?: (row: T) => string | number
  className?: string
  cellClassName?: string
  label?: string
  sticky?: 'start' | 'end'
}

export type EnhancedDataTableProps<T> = {
  columns: EnhancedColumn<T>[]
  rows: T[]
  rowKey: (row: T, index: number) => string
  loading?: boolean
  emptyTitle?: string
  emptyDescription?: string
  error?: string
  errorAction?: ReactNode
  className?: string
  pageSize?: number
  currentPage?: number
  totalCount?: number
  onPageChange?: (page: number) => void
  onPageSizeChange?: (size: number) => void
  selectedKeys?: string[]
  onSelectionChange?: (keys: string[]) => void
  expandRow?: (row: T) => ReactNode
  expandedKeys?: string[]
  onExpandChange?: (keys: string[]) => void
  stickyHeader?: boolean
  maxHeight?: number
  onRowClick?: (row: T, index: number) => void
  stackOnNarrow?: boolean
}

export function EnhancedDataTable<T>({
  columns,
  rows,
  rowKey,
  loading = false,
  emptyTitle = '暂无数据',
  emptyDescription,
  error,
  errorAction,
  className = '',
  pageSize,
  currentPage = 1,
  totalCount,
  onPageChange,
  onPageSizeChange,
  selectedKeys,
  onSelectionChange,
  expandRow,
  expandedKeys,
  onExpandChange,
  stickyHeader = true,
  maxHeight,
  onRowClick,
  stackOnNarrow = false,
}: EnhancedDataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  const sorted = useMemo(() => {
    if (!sortKey) return rows
    const column = columns.find((c) => c.key === sortKey)
    if (!column?.sortable) return rows
    const getter = column.getSortValue || column.getValue
    if (!getter) return rows
    const dir = sortDir === 'asc' ? 1 : -1
    const sorted = [...rows].sort((a, b) => {
      const va = getter(a) ?? ''
      const vb = getter(b) ?? ''
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir
      return String(va).localeCompare(String(vb)) * dir
    })
    return sorted
  }, [rows, sortKey, sortDir, columns])

  const paginated = useMemo(() => {
    if (!pageSize || pageSize <= 0) return sorted
    const start = (currentPage - 1) * pageSize
    return sorted.slice(start, start + pageSize)
  }, [sorted, pageSize, currentPage])

  const totalPages = pageSize && totalCount ? Math.ceil(totalCount / pageSize) : 1

  const handleSort = useCallback((key: string) => {
    setSortKey((prev) => {
      if (prev === key) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
        return key
      }
      setSortDir('asc')
      return key
    })
  }, [])

  const allKeys = useMemo(() => rows.map((r, i) => rowKey(r, i)), [rows, rowKey])
  const allSelected = selectedKeys && selectedKeys.length === allKeys.length && allKeys.length > 0

  const handleSelectAll = () => {
    if (!onSelectionChange) return
    if (allSelected) {
      onSelectionChange([])
    } else {
      onSelectionChange(allKeys)
    }
  }

  const handleSelectRow = (key: string) => {
    if (!onSelectionChange || !selectedKeys) return
    if (selectedKeys.includes(key)) {
      onSelectionChange(selectedKeys.filter((k) => k !== key))
    } else {
      onSelectionChange([...selectedKeys, key])
    }
  }

  const handleToggleExpand = (key: string) => {
    if (!onExpandChange || !expandedKeys) return
    if (expandedKeys.includes(key)) {
      onExpandChange(expandedKeys.filter((k) => k !== key))
    } else {
      onExpandChange([...expandedKeys, key])
    }
  }

  if (error) {
    return <ErrorState title="加载失败" description={error} action={errorAction} />
  }

  if (loading) return <LoadingState message="加载列表中..." />

  if (!paginated.length) return <EmptyState title={emptyTitle} description={emptyDescription} />

  return (
    <div className="enhanced-table-wrap" style={maxHeight ? { maxHeight } : undefined}>
      <div className={`table-scroll ${className}`.trim()}>
        <table className={`data-table data-table--compact ${stickyHeader ? 'data-table--sticky' : ''}${stackOnNarrow ? ' stack-on-narrow' : ''}`}>
          <thead>
            <tr>
              {onSelectionChange && (
                <th style={{ width: 40 }} className={!expandRow ? 'is-sticky-start' : ''}>
                  <input type="checkbox" checked={!!allSelected} onChange={handleSelectAll} />
                </th>
              )}
              {expandRow && <th style={{ width: 40 }} className={onSelectionChange ? '' : 'is-sticky-start'} />}
              {columns.map((column) => {
                const stickyCls = column.sticky === 'start' ? 'is-sticky-start' : column.sticky === 'end' ? 'is-sticky-end' : ''
                return (
                <th
                  key={column.key}
                  style={{ width: column.width, textAlign: column.align || 'left' }}
                  className={`${stickyCls} ${column.className || ''}`.trim()}
                  onClick={column.sortable ? () => handleSort(column.key) : undefined}
                >
                  <span className="sortable-col-header">
                    {column.title}
                    {column.sortable && (
                      <span className="sort-icon">
                        {sortKey === column.key ? (
                          sortDir === 'asc' ? <ChevronUp size={14} /> : <ChevronDown size={14} />
                        ) : (
                          <ChevronsUpDown size={14} />
                        )}
                      </span>
                    )}
                  </span>
                </th>
              )})}
            </tr>
          </thead>
          <tbody>
            {paginated.map((row, index) => {
              const key = rowKey(row, index)
              const isSelected = selectedKeys?.includes(key)
              const isExpanded = expandedKeys?.includes(key)
              const rowCls = [
                isSelected ? 'row-selected' : '',
                onRowClick ? 'row-clickable' : '',
              ].filter(Boolean).join(' ')
              const handleRowClick = (e: MouseEvent<HTMLTableRowElement>) => {
                if (!onRowClick) return
                const target = e.target as HTMLElement
                if (target.closest('button, a, input, [data-stop-row-click]')) return
                onRowClick(row, index)
              }
              return (
                <>
                  <tr key={key} className={rowCls} onClick={handleRowClick}>
                    {onSelectionChange && (
                      <td data-label="选择" className={!expandRow ? 'is-sticky-start' : ''}>
                        <input
                          type="checkbox"
                          checked={!!isSelected}
                          onChange={() => handleSelectRow(key)}
                        />
                      </td>
                    )}
                    {expandRow && (
                      <td data-label="展开" className={onSelectionChange ? '' : 'is-sticky-start'}>
                        <button
                          className={`expand-toggle-btn${isExpanded ? ' is-open' : ''}`}
                          onClick={() => handleToggleExpand(key)}
                          aria-label={isExpanded ? '折叠' : '展开'}
                        >
                          {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        </button>
                      </td>
                    )}
                    {columns.map((column) => {
                      const stickyCls = column.sticky === 'start' ? 'is-sticky-start' : column.sticky === 'end' ? 'is-sticky-end' : ''
                      const tdCls = `${stickyCls} ${column.cellClassName || column.className || ''}`.trim()
                      return (
                        <td key={column.key} style={{ textAlign: column.align || 'left' }} className={tdCls} data-label={column.label || (typeof column.title === 'string' ? column.title : column.key)}>
                          {column.render
                            ? column.render(row, index)
                            : column.getValue
                              ? column.getValue(row)
                              : '-'}
                        </td>
                      )
                    })}
                  </tr>
                  {expandRow && isExpanded && (
                    <tr key={`${key}-expand`} className="row-expanded">
                      <td colSpan={columns.length + (onSelectionChange ? 1 : 0) + 1}>
                        {expandRow(row)}
                      </td>
                    </tr>
                  )}
                </>
              )
            })}
          </tbody>
        </table>
      </div>
      {pageSize && pageSize > 0 && totalCount !== undefined && totalCount > 0 && (
        <div className="table-pagination">
          <span className="table-pagination-info">
            共 {totalCount} 条，第 {currentPage}/{totalPages} 页
          </span>
          <div className="table-pagination-actions">
            <label className="table-pagination-size">
              每页
              <select
                value={pageSize}
                onChange={(e) => {
                  const newSize = Number(e.target.value)
                  // 调整页码确保不超出范围
                  const newTotalPages = Math.ceil(totalCount / newSize)
                  const newPage = Math.min(currentPage, newTotalPages)
                  onPageChange?.(newPage)
                  onPageSizeChange?.(newSize)
                }}
              >
                {[10, 20, 50, 100, 200].map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              条
            </label>
            <button
              className="btn btn-subtle"
              disabled={currentPage <= 1}
              onClick={() => onPageChange?.(currentPage - 1)}
            >
              <ChevronLeft size={14} />
              上一页
            </button>
            <button
              className="btn btn-subtle"
              disabled={currentPage >= totalPages}
              onClick={() => onPageChange?.(currentPage + 1)}
            >
              下一页
              <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}