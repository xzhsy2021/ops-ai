import { useState } from 'react'
import { Database, HardDrive, Timer, Zap } from 'lucide-react'
import type { ReactNode } from 'react'

type ConnectionPoolStats = {
  active?: number
  idle?: number
  max?: number
  waiting?: number
}

export function DatabaseConnectionPool({
  stats,
  actions,
}: {
  stats: ConnectionPoolStats
  actions?: ReactNode
}) {
  const total = (stats.active || 0) + (stats.idle || 0)
  const pct = stats.max ? Math.min(100, Math.round((total / stats.max) * 100)) : 0

  return (
    <div className="card" style={{ display: 'grid', gap: 10 }}>
      <div className="card-header">
        <h3>
          <Database size={16} style={{ marginRight: 6 }} />
          连接池
        </h3>
        {actions}
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        <div className="health-bar-label">
          <span>使用率</span>
          <span>{total} / {stats.max || '?'}</span>
        </div>
        <div className="health-bar-track">
          <div
            className={`health-bar-fill health-bar-fill--${pct > 80 ? 'danger' : pct > 60 ? 'warning' : 'normal'}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
      <div style={{ display: 'grid', gap: 4, fontSize: 13, color: 'var(--text-secondary)' }}>
        <div>活跃：<strong style={{ color: 'var(--cyan)' }}>{stats.active || 0}</strong></div>
        <div>空闲：<strong style={{ color: 'var(--success)' }}>{stats.idle || 0}</strong></div>
        {stats.waiting != null && <div>等待：<strong>{stats.waiting}</strong></div>}
      </div>
    </div>
  )
}

export function DatabaseQueryConsole({
  defaultSql,
  onExecute,
  databases,
}: {
  defaultSql?: string
  onExecute: (sql: string, database?: string) => Promise<{ columns: string[]; rows: any[][]; duration_ms?: number; error?: string }>
  databases?: string[]
}) {
  const [sql, setSql] = useState(defaultSql || 'SELECT 1')
  const [database, setDatabase] = useState(databases?.[0] || '')
  const [result, setResult] = useState<{ columns: string[]; rows: any[][] } | null>(null)
  const [error, setError] = useState('')
  const [duration, setDuration] = useState<number | null>(null)
  const [executing, setExecuting] = useState(false)

  const handleExecute = async () => {
    setExecuting(true)
    setError('')
    setResult(null)
    setDuration(null)
    try {
      const res = await onExecute(sql.trim(), database || undefined)
      if (res.error) {
        setError(res.error)
      } else {
        setResult({ columns: res.columns, rows: res.rows })
      }
      setDuration(res.duration_ms ?? null)
    } catch (err: any) {
      setError(err?.message || '执行失败')
    } finally {
      setExecuting(false)
    }
  }

  return (
    <div className="card" style={{ display: 'grid', gap: 10 }}>
      <div className="card-header">
        <h3>
          <Zap size={16} style={{ marginRight: 6 }} />
          查询控制台
        </h3>
        <button className="btn btn-primary" disabled={!sql.trim() || executing} onClick={handleExecute}>
          {executing ? '执行中...' : '执行'}
        </button>
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        {databases && databases.length > 0 && (
          <select value={database} onChange={(e) => setDatabase(e.target.value)}>
            {databases.map((db) => (
              <option key={db} value={db}>{db}</option>
            ))}
          </select>
        )}
      </div>
      <textarea
        rows={4}
        value={sql}
        onChange={(e) => setSql(e.target.value)}
        style={{ fontFamily: "'Consolas', 'Monaco', ui-monospace, monospace" }}
        placeholder="输入 SQL 语句..."
      />
      {duration != null && (
        <small style={{ color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <Timer size={12} />
          {duration}ms
        </small>
      )}
      {error && <div className="alert alert-danger">{error}</div>}
      {result && (
        <div className="table-scroll">
          <table className="data-table data-table--compact">
            <thead>
              <tr>
                {result.columns.map((col) => (
                  <th key={col}>{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.length === 0 && (
                <tr>
                  <td colSpan={result.columns.length} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
                    查询无结果
                  </td>
                </tr>
              )}
              {result.rows.map((row, ri) => (
                <tr key={ri}>
                  {row.map((cell, ci) => (
                    <td key={ci}>{cell === null ? <em style={{ color: 'var(--text-muted)' }}>NULL</em> : String(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export function DatabaseExportPanel({
  tables,
  onExport,
}: {
  tables: string[]
  onExport: (table: string, format: 'csv' | 'json' | 'sql') => Promise<void>
}) {
  const [selected, setSelected] = useState('')
  const [format, setFormat] = useState<'csv' | 'json' | 'sql'>('csv')
  const [exporting, setExporting] = useState(false)

  const handleExport = async () => {
    if (!selected.trim()) return
    setExporting(true)
    try {
      await onExport(selected.trim(), format)
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="card" style={{ display: 'grid', gap: 10 }}>
      <div className="card-header">
        <h3>
          <HardDrive size={16} style={{ marginRight: 6 }} />
          数据导出
        </h3>
        <button className="btn btn-primary" disabled={!selected.trim() || exporting} onClick={handleExport}>
          {exporting ? '导出中...' : '导出'}
        </button>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <select value={selected} onChange={(e) => setSelected(e.target.value)}>
          <option value="">选择要导出的表...</option>
          {tables.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <select value={format} onChange={(e) => setFormat(e.target.value as any)}>
          <option value="csv">CSV</option>
          <option value="json">JSON</option>
          <option value="sql">SQL</option>
        </select>
      </div>
    </div>
  )
}