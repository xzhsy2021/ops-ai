import { useState } from 'react'
import { useToolPlayground } from './useToolPlayground'

export function ToolPlaygroundPanel({
  defaultTool,
  defaultArgs,
  onExecute,
}: {
  defaultTool?: string
  defaultArgs?: string
  onExecute?: (tool: string, args: string) => Promise<{ result: string; error?: string; duration_ms?: number }>
}) {
  const [tool, setTool] = useState(defaultTool || '')
  const [args, setArgs] = useState(defaultArgs || '{}')
  const playground = useToolPlayground()

  const handleExecute = async () => {
    const res = onExecute
      ? await onExecute(tool.trim(), args)
      : await playground.execute(tool.trim(), args)
    return res
  }

  const executing = playground.executing
  const output = playground.result
  const error = playground.error
  const duration = playground.duration_ms

  return (
    <div className="card" style={{ display: 'grid', gap: 12 }}>
      <div className="card-header">
        <div>
          <h2>Playground</h2>
          <span>调试工具调用</span>
        </div>
        <button
          className="btn btn-primary"
          disabled={!tool.trim() || executing}
          onClick={handleExecute}
        >
          {executing ? '执行中...' : '执行'}
        </button>
      </div>
      <div className="form-compact" style={{ display: 'grid', gap: 8 }}>
        <div className="field-item">
          <label>工具名称</label>
          <input
            value={tool}
            onChange={(e) => setTool(e.target.value)}
            placeholder="输入工具名称，例如：deploy_web_app"
          />
        </div>
        <div className="field-item">
          <label>参数 JSON</label>
          <textarea
            rows={6}
            value={args}
            onChange={(e) => setArgs(e.target.value)}
            placeholder='{"param1": "value1", "param2": "value2"}'
            style={{ fontFamily: "'Consolas', 'Monaco', ui-monospace, monospace" }}
          />
        </div>
      </div>
      {(output || error || duration != null) && (
        <div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
            <strong style={{ color: 'var(--text-primary)', fontSize: 13 }}>执行结果</strong>
            {duration != null && <small style={{ color: 'var(--text-muted)' }}>{duration}ms</small>}
          </div>
          {error && (
            <div className="alert alert-danger" style={{ marginBottom: 8 }}>
              <strong>错误：</strong>{error}
            </div>
          )}
          {output && (
            <pre className="code-block" style={{ maxHeight: 360 }}>
              {output}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}