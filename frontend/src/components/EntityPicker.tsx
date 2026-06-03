import { useState, useCallback, useEffect, useRef } from 'react'
import { Search, X } from 'lucide-react'
import { useDebouncedValue } from '../hooks/useDebouncedValue'

export type EntityPickerOption = {
  value: string
  label: string
  group?: string
  description?: string
  disabled?: boolean
  disabledReason?: string
  metadata?: Record<string, string>
}

export type EntityPickerProps = {
  options: EntityPickerOption[]
  value?: string | string[]
  onChange?: (value: string | string[]) => void
  multiple?: boolean
  placeholder?: string
  searchPlaceholder?: string
  loading?: boolean
  emptyText?: string
  maxHeight?: number
  showRecent?: boolean
  recentKey?: string
  maxRecent?: number
}

const RECENT_PREFIX = 'ops-recent-entity-'

function loadRecent(key: string, max: number): string[] {
  try {
    const raw = localStorage.getItem(RECENT_PREFIX + key)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.slice(0, max) : []
  } catch {
    return []
  }
}

function saveRecent(key: string, values: string[], max: number) {
  try {
    const deduped = [...new Set(values)].slice(0, max)
    localStorage.setItem(RECENT_PREFIX + key, JSON.stringify(deduped))
  } catch { /* quota exceeded, ignore */ }
}

export function EntityPicker({
  options,
  value = '',
  onChange,
  multiple = false,
  placeholder = '请选择...',
  searchPlaceholder = '搜索...',
  loading = false,
  emptyText = '暂无选项',
  maxHeight = 260,
  showRecent = false,
  recentKey = '',
  maxRecent = 5,
}: EntityPickerProps) {
  const [keyword, setKeyword] = useState('')
  const [open, setOpen] = useState(false)
  const debouncedKeyword = useDebouncedValue(keyword, 200)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    window.addEventListener('mousedown', handler)
    return () => window.removeEventListener('mousedown', handler)
  }, [])

  const filtered = options.filter((opt) => {
    if (!debouncedKeyword.trim()) return true
    const q = debouncedKeyword.trim().toLowerCase()
    return `${opt.label} ${opt.group || ''} ${opt.description || ''}`.toLowerCase().includes(q)
  })

  const recentItems = showRecent && recentKey ? loadRecent(recentKey, maxRecent) : []
  const recentOptions = recentItems
    .map((val) => options.find((o) => o.value === val))
    .filter(Boolean) as EntityPickerOption[]

  const selectedValues = Array.isArray(value) ? value : value ? [value] : []

  const handleSelect = useCallback(
    (val: string) => {
      if (multiple) {
        const next = selectedValues.includes(val)
          ? selectedValues.filter((v) => v !== val)
          : [...selectedValues, val]
        onChange?.(next)
      } else {
        onChange?.(val)
        setOpen(false)
      }
      if (recentKey) {
        const existing = loadRecent(recentKey, maxRecent)
        saveRecent(recentKey, [val, ...existing.filter((v) => v !== val)], maxRecent)
      }
    },
    [multiple, selectedValues, onChange, recentKey, maxRecent]
  )

  const groups = new Map<string, EntityPickerOption[]>()
  for (const opt of filtered) {
    const g = opt.group || '__default__'
    if (!groups.has(g)) groups.set(g, [])
    groups.get(g)!.push(opt)
  }

  const selectedLabel = multiple
    ? selectedValues.length
      ? `已选 ${selectedValues.length} 项`
      : placeholder
    : options.find((o) => o.value === value)?.label || placeholder

  return (
    <div className="entity-picker" ref={containerRef}>
      <button
        className="entity-picker-trigger"
        type="button"
        onClick={() => setOpen(!open)}
      >
        <span className={value ? '' : 'entity-picker-placeholder'}>{selectedLabel}</span>
        <X
          size={14}
          className="entity-picker-clear"
          onClick={(e) => {
            e.stopPropagation()
            onChange?.(multiple ? [] : '')
          }}
        />
      </button>
      {open && (
        <div className="entity-picker-dropdown" style={{ maxHeight }}>
          <div className="entity-picker-search">
            <Search size={14} />
            <input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder={searchPlaceholder}
              autoFocus
            />
          </div>
          <div className="entity-picker-list">
            {loading && (
              <div className="entity-picker-empty">加载中...</div>
            )}
            {!loading && filtered.length === 0 && (
              <div className="entity-picker-empty">{emptyText}</div>
            )}
            {!loading && recentOptions.length > 0 && debouncedKeyword.trim() === '' && (
              <div className="entity-picker-group">
                <span className="entity-picker-group-label">最近使用</span>
                {recentOptions.map((opt) => (
                  <button
                    key={opt.value}
                    className={`entity-picker-option${selectedValues.includes(opt.value) ? ' entity-picker-option--selected' : ''}`}
                    onClick={() => handleSelect(opt.value)}
                    disabled={opt.disabled}
                    type="button"
                  >
                    <span>{opt.label}</span>
                  </button>
                ))}
              </div>
            )}
            {Array.from(groups.entries()).map(([group, opts]) => (
              <div key={group} className="entity-picker-group">
                {group !== '__default__' && (
                  <span className="entity-picker-group-label">{group}</span>
                )}
                {opts.map((opt) => (
                  <button
                    key={opt.value}
                    className={`entity-picker-option${selectedValues.includes(opt.value) ? ' entity-picker-option--selected' : ''}${opt.disabled ? ' entity-picker-option--disabled' : ''}`}
                    onClick={() => !opt.disabled && handleSelect(opt.value)}
                    disabled={opt.disabled}
                    type="button"
                    title={opt.disabledReason || undefined}
                  >
                    <span>{opt.label}</span>
                    {opt.description && (
                      <small>{opt.description}</small>
                    )}
                  </button>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}