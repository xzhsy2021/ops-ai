import { useEffect, useMemo, useState } from 'react'
import { capabilityTools } from '../api'
import { useAuthStore, useNotificationStore } from '../store'
import { ROUTES } from '../routes'
import { ConfirmDialog, RiskConfirmDialog, Skeleton, StatusBadge, PageHeader, FavoriteButton } from '../components/ui'
import { ToolCatalogPanel } from './tools/ToolCatalogPanel'
import { ToolAuditTimeline } from './tools/ToolAuditTimeline'
import { ToolPlaygroundPanel } from './tools/ToolPlaygroundPanel'
import { ToolOverviewPanel } from './tools/ToolOverviewPanel'
import { ToolRiskPolicyPanel } from './tools/ToolRiskPolicyPanel'
import { ToolTokenPanel } from './tools/ToolTokenPanel'
import { McpAccessGuide } from './tools/McpAccessGuide'
import { ToolDetailDrawer } from './tools/ToolDetailDrawer'
import type { ToolInfo as _ToolInfo } from './tools/ToolCatalogPanel'

type ToolInfo = _ToolInfo

type TabKey = 'overview' | 'tokens' | 'catalog' | 'playground' | 'audit'

const TAB_ITEMS: Array<{ key: TabKey; label: string; hint: string }> = [
  { key: 'overview', label: '概览与接入', hint: '能力开关、端点、Manifest' },
  { key: 'tokens', label: 'Tool Token', hint: '令牌、权限最小化' },
  { key: 'catalog', label: '工具目录', hint: '能力发现、Schema、风险' },
  { key: 'playground', label: '工具调试', hint: '手动调用 HTTP Tool' },
  { key: 'audit', label: '审计与计划', hint: '调用记录、操作计划' },
]

// Frontend fallback for capability switches when the backend has not yet
// returned any value. Mirrors `app/services/tool_policy.py::DEFAULT_CAPABILITY_SETTINGS`.
// Read-side switches default to true (MCP/AI friendly), destructive switches
// default to false (admin must opt in).
const CAPABILITY_DEFAULTS: Record<string, boolean> = {
  enabled: true,
  http_tools_enabled: true,
  mcp_enabled: true,
  read_only: false,
  allow_deploy_plan: true,
  allow_deploy_execute: false,
  allow_prod_deploy: false,
  allow_rollback: false,
  allow_config_write: false,
  allow_backup_write: false,
  allow_backup_restore: false,
  allow_server_read: true,
  allow_server_write: false,
  allow_package_write: false,
  allow_package_cleanup: false,
  allow_runtime_cleanup: false,
  allow_db_read_tools: true,
  allow_db_export_tools: true,
  allow_db_write_tools: false,
  allow_high_risk_tools: true,
  allow_critical_risk_tools: true,
  allow_ai_token_to_run_inspection_execute: true,
  require_confirmation: true,
  taskize_high_risk_tools: true,
  strict_prod_confirmation: true,
  agent_runtime_enabled: false,
}

function getData(res: any) {
  return res?.data ?? res
}

function isRiskyTool(tool?: ToolInfo | null) {
  if (!tool) return false
  const risk = String(tool.risk || '').toLowerCase()
  return Boolean(tool.write || tool.requires_confirmation || ['high', 'critical'].includes(risk))
}

function toMcpAlias(name?: string) {
  return String(name || '').replace(/[^A-Za-z0-9_-]/g, '_').replace(/^_+|_+$/g, '') || 'ops_tool'
}

function buildSampleArgs(tool?: ToolInfo | null) {
  if (!tool) return '{}'
  const props = tool.input_schema?.properties || {}
  const example: Record<string, any> = {}
  Object.keys(props).forEach((key) => {
    const typ = props[key]?.type
    if (typ === 'array') example[key] = []
    else if (typ === 'boolean') example[key] = false
    else if (typ === 'integer' || typ === 'number') example[key] = 0
    else if (typ === 'object') example[key] = {}
    else example[key] = ''
  })
  return JSON.stringify(example, null, 2)
}

function JsonBlock({ value, small = true }: { value: any; small?: boolean }) {
  return <pre className={`code-block ${small ? 'small' : ''}`}>{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre>
}

function ToolRecordPagination({
  total,
  limit,
  offset,
  onOffsetChange,
  onPageSizeChange,
}: {
  total: number
  limit: number
  offset: number
  onOffsetChange: (next: number) => void
  onPageSizeChange: (next: number) => void
}) {
  const safeTotal = Math.max(0, Number(total || 0))
  const safeLimit = Math.max(1, Number(limit || 20))
  const safeOffset = Math.max(0, Number(offset || 0))
  const page = Math.floor(safeOffset / safeLimit) + 1
  const pages = Math.max(1, Math.ceil(safeTotal / safeLimit))

  return (
    <div className="pagination-bar">
      <div className="pagination-info">第 {page}/{pages} 页 · 共 {safeTotal} 条</div>
      <div className="pagination-controls">
        <label className="pagination-size-label">每页
          <select value={safeLimit} onChange={(e) => onPageSizeChange(Number(e.target.value))}>
            {[10, 20, 50].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <button className="pagination-btn" disabled={safeOffset <= 0} onClick={() => onOffsetChange(Math.max(0, safeOffset - safeLimit))}>上一页</button>
        <button className="pagination-btn" disabled={safeOffset + safeLimit >= safeTotal} onClick={() => onOffsetChange(safeOffset + safeLimit)}>下一页</button>
      </div>
    </div>
  )
}

export default function ToolAccessPage() {
  const addMessage = useNotificationStore((s) => s.addMessage)
  const notify = (m: { type: 'success' | 'error' | 'info'; text: string }) => addMessage(m.text, m.type)
  const { user } = useAuthStore()
  const isAdmin = Boolean(user?.is_admin)
  const [loading, setLoading] = useState(false)
  const [tools, setTools] = useState<ToolInfo[]>([])
  const [settings, setSettings] = useState<Record<string, any>>({})
  const [tokens, setTokens] = useState<any[]>([])
  const [tokenTemplates, setTokenTemplates] = useState<any[]>([])
  const [calls, setCalls] = useState<any[]>([])
  const [plans, setPlans] = useState<any[]>([])
  const [manifest, setManifest] = useState<any>(null)
  const [capabilities, setCapabilities] = useState<any>(null)
  const [riskPolicy, setRiskPolicy] = useState<any>(null)
  const [createdToken, setCreatedToken] = useState('')
  const [selectedTool, setSelectedTool] = useState<ToolInfo | null>(null)
  const [toolDetail, setToolDetail] = useState<any>(null)
  const [sampleTool, setSampleTool] = useState('ops.describe_capabilities')
  const [sampleArgs, setSampleArgs] = useState(`{
  "include_schema": false,
  "limit": 20
}`)
  const [sampleResult, setSampleResult] = useState<any>(null)
  const [activeTab, setActiveTab] = useState<TabKey>('overview')
  const [selectedMcpAccess, setSelectedMcpAccess] = useState('HTTP')
  const [riskConfirmOpen, setRiskConfirmOpen] = useState(false)
  const [riskConfirmValue, setRiskConfirmValue] = useState('')
  const [revokeCandidate, setRevokeCandidate] = useState<any>(null)
  const [deleteCandidate, setDeleteCandidate] = useState<any>(null)
  const [callTotal, setCallTotal] = useState(0)
  const [callOffset, setCallOffset] = useState(0)
  const [callPageSize, setCallPageSize] = useState(20)
  const [planTotal, setPlanTotal] = useState(0)
  const [planOffset, setPlanOffset] = useState(0)
  const [planPageSize, setPlanPageSize] = useState(20)
  const [selectedCallIds, setSelectedCallIds] = useState<string[]>([])
  const [selectedPlanIds, setSelectedPlanIds] = useState<string[]>([])
  const [deleteToolRecordsOpen, setDeleteToolRecordsOpen] = useState(false)
  const [deletingToolRecords, setDeletingToolRecords] = useState(false)
  const [toolDetailOpen, setToolDetailOpen] = useState(false)
  const [showSwitchModal, setShowSwitchModal] = useState(false)
  const [showRiskPolicyModal, setShowRiskPolicyModal] = useState(false)
  const [showManifestModal, setShowManifestModal] = useState(false)

  const loadInitial = async () => {
    setLoading(true)
    try {
      const params = {
        profile: 'admin_full',
        include_disabled: true,
        include_schema: false,
        limit: 300,
      }
      const [listRes, capRes, settingsRes, tokensRes, tokenTemplateRes] = await Promise.allSettled([
        capabilityTools.list(params),
        capabilityTools.capabilities({ profile: 'admin_full', include_disabled: true, include_schema: false, limit: 300 }),
        capabilityTools.settings(),
        capabilityTools.tokens(),
        capabilityTools.tokenTemplates(),
      ])
      if (listRes.status === 'fulfilled') {
        const d = getData(listRes.value)
        setTools(d.tools || [])
        setSettings(d.settings || {})
      }
      if (capRes.status === 'fulfilled') setCapabilities(getData(capRes.value))
      if (settingsRes.status === 'fulfilled') {
        const s = getData(settingsRes.value)
        if (s && Object.keys(s).length > 0) setSettings(s)
      }
      if (tokensRes.status === 'fulfilled') setTokens(getData(tokensRes.value) || [])
      if (tokenTemplateRes.status === 'fulfilled') setTokenTemplates(getData(tokenTemplateRes.value)?.templates || [])
    } catch (e: any) {
      notify({ type: 'error', text: String(e) })
    } finally {
      setLoading(false)
    }
  }

  const loadTabData = async (tab: TabKey) => {
    try {
      if (tab === 'overview') {
        const [manifestRes, riskPolicyRes] = await Promise.allSettled([
          capabilityTools.mcpManifest(),
          capabilityTools.riskPolicy(),
        ])
        if (manifestRes.status === 'fulfilled') setManifest(getData(manifestRes.value))
        if (riskPolicyRes.status === 'fulfilled') setRiskPolicy(getData(riskPolicyRes.value))
      } else if (tab === 'catalog') {
        const res = await capabilityTools.list({ profile: 'admin_full', include_disabled: true, include_schema: true, limit: 300 })
        const d = getData(res)
        setTools(d.tools || [])
        setSettings(d.settings || settings)
      } else if (tab === 'audit') {
        const [callsRes, plansRes] = await Promise.allSettled([
          capabilityTools.calls({ limit: callPageSize, offset: callOffset }),
          capabilityTools.plans({ limit: planPageSize, offset: planOffset }),
        ])
        if (callsRes.status === 'fulfilled') {
          const data = getData(callsRes.value) || {}
          const nextCalls = Array.isArray(data) ? data : (data.items || [])
          setCalls(nextCalls)
          setCallTotal(Number(data.total || nextCalls.length || 0))
          const visibleIds = new Set(nextCalls.map((item: any) => String(item.id)))
          setSelectedCallIds((prev) => prev.filter((id) => visibleIds.has(id)))
        }
        if (plansRes.status === 'fulfilled') {
          const data = getData(plansRes.value) || {}
          const nextPlans = Array.isArray(data) ? data : (data.items || [])
          setPlans(nextPlans)
          setPlanTotal(Number(data.total || nextPlans.length || 0))
          const visibleIds = new Set(nextPlans.map((item: any) => String(item.id)))
          setSelectedPlanIds((prev) => prev.filter((id) => visibleIds.has(id)))
        }
      }
    } catch (e: any) {
      notify({ type: 'error', text: String(e) })
    }
  }

  const refreshAll = async () => {
    capabilityTools.clearCache()
    await loadInitial()
    await loadTabData(activeTab)
  }

  useEffect(() => { loadInitial() }, [])
  useEffect(() => { loadTabData(activeTab) }, [activeTab, callOffset, callPageSize, planOffset, planPageSize])

  const categories = useMemo(() => {
    const fromCaps = capabilities?.categories || []
    if (fromCaps.length) return fromCaps
    return Array.from(new Set(tools.map((t) => t.category).filter(Boolean))).sort()
  }, [capabilities, tools])

  const enabledToolCount = tools.filter((t) => t.available !== false && t.enabled !== false).length
  const blockedToolCount = tools.filter((t) => t.available === false || t.enabled === false || t.blocked_reason).length
  const capabilityVersion = capabilities?.server?.capability_version || manifest?.capability_version || '-'
  const sampleToolInfo = useMemo(() => tools.find((t) => t.name === sampleTool) || null, [tools, sampleTool])
  const sampleMcpAlias = useMemo(() => toMcpAlias(sampleTool), [sampleTool])

  const selectTool = async (tool: ToolInfo) => {
    setSelectedTool(tool)
    setSampleTool(tool.name)
    setSampleArgs(buildSampleArgs(tool))
    try {
      const res = await capabilityTools.detail(tool.name)
      setToolDetail(getData(res))
    } catch (e: any) {
      setToolDetail({ error: String(e) })
    }
  }

  const callSample = async (confirmed = false) => {
    const toolInfo = tools.find((t) => t.name === sampleTool)
    if (!confirmed && isRiskyTool(toolInfo)) {
      setRiskConfirmValue('')
      setRiskConfirmOpen(true)
      return
    }
    try {
      const args = sampleArgs.trim() ? JSON.parse(sampleArgs) : {}
      const res = await capabilityTools.call(sampleTool, args)
      setSampleResult(getData(res))
      setRiskConfirmOpen(false)
      setRiskConfirmValue('')
      await loadTabData(activeTab)
    } catch (e: any) {
      notify({ type: 'error', text: String(e) })
    }
  }

  const updateSetting = async (key: string, value: boolean) => {
    // 乐观更新：先更新本地状态，UI 立即响应
    const next = { ...settings, [key]: value }
    setSettings(next)
    try {
      const res = await capabilityTools.updateSettings(next)
      const serverSettings = getData(res)
      // 将后端可能返回的字符串布尔值转换为真正的布尔值
      const normalized: Record<string, any> = {}
      for (const k of Object.keys(serverSettings || next)) {
        const v = serverSettings?.[k] ?? next[k]
        if (typeof v === 'string') {
          normalized[k] = v.toLowerCase() === 'true'
        } else {
          normalized[k] = v
        }
      }
      setSettings(normalized)
      capabilityTools.clearCache()
      notify({ type: 'success', text: '工具接入设置已保存' })
    } catch (e: any) {
      // 失败时回滚到加载最新状态
      notify({ type: 'error', text: String(e) })
      await refreshAll()
    }
  }


  const revokeToken = async (token: any) => {
    try {
      await capabilityTools.revokeToken(token.id)
      capabilityTools.clearCache()
      notify({ type: 'success', text: 'Token 已撤销' })
      setRevokeCandidate(null)
      await refreshAll()
    } catch (e: any) {
      notify({ type: 'error', text: String(e) })
      setRevokeCandidate(null)
    }
  }

  const purgeToken = async (token: any) => {
    try {
      await capabilityTools.purgeToken(token.id)
      capabilityTools.clearCache()
      notify({ type: 'success', text: `Token「${token.name}」已从数据库删除` })
      setDeleteCandidate(null)
      await refreshAll()
    } catch (e: any) {
      notify({ type: 'error', text: String(e) })
      setDeleteCandidate(null)
    }
  }

  function toggleCallSelection(id: string, checked: boolean) {
    setSelectedCallIds((prev) => checked ? Array.from(new Set([...prev, id])) : prev.filter((x) => x !== id))
  }

  function togglePlanSelection(id: string, checked: boolean) {
    setSelectedPlanIds((prev) => checked ? Array.from(new Set([...prev, id])) : prev.filter((x) => x !== id))
  }

  function toggleVisibleCalls(ids: string[], checked: boolean) {
    setSelectedCallIds((prev) => checked ? Array.from(new Set([...prev, ...ids])) : prev.filter((id) => !ids.includes(id)))
  }

  function toggleVisiblePlans(ids: string[], checked: boolean) {
    setSelectedPlanIds((prev) => checked ? Array.from(new Set([...prev, ...ids])) : prev.filter((id) => !ids.includes(id)))
  }

  const selectedToolRecordCount = selectedCallIds.length + selectedPlanIds.length

  const deleteSelectedToolRecords = async () => {
    if (!selectedToolRecordCount) return
    setDeletingToolRecords(true)
    try {
      await capabilityTools.deleteRecords({ call_ids: selectedCallIds, plan_ids: selectedPlanIds })
      notify({ type: 'success', text: `已删除 ${selectedToolRecordCount} 条工具审计记录` })
      setSelectedCallIds([])
      setSelectedPlanIds([])
      setDeleteToolRecordsOpen(false)
      await loadTabData('audit')
    } catch (e: any) {
      notify({ type: 'error', text: String(e) })
    } finally {
      setDeletingToolRecords(false)
    }
  }

  const apiBaseUrl = 'http://127.0.0.1:8000'

  return (
    <div className="page-stack tool-access-page">
      <PageHeader
        title="AI 工具接入"
        description="OPS 不内置大模型，只暴露可自动发现、可审计的 HTTP Tools / MCP 能力。页面已按场景拆分为接入、Token、工具目录、调试和审计，避免信息堆叠。"
        badge={<StatusBadge value={loading ? 'loading' : 'ready'} />}
        actions={
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <FavoriteButton url={ROUTES.tools} label="AI 工具接入" category="tools" />
            <button className="btn btn-primary" onClick={refreshAll} disabled={loading}>{loading ? '刷新中...' : '刷新能力'}</button>
          </div>
        }
      />

      {loading && tools.length === 0 && (
        <Skeleton type="card" count={3} />
      )}

      {!loading || tools.length > 0 ? (
      <>
      <section className="grid-3 tool-summary-grid">
        <div className="stat-card"><span>Capability Version</span><strong>{capabilityVersion}</strong><small>客户端发现版本变化后应刷新能力清单</small></div>
        <div className="stat-card"><span>当前可调用能力</span><strong>{enabledToolCount}</strong><small>{blockedToolCount > 0 ? `${blockedToolCount} 个被禁用或阻断` : '没有阻断项'}</small></div>
        <div className="stat-card"><span>客户端身份</span><strong>{capabilities?.auth?.client_name || capabilities?.auth?.owner || 'web-session'}</strong><small>{(capabilities?.auth?.scopes || ['*']).join(', ')}</small></div>
      </section>

      <nav className="tool-tabs" aria-label="AI 工具接入功能区">
        {TAB_ITEMS.map((tab) => (
          <button key={tab.key} className={`tool-tab ${activeTab === tab.key ? 'active' : ''}`} onClick={() => setActiveTab(tab.key)}>
            <strong>{tab.label}</strong>
            <span>{tab.hint}</span>
          </button>
        ))}
      </nav>

      {activeTab === 'overview' && (
        <section className="tool-tab-panel">
          <ToolOverviewPanel
            stats={[
              { label: '能力总数', value: tools.length, tone: 'success' },
              { label: '已启用', value: tools.filter((t) => t.enabled !== false && t.available !== false).length, tone: 'success' },
              { label: '高风险', value: tools.filter((t) => t.risk === 'high' || t.risk === 'critical').length, tone: 'danger' },
              { label: '写操作', value: tools.filter((t) => t.write).length, tone: 'warning' },
            ]}
            actions={
              <>
                <button className="btn btn-subtle" onClick={() => setShowSwitchModal(true)}>能力开关</button>
                <button className="btn btn-subtle" onClick={() => setShowRiskPolicyModal(true)}>风险策略</button>
                <button className="btn btn-subtle" onClick={() => setShowManifestModal(true)}>Manifest</button>
              </>
            }
          />

          <div className="card">
            <div className="card-header"><h2>自动发现接入</h2><span>HTTP / MCP</span></div>
            <div className="endpoint-list endpoint-list--canonical">
              {[
                { title: '正式 MCP 主入口', method: 'POST', path: '/api/v2/mcp', desc: 'Streamable HTTP JSON-RPC：initialize / tools/list / tools/call' },
                { title: 'HTTP Tool Catalog', method: 'GET', path: '/api/v2/tools?format=mcp', desc: 'MCP / AI 客户端与前端页面使用的工具目录' },
                { title: 'HTTP Tool Call', method: 'POST', path: '/api/v2/tools/call', desc: '用于非 MCP 客户端，仍走同一 Tool Registry' },
                { title: 'Legacy Gateway', method: 'GET', path: '/api/v2/mcp/legacy/capabilities', desc: '仅兼容旧脚本，新接入不要使用' },
              ].map((ep) => (
                <div key={ep.path} className="endpoint-card">
                  <div className="endpoint-card-header">
                    <strong>{ep.title}</strong>
                    <span className="endpoint-method">{ep.method}</span>
                  </div>
                  <code className="endpoint-path">{ep.path}</code>
                  <small>{ep.desc}</small>
                </div>
              ))}
            </div>
          </div>

          <McpAccessGuide
            selectedServer={selectedMcpAccess}
            onSelectServer={setSelectedMcpAccess}
            servers={[
              { name: 'HTTP', transport: 'http', url: `${apiBaseUrl}/api/v2/tools/call`, description: 'HTTP Tool API：适合普通 HTTP 客户端，使用 tool + arguments 调用 OPS 工具。' },
              { name: 'MCP stdio', transport: 'stdio', command: 'python', args: ['scripts/mcp_server_entry.py'], env: `OPS_BASE_URL=${apiBaseUrl}
OPS_TOOL_TOKEN=<填入 Tool Token>`, description: 'MCP stdio：适合 Claude Desktop / Cursor 等本地 MCP 客户端。' },
              { name: 'MCP HTTP', transport: 'streamable-http', url: `${apiBaseUrl}/api/v2/mcp`, description: 'MCP Streamable HTTP：适合支持远程 MCP HTTP 的客户端。' },
              { name: 'JSON-RPC', transport: 'json-rpc', url: `${apiBaseUrl}/api/v2/mcp`, description: '标准 JSON-RPC 2.0：initialize / tools/list / tools/call。' },
            ]}
            baseUrl={apiBaseUrl}
            extra={
              <div className="helper-strip">
                <span>提示</span>
                <p>只读查询与发布计划可直接通过 Token 调用；发布执行、回滚、配置变更等高风险工具仍需服务端策略允许并通过确认。</p>
                <p style={{ marginTop: 6 }}>
                  <strong>推荐 Token 配置：</strong>
                  scopes 至少包含 <code>["ops:read", "ops:write"]</code>，allow_write=true。
                  <code>ops.inspection.run_*</code> 内部是 write=True（要建 inspection_runs），只给 <code>ops:read</code> 会 403。
                </p>
              </div>
            }
          />

          {/* 能力开关弹窗 */}
          {showSwitchModal && (
            <div className="modal-backdrop" style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,.35)', zIndex: 1000, display: 'grid', placeItems: 'center', padding: 24 }} onClick={() => setShowSwitchModal(false)}>
              <div className="card" style={{ width: 'min(820px, 96vw)', maxHeight: '88vh', overflow: 'auto', display: 'grid', gap: 14 }} onClick={(e) => e.stopPropagation()}>
                <div className="card-header">
                  <div><h2>能力开关</h2><span>默认只读，写操作逐项开启</span></div>
                  <button className="btn btn-subtle" onClick={() => setShowSwitchModal(false)}>关闭</button>
                </div>
                <div className="tool-switch-groups">
                  {[
                    {
                      title: '基础接入',
                      keys: [
                        { key: 'enabled', label: '总开关', desc: '启用全部工具能力' },
                        { key: 'http_tools_enabled', label: 'HTTP 工具', desc: '通过 HTTP 调用工具' },
                        { key: 'mcp_enabled', label: 'MCP 协议', desc: '启用 MCP 接入' },
                        { key: 'read_only', label: '只读模式', desc: '默认禁止写入操作' },
                      ],
                    },
                    {
                      title: '风险管控',
                      keys: [
                        { key: 'require_confirmation', label: '需要确认', desc: '高风险操作需人工确认' },
                        { key: 'taskize_high_risk_tools', label: '任务化高风险', desc: '将高风险转为任务执行' },
                        { key: 'allow_high_risk_tools', label: '允许高风险', desc: '开放高风险工具调用' },
                        { key: 'allow_critical_risk_tools', label: '允许危险级', desc: '开放危险级工具调用' },
                        { key: 'allow_ai_token_to_run_inspection_execute', label: 'AI token 可跑巡检', desc: '允许 AI/MCP tool-token 跑 Path A 巡检（带 confirm_text 二次确认）' },
                      ],
                    },
                    {
                      title: '发布与部署',
                      keys: [
                        { key: 'allow_deploy_plan', label: '发布计划', desc: '允许创建发布计划' },
                        { key: 'allow_deploy_execute', label: '发布执行', desc: '允许执行发布部署' },
                        { key: 'allow_prod_deploy', label: '生产发布', desc: '允许发布到生产环境' },
                        { key: 'allow_rollback', label: '回滚操作', desc: '允许执行回滚' },
                      ],
                    },
                    {
                      title: '配置与维护',
                      keys: [
                        { key: 'allow_config_write', label: '配置写入', desc: '允许修改系统配置' },
                        { key: 'allow_backup_write', label: '备份创建', desc: '允许创建备份' },
                        { key: 'allow_backup_restore', label: '备份恢复', desc: '允许从备份恢复' },
                        { key: 'allow_runtime_cleanup', label: '运行时清理', desc: '允许清理运行时数据' },
                      ],
                    },
                    {
                      title: '服务器与包',
                      keys: [
                        { key: 'allow_server_read', label: '服务器读取', desc: '允许读取服务器信息' },
                        { key: 'allow_server_write', label: '服务器写入', desc: '允许修改服务器配置' },
                        { key: 'allow_package_write', label: '包管理', desc: '允许上传/管理发布包' },
                        { key: 'allow_package_cleanup', label: '包清理', desc: '允许清理过期包' },
                      ],
                    },
                    {
                      title: '数据库',
                      keys: [
                        { key: 'allow_db_read_tools', label: '数据库只读工具', desc: '允许数据库只读工具' },
                        { key: 'allow_db_export_tools', label: '数据库导出工具', desc: '允许数据库导出工具' },
                        { key: 'allow_db_write_tools', label: '数据库写入工具', desc: '允许数据库写入工具' },
                      ],
                    },
                    {
                      title: '运行时与安全',
                      keys: [
                        { key: 'agent_runtime_enabled', label: 'Agent 运行时', desc: 'Agent 运行时' },
                        { key: 'strict_prod_confirmation', label: '严格生产环境确认', desc: '严格生产环境确认' },
                      ],
                    },
                  ].map((group) => (
                    <div key={group.title} className="tool-switch-group">
                      <div className="tool-switch-group-title">{group.title}</div>
                      <div className="tool-switch-grid">
                        {group.keys.map(({ key, label, desc }) => (
                          <label key={key} className="tool-switch" title={desc}>
                            <input type="checkbox" checked={settings[key] !== undefined ? !!settings[key] : (CAPABILITY_DEFAULTS[key] ?? false)} onChange={(e) => updateSetting(key, e.target.checked)} />
                            <span>{label}</span>
                          </label>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* 风险策略弹窗 */}
          {showRiskPolicyModal && (
            <div className="modal-backdrop" style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,.35)', zIndex: 1000, display: 'grid', placeItems: 'center', padding: 24 }} onClick={() => setShowRiskPolicyModal(false)}>
              <div className="card" style={{ width: 'min(820px, 96vw)', maxHeight: '88vh', overflow: 'auto', display: 'grid', gap: 14 }} onClick={(e) => e.stopPropagation()}>
                <div className="card-header">
                  <div><h2>风险策略</h2><span>工具风险分级与确认规则</span></div>
                  <button className="btn btn-subtle" onClick={() => setShowRiskPolicyModal(false)}>关闭</button>
                </div>
                <ToolRiskPolicyPanel
                  rules={riskPolicy?.rules
                    ? Object.entries(riskPolicy.rules).map(([risk, config]: [string, any]) => ({
                      risk,
                      action: config?.require_confirmation ? 'confirm' : 'allow',
                      description: config?.description || '',
                      affected_tools: config?.affected_tools,
                    }))
                    : [
                      { risk: 'critical', action: 'confirm' as const, description: '危险 CLI、写入文件、执行远程命令' },
                      { risk: 'high', action: 'confirm' as const, description: '发布执行、回滚、配置变更、备份恢复' },
                      { risk: 'medium', action: 'confirm' as const, description: '发布计划、预检、SQL 写操作' },
                      { risk: 'low', action: 'allow' as const, description: '只读查询、巡检、状态检查' },
                    ]}
                />
              </div>
            </div>
          )}

          {/* Manifest 弹窗 */}
          {showManifestModal && (
            <div className="modal-backdrop" style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,.35)', zIndex: 1000, display: 'grid', placeItems: 'center', padding: 24 }} onClick={() => setShowManifestModal(false)}>
              <div className="card" style={{ width: 'min(920px, 96vw)', maxHeight: '88vh', overflow: 'auto', display: 'grid', gap: 14 }} onClick={(e) => e.stopPropagation()}>
                <div className="card-header">
                  <div><h2>Manifest 摘要</h2><span>当前能力发现元数据</span></div>
                  <button className="btn btn-subtle" onClick={() => setShowManifestModal(false)}>关闭</button>
                </div>
                <JsonBlock value={{
                  server: capabilities?.server,
                  features: capabilities?.features || settings,
                  policies: capabilities?.policies,
                  risk_policy: riskPolicy?.rules,
                  resources: capabilities?.resources,
                  prompts: capabilities?.prompts,
                  endpoints: manifest?.tools_endpoint ? {
                    tools_endpoint: manifest.tools_endpoint,
                    capabilities_endpoint: manifest.capabilities_endpoint,
                    call_endpoint: manifest.call_endpoint,
                    mcp_stdio: 'Windows: venv\\Scripts\\python.exe scripts\\mcp_server_entry.py; macOS/Linux: scripts/mcp-server.sh',
                    api_base_url: apiBaseUrl,
                  } : undefined,
                }} />
              </div>
            </div>
          )}
        </section>
      )}

      {activeTab === 'tokens' && (
        <section className="tool-tab-panel">
          <ToolTokenPanel
            tokens={tokens.map((t: any) => ({
              id: t.id,
              name: t.name,
              description: t.description || '',
              status: t.status || (t.revoked_at ? 'revoked' : 'active'),
              created_at: t.created_at,
              last_used_at: t.last_used_at,
              expires_at: t.expires_at,
              revoked_at: t.revoked_at,
              scopes: t.scopes,
              allow_write: t.allow_write,
              allow_prod: t.allow_prod,
              bound_room_ids: Array.isArray(t.bound_room_ids) ? t.bound_room_ids : [],
              approver_matrix_ids: Array.isArray(t.approver_matrix_ids) ? t.approver_matrix_ids : [],
              key_prefix: t.token_prefix,
              masked_value: t.token_prefix ? `${t.token_prefix}...` : undefined,
            }))}
            loading={loading}
            canDelete={isAdmin}
            tokenTemplates={tokenTemplates}
            onGenerate={async (data) => {
              setCreatedToken('')
              const res = await capabilityTools.createToken({
                name: data.name,
                description: data.description,
                scopes: data.scopes,
                allow_write: data.allow_write,
                allow_prod: data.allow_prod,
                expires_in_days: data.expires_in_days,
                bound_room_ids: data.bound_room_ids || [],
                approver_matrix_ids: data.approver_matrix_ids || [],
              })
              const d = getData(res)
              setCreatedToken(d.token || '')
              capabilityTools.clearCache()
              notify({ type: 'success', text: 'Token 已创建，请立即复制保存' })
              await refreshAll()
            }}
            onUpdate={async (tokenId, data) => {
              await capabilityTools.updateToken(tokenId, {
                ...data,
                bound_room_ids: data.bound_room_ids ?? [],
              })
              capabilityTools.clearCache()
              notify({ type: 'success', text: 'Token 权限已更新' })
              await refreshAll()
            }}
            onRevoke={(tokenId) => {
               const token = tokens.find((t: any) => t.id === tokenId)
               setRevokeCandidate(token || { id: tokenId, name: tokenId })
             }}
            onDelete={(tokenId) => {
               const token = tokens.find((t: any) => t.id === tokenId)
               setDeleteCandidate(token || { id: tokenId, name: tokenId })
             }}
            onPreviewPolicy={async (data) => {
              const res = await capabilityTools.policyPreview(data)
              return getData(res)
            }}
          />
          {createdToken && (
            <div className="alert alert-warning" style={{ marginTop: 12 }}>
              <strong>请立即复制新令牌：</strong><code>{createdToken}</code>
              <button className="btn btn-subtle" style={{ marginLeft: 8 }} onClick={() => void navigator.clipboard?.writeText(createdToken)}>复制</button>
            </div>
          )}
        </section>
      )}

      {activeTab === 'catalog' && (
        <section className="tool-tab-panel">
          <ToolCatalogPanel
            tools={tools}
            categories={categories}
            loading={loading}
            selectedToolName={selectedTool?.name}
            onOpenDetail={(tool) => {
              selectTool(tool)
              setToolDetailOpen(true)
            }}
            onOpenPlayground={(tool) => {
              setSampleTool(tool.name)
              setSampleArgs(buildSampleArgs(tool))
              setActiveTab('playground')
            }}
          />
          <ToolDetailDrawer
            open={toolDetailOpen}
            tool={selectedTool}
            toolDetail={toolDetail}
            onClose={() => setToolDetailOpen(false)}
            onOpenPlayground={() => {
              if (!selectedTool) return
              setSampleTool(selectedTool.name)
              setSampleArgs(buildSampleArgs(selectedTool))
              setToolDetailOpen(false)
              setActiveTab('playground')
            }}
          />
        </section>
      )}

      {activeTab === 'playground' && (
        <section className="tool-tab-panel">
          <div className="grid-2">
            <ToolPlaygroundPanel
              defaultTool={sampleTool}
              defaultArgs={sampleArgs}
              onExecute={async (toolName, argsStr) => {
                const toolInfo = tools.find((t) => t.name === toolName)
                if (isRiskyTool(toolInfo)) {
                  setSampleTool(toolName)
                  setSampleArgs(argsStr)
                  setRiskConfirmValue('')
                  setRiskConfirmOpen(true)
                  return { result: '', error: '等待风险确认中...' }
                }
                try {
                   const parsed = JSON.parse(argsStr)
                   const res = await capabilityTools.call(toolName, parsed)
                   const data = getData(res)
                   setSampleResult(data)
                   const result = {
                     result: JSON.stringify(data?.result || data, null, 2),
                     error: data?.error || '',
                     duration_ms: data?.duration_ms || data?.elapsed_ms || null,
                   }
                   // Show the tool response immediately; audit/catalog refresh must not block it.
                   void loadTabData(activeTab)
                   return result
                } catch (err: any) {
                  return {
                    result: '',
                    error: err?.response?.data?.error || err?.message || '执行失败',
                    duration_ms: null,
                  }
                }
              }}
            />
            <div className="card">
              <div className="card-header"><h2>调用结果</h2><span>{sampleResult?.ok === false ? '失败' : sampleResult ? '已返回' : '等待调用'}</span></div>
              {sampleResult ? <JsonBlock value={sampleResult} /> : <div className="empty-cell">选择工具并填写参数后，点击“调用工具”。</div>}
            </div>
          </div>
        </section>
      )}

      {activeTab === 'audit' && (
        <section className="tool-tab-panel">
          <div className="card" style={{ marginBottom: 12 }}>
            <div className="card-header">
              <div>
                <h2>工具审计记录</h2>
                <span>调用记录和操作计划均支持分页查看；活跃计划默认受后端保护，不会被误删。</span>
              </div>
              <button className="btn btn-danger" disabled={!isAdmin || selectedToolRecordCount === 0 || deletingToolRecords} onClick={() => setDeleteToolRecordsOpen(true)}>
                批量删除 ({selectedToolRecordCount})
              </button>
            </div>
          </div>
          <div className="grid-2">
            <div style={{ display: 'grid', gap: 10 }}>
              <ToolAuditTimeline
                entries={calls.map((c: any) => ({
                  id: c.id,
                  tool: c.tool_name || c.tool,
                  risk: c.risk_level || c.risk,
                  created_at: c.created_at,
                  caller: c.caller || c.owner || '-',
                  source: c.source || 'HTTP',
                  status: c.status,
                  duration_ms: c.duration_ms,
                  args_summary: c.args_summary,
                  result_summary: c.blocked_reason || c.related_deployment_id || c.related_plan_id || c.result_summary,
                }))}
                loading={loading}
                selectedIds={selectedCallIds}
                onToggle={toggleCallSelection}
                onToggleAll={toggleVisibleCalls}
              />
              <ToolRecordPagination
                total={callTotal}
                limit={callPageSize}
                offset={callOffset}
                onOffsetChange={setCallOffset}
                onPageSizeChange={(next) => { setCallPageSize(next); setCallOffset(0) }}
              />
            </div>
            <div className="card">
              <div className="card-header">
                <div><h2>操作计划</h2><span>发布计划 / 配置变更计划</span></div>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text-muted)', fontSize: 13 }}>
                  <input
                    type="checkbox"
                    checked={plans.length > 0 && plans.every((p) => selectedPlanIds.includes(String(p.id)))}
                    onChange={(e) => toggleVisiblePlans(plans.map((p) => String(p.id)), e.target.checked)}
                  />
                  选择当前页
                </label>
              </div>
              <div className="timeline-list">
                {plans.map((p) => <div className="timeline-item" key={p.id}><input type="checkbox" checked={selectedPlanIds.includes(String(p.id))} onChange={(e) => togglePlanSelection(String(p.id), e.target.checked)} aria-label={`选择计划 ${p.id}`} style={{ float: 'right' }} /><strong>{p.plan_type} · {p.status}</strong><small>{p.system}/{p.service}/{p.environment} · {p.created_at}</small><p>{p.confirm_text || p.related_deployment_id || p.id}</p></div>)}
                {plans.length === 0 && <div className="empty-cell">暂无计划</div>}
              </div>
              <ToolRecordPagination
                total={planTotal}
                limit={planPageSize}
                offset={planOffset}
                onOffsetChange={setPlanOffset}
                onPageSizeChange={(next) => { setPlanPageSize(next); setPlanOffset(0) }}
              />
            </div>
          </div>
        </section>
      )}

      <ConfirmDialog
        open={Boolean(revokeCandidate)}
        title="撤销 Tool Token"
        description="撤销后，使用该 Token 的 MCP / AI 客户端会立即失去访问能力。"
        danger
        confirmLabel="撤销"
        onCancel={() => setRevokeCandidate(null)}
        onConfirm={() => { if (revokeCandidate) void revokeToken(revokeCandidate) }}
      >
        <div className="token-revoke-summary">
          <strong>{revokeCandidate?.name}</strong>
          <span>{revokeCandidate?.owner} · {(revokeCandidate?.scopes || []).join(', ')}</span>
        </div>
      </ConfirmDialog>

      <ConfirmDialog
        open={Boolean(deleteCandidate)}
        title="永久删除 Tool Token"
        description="将从数据库物理删除该 Token（不可恢复），相关审计记录会保留。仅管理员可执行此操作。"
        danger
        confirmLabel="确认删除"
        onCancel={() => setDeleteCandidate(null)}
        onConfirm={() => { if (deleteCandidate) void purgeToken(deleteCandidate) }}
      >
        <div className="token-revoke-summary">
          <strong>{deleteCandidate?.name}</strong>
          <span>
            {deleteCandidate?.owner} · {(deleteCandidate?.scopes || []).join(', ')}
            {deleteCandidate?.allow_write && <em style={{ color: 'var(--text-warning)', marginLeft: 6 }}>· 允许写</em>}
            {deleteCandidate?.allow_prod && <em style={{ color: 'var(--text-danger)', marginLeft: 6 }}>· 允许生产</em>}
          </span>
        </div>
      </ConfirmDialog>

      <ConfirmDialog
        open={deleteToolRecordsOpen}
        title="批量删除工具审计记录"
        description={`确认删除选中的 ${selectedCallIds.length} 条调用记录和 ${selectedPlanIds.length} 条操作计划？运行中计划会被后端拒绝删除。`}
        danger
        confirmLabel={deletingToolRecords ? '删除中...' : '批量删除'}
        onCancel={() => setDeleteToolRecordsOpen(false)}
        onConfirm={deleteSelectedToolRecords}
      />

      <RiskConfirmDialog
        open={riskConfirmOpen}
        title="确认调用高风险工具"
        description="当前工具可能产生写入、发布、清理、恢复或配置变更。本地调试也应通过显式确认，避免误点。"
        target={sampleTool}
        confirmText={`CALL ${sampleTool}`}
        value={riskConfirmValue}
        onValueChange={setRiskConfirmValue}
        onCancel={() => setRiskConfirmOpen(false)}
        onConfirm={() => void callSample(true)}
        riskLevel={sampleToolInfo?.risk || 'high'}
        details={[
          { label: '工具别名', value: sampleMcpAlias },
          { label: '分类', value: sampleToolInfo?.category || '-' },
          { label: '权限', value: sampleToolInfo?.write ? 'write' : 'read-only' },
        ]}
        confirmMode="one-click"
      />
      </>
      ) : null}
    </div>
  )
}
