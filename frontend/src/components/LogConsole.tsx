import { useState, useMemo, useCallback, useRef, useEffect, memo } from 'react'
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

const LEVEL_LABEL: Record<string, string> = {
  debug: 'DBG',
  info: 'INF',
  warning: 'WRN',
  warn: 'WRN',
  error: 'ERR',
  success: 'OK ',
}

/** 单行日志 — memo 化避免大列表重渲染（计划 §5.3.2） */
const LogLine = memo(function LogLine({ entry }: { entry: LogConsoleEntry }) {
  const t = entry.created_at || entry.time || ''
  const time = t.length >= 19 ? t.slice(11, 19) : t
  const source = entry.step_name || entry.server_name || '-'
  const level = (entry.level || 'info').toLowerCase()
  return (
    <div className={`log-console-line log-console-line--${level}`}>
      <span className="log-console-line-time">{time || '--:--:--'}</span>
      <span className="log-console-line-level">{LEVEL_LABEL[level] || 'INF'}</span>
      <span className="log-console-line-source" title={source}>{source}</span>
      <span className="log-console-line-message">{entry.message || ''}</span>
    </div>
  )
})

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
  const [onlyErrors, setOnlyErrors] = useState(false)
  const bodyRef = useRef<HTMLDivElement>(null)
  // 用户是否手动上滚 — 自动滚动应让位
  const userScrolledUpRef = useRef(false)

  const debouncedKeyword = useDebouncedValue(keyword, 250)

  const serverOptions = useMemo(() => {
    const names = new Set<string>()
    entries.forEach((e) => { if (e.server_name) names.add(e.server_name) })
    return Array.from(names).sort()
  }, [entries])

  const stepOptions = useMemo(() => {
    const names = new Set<string>()
    entries.forEach((e) => { if (e.step_name) names.add(e.step_name) })
    return Array.from(names).sort()
  }, [entries])

  const filtered = useMemo(() => {
    const q = debouncedKeyword.trim().toLowerCase()
    let items = entries
    if (onlyErrors) {
      items = items.filter((e) => (e.level || '').toLowerCase() === 'error')
    } else if (level !== 'all') {
      items = items.filter((e) => {
        const l = (e.level || 'info').toLowerCase()
        return l === level || (level === 'warn' && l === 'warning')
      })
    }
    if (server !== 'all') items = items.filter((e) => e.server_name === server)
    if (step !== 'all') items = items.filter((e) => e.step_name === step)
    if (q) {
      items = items.filter((e) => {
        const text = `${e.created_at || e.time || ''} ${e.step_name || ''} ${e.server_name || ''} ${e.level || ''} ${e.message || ''}`.toLowerCase()
        return text.includes(q)
      })
    }
    return items
  }, [entries, level, server, step, debouncedKeyword, onlyErrors])

  const displayEntries = useMemo(() => filtered.slice(-maxRows), [filtered, maxRows])
  const truncated = filtered.length > maxRows

  // 错误计数 — 用于只看错误徽标
  const errorCount = useMemo(
    () => entries.filter((e) => (e.level || '').toLowerCase() === 'error').length,
    [entries],
  )

  // 自动滚动到底部（计划 §5.3.1）— 暂停或用户上滚时让位
  useEffect(() => {
    if (!autoScroll || paused) return
    if (userScrolledUpRef.current) return
    const el = bodyRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [displayEntries, autoScroll, paused])

  // 检测用户手动上滚
  const handleScroll = useCallback(() => {
    const el = bodyRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24
    userScrolledUpRef.current = !atBottom
  }, [])

  // 新日志到达时若用户已在底部，重置标志让自动滚动继续
  useEffect(() => {
    if (!userScrolledUpRef.current) return
    const el = bodyRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24
    if (atBottom) userScrolledUpRef.current = false
  }, [displayEntries])

  const copyErrors = useCallback(() => {
    const candidates = entries.filter((entry) =>
      `${entry.level || ''} ${entry.message || ''}`.toLowerCase().match(/error|失败|异常/),
    )
    const content = (candidates.length ? candidates : entries)
      .slice(-80)
      .map((entry) =>
        `[${(entry.created_at || entry.time || '').slice(11, 19) || '-'}] [${entry.step_name || entry.server_name || '-'}] ${entry.level || 'info'}: ${entry.message || ''}`,
      )
      .join('\n')
    void navigator.clipboard?.writeText(content)
  }, [entries])

  const copyAll = useCallback(() => {
    const content = entries
      .map((entry) =>
        `[${(entry.created_at || entry.time || '').slice(11, 19) || '-'}] [${entry.step_name || entry.server_name || '-'}] ${entry.level || 'info'}: ${entry.message || ''}`,
      )
      .join('\n')
    void navigator.clipboard?.writeText(content)
  }, [entries])

  const jumpToFirstError = useCallback(() => {
    const first = entries.find((e) => (e.level || '').toLowerCase() === 'error')
    if (first) setKeyword(first.message || '')
  }, [entries])

  return (
    <div className="log-console">
      <div className="log-console-toolbar">
        <div className="log-console-toolbar-left">
          <span className="log-console-led" aria-hidden="true" data-paused={paused || !autoScroll} />
          <strong>{title}</strong>
          <span className="log-console-count">
            {entries.length} 行
            {displayEntries.length !== entries.length && <> · 显示 {displayEntries.length}</>}
          </span>
          {errorCount > 0 && (
            <span className="log-console-error-badge">{errorCount} 错误</span>
          )}
        </div>
        <div className="log-console-toolbar-right">
          <input
            className="log-console-search"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索日志..."
          />
          <button
            type="button"
            className={`log-console-toggle${onlyErrors ? ' is-active' : ''}`}
            onClick={() => setOnlyErrors((v) => !v)}
            title="只看错误级别日志"
          >
            只看错误
          </button>
          {!onlyErrors && (
            <select value={level} onChange={(e) => setLevel(e.target.value)}>
              <option value="all">全部级别</option>
              <option value="error">错误</option>
              <option value="warn">警告</option>
              <option value="info">信息</option>
              <option value="debug">调试</option>
            </select>
          )}
          {serverOptions.length > 1 && (
            <select value={server} onChange={(e) => setServer(e.target.value)}>
              <option value="all">全部服务器</option>
              {serverOptions.map((s) => (<option key={s} value={s}>{s}</option>))}
            </select>
          )}
          {stepOptions.length > 1 && (
            <select value={step} onChange={(e) => setStep(e.target.value)}>
              <option value="all">全部步骤</option>
              {stepOptions.map((s) => (<option key={s} value={s}>{s}</option>))}
            </select>
          )}
          <label className="log-console-check">
            <input type="checkbox" checked={paused} onChange={(e) => setPaused(e.target.checked)} />
            <span>暂停</span>
          </label>
          <label className="log-console-check">
            <input type="checkbox" checked={autoScroll} onChange={(e) => { setAutoScroll(e.target.checked); userScrolledUpRef.current = false }} />
            <span>自动滚动</span>
          </label>
          <button className="btn btn-subtle" type="button" onClick={copyErrors}>复制错误</button>
          <button className="btn btn-subtle" type="button" onClick={copyAll}>复制全部</button>
          <button className="btn btn-subtle" type="button" onClick={jumpToFirstError}>首条错误</button>
          {onDownload && (<button className="btn btn-subtle" type="button" onClick={onDownload}>下载</button>)}
          {onRefresh && (<button className="btn btn-subtle" type="button" onClick={onRefresh}>刷新</button>)}
        </div>
      </div>
      <div
        className="log-console-body"
        ref={bodyRef}
        style={{ maxHeight }}
        onScroll={handleScroll}
      >
        {loading && entries.length === 0 && (
          <div className="log-console-empty">加载中...</div>
        )}
        {!loading && entries.length === 0 && (
          <div className="log-console-empty">暂无日志</div>
        )}
        {entries.length > 0 && displayEntries.length === 0 && (
          <div className="log-console-empty">没有匹配当前筛选条件的日志</div>
        )}
        {hasMoreBefore && onLoadMoreBefore && (
          <button
            className="log-console-loadmore"
            type="button"
            onClick={onLoadMoreBefore}
          >
            加载更早的日志...
          </button>
        )}
        {displayEntries.map((entry, index) => (
          <LogLine key={entry.id || index} entry={entry} />
        ))}
        {truncated && (
          <div className="log-console-truncate-hint">
            已隐藏 {filtered.length - maxRows} 行较早日志。
            {onDownload && <button className="btn btn-subtle" type="button" onClick={onDownload}>下载完整日志</button>}
          </div>
        )}
      </div>
    </div>
  )
}
