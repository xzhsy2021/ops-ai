import { useState, useEffect, useRef } from 'react'

interface KeyValuePair {
  key: string
  value: string
  id: number
}

interface KeyValueEditorProps {
  value: Record<string, any>
  onChange: (obj: Record<string, any>) => void
  keyPlaceholder?: string
  valuePlaceholder?: string
  addButtonText?: string
  emptyText?: string
}

let _idCounter = 0

function objectToPairs(obj: Record<string, any>): KeyValuePair[] {
  return Object.entries(obj || {}).map(([k, v]) => ({
    key: k,
    value: typeof v === 'string' ? v : JSON.stringify(v),
    id: ++_idCounter,
  }))
}

function pairsToObject(pairs: KeyValuePair[]): Record<string, any> {
  const result: Record<string, any> = {}
  for (const p of pairs) {
    const k = p.key.trim()
    if (!k) continue
    const v = p.value.trim()
    try {
      result[k] = JSON.parse(v)
    } catch {
      result[k] = v
    }
  }
  return result
}

export default function KeyValueEditor({
  value,
  onChange,
  keyPlaceholder = '键',
  valuePlaceholder = '值',
  addButtonText = '+ 添加',
  emptyText = '暂无条目',
}: KeyValueEditorProps) {
  const [pairs, setPairs] = useState<KeyValuePair[]>(() => objectToPairs(value))
  const internalChange = useRef(false)
  const prevValueRef = useRef(value)

  useEffect(() => {
    if (internalChange.current) {
      internalChange.current = false
      return
    }
    if (value !== prevValueRef.current) {
      setPairs(objectToPairs(value))
    }
    prevValueRef.current = value
  }, [value])

  const emitChange = (next: KeyValuePair[]) => {
    internalChange.current = true
    prevValueRef.current = pairsToObject(next)
    onChange(pairsToObject(next))
  }

  const addPair = () => {
    const next = [...pairs, { key: '', value: '', id: ++_idCounter }]
    setPairs(next)
  }

  const removePair = (id: number) => {
    const next = pairs.filter((p) => p.id !== id)
    setPairs(next)
    emitChange(next)
  }

  const updatePair = (id: number, field: 'key' | 'value', val: string) => {
    const next = pairs.map((p) => (p.id === id ? { ...p, [field]: val } : p))
    setPairs(next)
    if (next.every((p) => p.key.trim())) {
      emitChange(next)
    }
  }

  const inputStyle: React.CSSProperties = {
    padding: '6px 10px',
    background: 'var(--bg-page)',
    border: '1px solid var(--border-strong)',
    borderRadius: '6px',
    color: 'var(--text-primary)',
    fontSize: '13px',
    width: '100%',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {pairs.length === 0 && (
        <div style={{ color: 'var(--text-muted)', fontSize: '13px', padding: '8px 0' }}>{emptyText}</div>
      )}
      {pairs.map((p) => (
        <div key={p.id} style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <input
            style={{ ...inputStyle, flex: '0 0 35%' }}
            value={p.key}
            onChange={(e) => updatePair(p.id, 'key', e.target.value)}
            placeholder={keyPlaceholder}
          />
          <span style={{ color: 'var(--text-muted)', fontSize: '13px' }}>:</span>
          <input
            style={{ ...inputStyle, flex: '1 1 auto' }}
            value={p.value}
            onChange={(e) => updatePair(p.id, 'value', e.target.value)}
            placeholder={valuePlaceholder}
          />
          <button
            className="btn"
            onClick={() => removePair(p.id)}
            style={{
              padding: '4px 10px',
              fontSize: '12px',
              background: 'var(--danger-surface)',
              color: 'var(--danger)',
              flexShrink: 0,
            }}
          >
            删除
          </button>
        </div>
      ))}
      <button
        className="btn"
        onClick={addPair}
        style={{
          padding: '6px 14px',
          fontSize: '13px',
          background: 'var(--action-bg)',
          color: 'var(--action-text)',
          alignSelf: 'flex-start',
        }}
      >
        {addButtonText}
      </button>
    </div>
  )
}
