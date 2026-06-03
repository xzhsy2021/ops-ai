import { useState, useMemo, useCallback, useRef } from 'react'
import { useDebouncedValue } from '../hooks/useDebouncedValue'

export type LogConsoleEntry = {
  id?: string
  time?: string
  created_at?: string
  level?: 'debug' | 'info' | 'warning' | 'error' | 'success' | string
  server_name?: string
  step_name?: string
  message?: string
  raw?: unknown
}

export type LogConsoleProps = {
  entries: LogConsoleEntry[]
  title?: string
  maxRows?: number
  maxHeight?: number
  loading?: boolean
  hasMoreBefore?: boolean
  autoScrollDefault?: boolean
  onRefresh?: () => void
  onLoadMoreBefore?: () => void
  onDownload?: () => void
}

export function LogConsole({
  entries,
  title = '日志',
  maxRows = 1000,
  maxHeight = 480,
  loading = false,
  hasMoreBefore = false,
  autoScrollDefault = true,
  onRefresh,
  onLoadMoreBefore,
  onDownload,
}: LogConsoleProps) {
  const [keyword, setKeyword] = useState('')
  const [level, setLevel] = useState('all')
  const [server, setServer] = useState('all')
  const [step, setStep] = useState('all')
  const [autoScroll, setAutoScroll] = useState(autoScrollDefault)
  const [paused, setPaused] = useState(false)
  const bodyRef = useRef<HTMLDivElement>(null)

  const debouncedKeyword = useDebouncedValue(keyword, 250)

  const serverOptions = useMemo(() => {
    const names = new Set<string>()
    entries.forEach((e) => {
      if (e.server_name) names.add(e.server_name)
    })
    return Array.from(names).sort()
  }, [entries])

  const stepOptions = useMemo(() => {
    const names = new Set<string>()
    entries.forEach((e) => {
      if (e.step_name) names.add(e.step_name)
    })
    return Array.from(names).sort()
  }, [entries])

  const filtered = useMemo(() => {
    const q = debouncedKeyword.trim().toLowerCase()
    let items = entries
    if (level !== 'all') {
      items = items.filter((e) => {
        const l = (e.level || 'info').toLowerCase()
        return l === level || (level === 'warn' && l === 'warning')
      })
    }
    if (server !== 'all') {
      items = items.filter((e) => e.server_name === server)
    }
    if (step !== 'all') {
      items = items.filter((e) => e.step_name === step)
    }
    if (q) {
      items = items.filter((e) => {
        const text = `${e.created_at || e.time || ''} ${e.step_name || ''} ${e.server_name || ''} ${e.level || ''} ${e.message || ''}`.toLowerCase()
        return text.includes(q)
      })
    }
    return items
  }, [entries, level, server, step, debouncedKeyword])

  const displayEntries = useMemo(() => {
    const limited = filtered.slice(-maxRows)
    return limited
  }, [filtered, maxRows])

  const truncated = filtered.length > maxRows

  const copyErrors = useCallback(() => {
    const candidates = entries.filter((entry) =>
      `${entry.level || ''} ${entry.message || ''}`.toLowerCase().match(/error|失败|异常/)
    )
    const content = (candidates.length ? candidates : entries)
      .slice(-80)
      .map(
        (entry) =>
          `[${(entry.created_at || entry.time || '').slice(11, 19) || '-'}] [${entry.step_name || entry.server_name || '-'}] ${entry.level || 'info'}: ${entry.message || ''}`
      )
      .join('\n')
    void navigator.clipboard?.writeText(content)
  }, [entries])

  const copyAll = useCallback(() => {
    const content = entries
      .map(
        (entry) =>
          `[${(entry.created_at || entry.time || '').slice(11, 19) || '-'}] [${entry.step_name || entry.server_name || '-'}] ${entry.level || 'info'}: ${entry.message || ''}`
      )
      .join('\n')
    void navigator.clipboard?.writeText(content)
  }, [entries])

  const jumpToFirstError = useCallback(() => {
    const first = entries.find((e) => (e.level || '').toLowerCase() === 'error')
    if (first) {
      setKeyword(first.message || '')
    }
  }, [entries])

  const timeString = (entry: LogConsoleEntry) => {
    const t = entry.created_at || entry.time || ''
    return t.length >= 19 ? t.slice(11, 19) : t
  }

  const sourceString = (entry: LogConsoleEntry) => entry.step_name || entry.server_name || '-'

  return (
    <div className="log-console">
      <div className="log-console-toolbar">
        <div className="log-console-toolbar-left">
          <strong>{title}</strong>
          <span>{entries.length} 行，显示 {displayEntries.length} 行</span>
        </div>
        <div className="log-console-toolbar-right">
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索日志..."
            style={{ width: 160 }}
          />
          <select value={level} onChange={(e) => setLevel(e.target.value)}>
            <option value="all">全部级别</option>
            <option value="error">错误</option>
            <option value="warn">警告</option>
            <option value="info">信息</option>
            <option value="debug">调试</option>
          </select>
          {serverOptions.length > 1 && (
            <select value={server} onChange={(e) => setServer(e.target.value)}>
              <option value="all">全部服务器</option>
              {serverOptions.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          )}
          {stepOptions.length > 1 && (
            <select value={step} onChange={(e) => setStep(e.target.value)}>
              <option value="all">全部步骤</option>
              {stepOptions.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          )}
          <label className="inline-check">
            <input
              type="checkbox"
              checked={paused}
              onChange={(e) => setPaused(e.target.checked)}
            />
            <span>暂停</span>
          </label>
          <label className="inline-check">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
            />
            <span>自动滚动</span>
          </label>
          <button className="btn btn-subtle" type="button" onClick={copyErrors}>
            复制错误上下文
          </button>
          <button className="btn btn-subtle" type="button" onClick={copyAll}>
            复制全部
          </button>
          <button className="btn btn-subtle" type="button" onClick={jumpToFirstError}>
            首条错误
          </button>
          {onDownload && (
            <button className="btn btn-subtle" type="button" onClick={onDownload}>
              下载
            </button>
          )}
          {onRefresh && (
            <button className="btn btn-subtle" type="button" onClick={onRefresh}>
              刷新
            </button>
          )}
        </div>
      </div>
      <div
        className="log-console-body"
        ref={bodyRef}
        style={{ maxHeight }}
      >
        {loading && entries.length === 0 && (
          <div style={{ color: 'var(--text-muted)', padding: 12 }}>
            加载中...
          </div>
        )}
        {!loading && entries.length === 0 && (
          <div style={{ color: 'var(--text-muted)', padding: 12 }}>
            暂无日志
          </div>
        )}
        {entries.length > 0 && displayEntries.length === 0 && (
          <div style={{ color: 'var(--text-muted)', padding: 12 }}>
            没有匹配当前筛选条件的日志
          </div>
        )}
        {hasMoreBefore && onLoadMoreBefore && (
          <button
            className="log-console-truncate-hint"
            onClick={onLoadMoreBefore}
            style={{ width: '100%', cursor: 'pointer', border: 'none', borderBottom: '1px solid var(--border)' }}
          >
            加载更早的日志...
          </button>
        )}
        {displayEntries.map((entry, index) => (
          <div
            key={entry.id || index}
            className={`log-console-line log-console-line--${entry.level || 'info'}`}
          >
            <span className="log-console-line-time">{timeString(entry)}</span>
            <span className="log-console-line-source" title={sourceString(entry)}>
              {sourceString(entry)}
            </span>
            <span className="log-console-line-message">{entry.message || ''}</span>
          </div>
        ))}
        {truncated && (
          <div className="log-console-truncate-hint">
            已隐藏 {filtered.length - maxRows} 行较早日志。
            {onDownload && <button className="btn btn-subtle" type="button" onClick={onDownload} style={{ marginLeft: 8 }}>下载完整日志</button>}
          </div>
        )}
      </div>
    </div>
  )
}