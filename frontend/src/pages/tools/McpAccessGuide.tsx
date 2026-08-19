import { CopyButton } from '../../components/ui'
import { ReactNode, useEffect, useMemo, useState } from 'react'

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

type GuideExample = {
  title: string
  language: string
  content: string
}

function normalizeName(name?: string) {
  return String(name || '').trim().toLowerCase()
}

function getServerKey(server?: McpServerConfig) {
  return normalizeName(server?.name || server?.transport || server?.type)
}

function prettyJson(value: any) {
  return JSON.stringify(value, null, 2)
}

function getTransportLabel(server?: McpServerConfig) {
  const key = getServerKey(server)
  if (key.includes('json')) return 'json-rpc over http'
  if (key.includes('tool') || key.includes('direct')) return 'http tool api'
  if (key.includes('mcp') || key.includes('stream')) return 'streamable http'
  return server?.transport || server?.type || 'http'
}

function getConnectionUrl(server: McpServerConfig | undefined, baseUrl?: string) {
  if (!server) return ''
  const key = getServerKey(server)
  if (server.url) return server.url
  if (key.includes('tool') || key.includes('direct')) return `${baseUrl || 'http://127.0.0.1:8000'}/api/v2/tools/call`
  return `${baseUrl || 'http://127.0.0.1:8000'}/api/v2/mcp`
}

function buildExamples(server: McpServerConfig | undefined, baseUrl?: string): GuideExample[] {
  if (!server) return []
  const apiBase = baseUrl || 'http://127.0.0.1:8000'
  const key = getServerKey(server)
  const mcpUrl = `${apiBase}/api/v2/mcp`
  const toolsCallUrl = `${apiBase}/api/v2/tools/call`

  if (key.includes('json')) {
    return [
      {
        title: 'JSON-RPC initialize',
        language: 'bash',
        content: `curl -X POST "${mcpUrl}" \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer <填入 Tool Token>" \\\n  -d '${prettyJson({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'ops-client', version: '1.0.0' } } })}'`,
      },
      {
        title: 'JSON-RPC tools/list',
        language: 'bash',
        content: `curl -X POST "${mcpUrl}" \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer <填入 Tool Token>" \\\n  -d '${prettyJson({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} })}'`,
      },
      {
        title: 'JSON-RPC tools/call',
        language: 'bash',
        content: `curl -X POST "${mcpUrl}" \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer <填入 Tool Token>" \\\n  -d '${prettyJson({ jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: 'ops_describe_capabilities', arguments: { include_schema: false, limit: 20 } } })}'`,
      },
    ]
  }

  if (key.includes('mcp') || key.includes('stream')) {
    return [
      {
        title: 'MCP Streamable HTTP 配置',
        language: 'json',
        content: prettyJson({
          mcpServers: {
            'ops-ai-http': {
              type: 'streamable-http',
              url: mcpUrl,
              headers: {
                Authorization: 'Bearer <填入 Tool Token>',
              },
            },
          },
        }),
      },
      {
        title: 'tools/list 测试',
        language: 'bash',
        content: `curl -X POST "${mcpUrl}" \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer <填入 Tool Token>" \\\n  -d '${prettyJson({ jsonrpc: '2.0', id: 1, method: 'tools/list', params: {} })}'`,
      },
    ]
  }

  return [
    {
      title: 'HTTP Tool Call 示例',
      language: 'bash',
      content: `curl -X POST "${toolsCallUrl}" \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer <填入 Tool Token>" \\\n  -d '${prettyJson({ tool: 'ops.describe_capabilities', arguments: { include_schema: false, limit: 20 } })}'`,
    },
    {
      title: 'HTTP Tool Catalog',
      language: 'bash',
      content: `curl "${apiBase}/api/v2/tools?format=mcp" \\\n  -H "Authorization: Bearer <填入 Tool Token>"`,
    },
  ]
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
  const [internalSelected, setInternalSelected] = useState<string>(selectedServer || servers[0]?.name || '')

  useEffect(() => {
    if (selectedServer) {
      setInternalSelected(selectedServer)
      return
    }
    if (!servers.some((s) => s.name === internalSelected)) {
      setInternalSelected(servers[0]?.name || '')
    }
  }, [selectedServer, servers, internalSelected])

  const activeName = selectedServer || internalSelected || servers[0]?.name
  const server = servers.find((s) => s.name === activeName) || servers[0]
  const connectionUrl = getConnectionUrl(server, baseUrl)
  const transportLabel = getTransportLabel(server)
  const examples = useMemo(() => buildExamples(server, baseUrl), [server, baseUrl])

  const select = (name: string) => {
    setInternalSelected(name)
    onSelectServer?.(name)
  }

  return (
    <div className="card" style={{ display: 'grid', gap: 14 }}>
      <div className="card-header">
        <div>
          <h2>MCP 接入指南</h2>
          <span>{servers.length} 种接入方式，点击上方标签切换配置示例</span>
        </div>
      </div>
      {servers.length > 1 && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {servers.map((s) => {
            const active = s.name === server?.name
            return (
              <button
                key={s.name}
                type="button"
                className={`btn ${active ? 'btn-primary' : 'btn-subtle'}`}
                onClick={() => select(s.name)}
                aria-pressed={active}
              >
                {s.name}
              </button>
            )
          })}
        </div>
      )}
      {server && (
        <div style={{ display: 'grid', gap: 12 }}>
          <div className="form-compact" style={{ display: 'grid', gap: 10 }}>
            <div className="field-item">
              <label>接入方式</label>
              <div className="input-with-copy">
                <input readOnly value={server.name} />
                <CopyButton text={server.name} />
              </div>
            </div>
            <div className="field-item">
              <label>传输类型</label>
              <div className="input-with-copy">
                <input readOnly value={transportLabel} />
                <CopyButton text={transportLabel} />
              </div>
            </div>
            {server.command && (
              <div className="field-item">
                <label>启动命令</label>
                <div className="input-with-copy">
                  <input readOnly value={`${server.command} ${(server.args || []).join(' ')}`.trim()} />
                  <CopyButton text={`${server.command} ${(server.args || []).join(' ')}`.trim()} />
                </div>
              </div>
            )}
            <div className="field-item">
              <label>连接地址</label>
              <div className="input-with-copy">
                <input readOnly value={connectionUrl} />
                <CopyButton text={connectionUrl} />
              </div>
            </div>
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

          <div className="endpoint-list endpoint-list--canonical" style={{ gap: 10 }}>
            {examples.map((example) => (
              <div className="endpoint-card" key={example.title}>
                <div className="endpoint-card-header">
                  <strong>{example.title}</strong>
                  <span className="endpoint-method">{example.language}</span>
                </div>
                <pre className="code-block small" style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{example.content}</pre>
                <div style={{ marginTop: 8 }}>
                  <CopyButton text={example.content} />
                </div>
              </div>
            ))}
          </div>
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
