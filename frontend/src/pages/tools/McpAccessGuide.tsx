import { CopyButton } from '../../components/ui'
import { ReactNode } from 'react'

type McpServerConfig = {
  name: string
  transport?: string
  type?: string
  command?: string
  args?: string[]
  env?: string
  url?: string
  port?: number
  description?: string
}

export function McpAccessGuide({
  servers,
  baseUrl,
  selectedServer,
  onSelectServer,
  extra,
}: {
  servers: McpServerConfig[]
  baseUrl?: string
  selectedServer?: string
  onSelectServer?: (name: string) => void
  extra?: ReactNode
}) {
  const server = servers.find((s) => s.name === selectedServer) || servers[0]

  return (
    <div className="card" style={{ display: 'grid', gap: 12 }}>
      <div className="card-header">
        <div>
          <h2>MCP 接入指南</h2>
          <span>{servers.length} 个服务器</span>
        </div>
      </div>
      {servers.length > 1 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {servers.map((s) => (
            <button
              key={s.name}
              className={`chip ${selectedServer === s.name ? 'chip--active' : ''}`}
              onClick={() => onSelectServer?.(s.name)}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}
      {server && (
        <div style={{ display: 'grid', gap: 10 }}>
          <div className="form-compact" style={{ display: 'grid', gap: 8 }}>
            <div className="field-item">
              <label>服务器名称</label>
              <div className="input-with-copy">
                <input readOnly value={server.name} />
                <CopyButton text={server.name} />
              </div>
            </div>
            <div className="field-item">
              <label>传输类型</label>
              <div className="input-with-copy">
                <input readOnly value={server.transport || server.type || '-'} />
              </div>
            </div>
            {server.command && (
              <div className="field-item">
                <label>启动命令</label>
                <div className="input-with-copy">
                  <input readOnly value={`${server.command} ${(server.args || []).join(' ')}`} />
                  <CopyButton text={`${server.command} ${(server.args || []).join(' ')}`} />
                </div>
              </div>
            )}
            {server.url && (
              <div className="field-item">
                <label>连接地址</label>
                <div className="input-with-copy">
                  <input readOnly value={server.url} />
                  <CopyButton text={server.url} />
                </div>
              </div>
            )}
            {server.env && (
              <div className="field-item">
                <label>环境配置</label>
                <pre className="code-block small">{server.env}</pre>
              </div>
            )}
          </div>
          {server.description && (
            <p style={{ color: 'var(--text-secondary)', fontSize: 13, margin: 0 }}>{server.description}</p>
          )}
          {baseUrl && (
            <div className="alert alert-info" style={{ margin: 0 }}>
              <strong>连接地址：</strong>
              <code>{baseUrl}</code>
              <CopyButton text={baseUrl} />
            </div>
          )}
        </div>
      )}
      {!server && (
        <div style={{ padding: 24, color: 'var(--text-muted)', textAlign: 'center' }}>
          暂无 MCP 服务器配置
        </div>
      )}
      {extra}
    </div>
  )
}