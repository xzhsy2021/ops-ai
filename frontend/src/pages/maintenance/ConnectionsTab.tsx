import { useState, useCallback, useRef, useEffect } from 'react'
import type { CSSProperties, ChangeEvent } from 'react'

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  draft: { bg: 'var(--border-strong)', color: 'var(--text-secondary)' },
  pending_approval: { bg: 'var(--warning-surface)', color: 'var(--warning)' },
  approved: { bg: 'var(--success-surface)', color: 'var(--success)' },
}

function keyDisplayName(key: any) {
  const size = Number(key?.size || 0)
  const sizeText = size > 0 ? ` (${Math.ceil(size / 1024)}KB)` : ''
  return `${key?.name || ''}${sizeText}`
}

function serverDisplayName(server: any) {
  const host = server?.host || '-'
  const port = server?.port || 22
  const group = server?.group ? ` · ${server.group}` : ''
  return `${server?.name || host} (${host}:${port})${group}`
}

function serverSourceLabel(server: any) {
  const source = server?.source
  if (source === 'database') return '管理服务器'
  if (source === 'config') return '配置服务器'
  return '服务器资产'
}

function applyTargetServer(server: any, setConnForm: (updater: any) => void) {
  if (!server) {
    setConnForm((f: any) => ({ ...f, ssh_target_server_name: '' }))
    return
  }
  const authType = server.auth_type || (server.has_key_content ? 'key_content' : server.key ? 'key_file' : server.has_password ? 'password' : '')
  setConnForm((f: any) => ({
    ...f,
    use_ssh_tunnel: true,
    ssh_target_server_name: server.name || '',
    ssh_target_host: server.host || f.ssh_target_host || '',
    ssh_target_port: Number(server.port || f.ssh_target_port || 22),
    ssh_target_username: server.user || server.username || f.ssh_target_username || 'root',
    ssh_target_key_path: authType === 'key_file' ? (server.key || server.key_file || f.ssh_target_key_path || '') : f.ssh_target_key_path,
    ssh_target_password: '',
  }))
}

function applyBastionServer(server: any, setConnForm: (updater: any) => void) {
  if (!server) {
    setConnForm((f: any) => ({ ...f, ssh_mode: 'manual', ssh_server_name: '' }))
    return
  }
  const authType = server.auth_type || (server.has_key_content ? 'key_content' : server.key ? 'key_file' : server.has_password ? 'password' : '')
  setConnForm((f: any) => ({
    ...f,
    use_ssh_tunnel: true,
    ssh_mode: 'server',
    ssh_server_name: server.name || '',
    ssh_host: server.host || f.ssh_host || '',
    ssh_port: Number(server.port || f.ssh_port || 22),
    ssh_username: server.user || server.username || f.ssh_username || 'root',
    ssh_key_path: authType === 'key_file' ? (server.key || server.key_file || f.ssh_key_path || '') : f.ssh_key_path,
    ssh_password: '',
  }))
}

function Badge({ label, value }: { label: string; value: string }) {
  const colors = STATUS_COLORS[value]
  if (!colors) return <span>{label}</span>
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: '4px',
      fontSize: '12px',
      fontWeight: 500,
      background: colors.bg,
      color: colors.color,
    }}>
      {label}
    </span>
  )
}

interface ConnectionsTabProps {
  connStats: { total: number; prod: number; tunnel: number }
  connSearch: string
  connEnvFilter: string
  envOptions: string[]
  connLoading: boolean
  showConnForm: boolean
  connForm: any
  connSubmitting: boolean
  filteredConnections: any[]
  bastionServers: any[]
  bastionServersLoading: boolean
  loadBastionServers: () => void
  sshKeys: any[]
  sshKeysLoading: boolean
  sshKeyUploading: boolean
  loadSshKeys: () => void
  onUploadSshKey: (event: ChangeEvent<HTMLInputElement>) => void
  onUploadTargetSshKey: (event: ChangeEvent<HTMLInputElement>) => void
  inputStyle: CSSProperties
  selectStyle: CSSProperties
  setConnSearch: (value: string) => void
  setConnEnvFilter: (value: string) => void
  setConnForm: (updater: any) => void
  loadConnections: () => void
  onShowCreate: () => void
  onCloseForm: () => void
  onSubmit: () => void
  onTest: (id: string) => void
  onDelete: (id: string, name: string) => void
  onEdit?: (connection: any) => void
  onUploadSshKeyContent?: (connectionId: string, content: string) => Promise<void>
  onDeleteSshKeyContent?: (connectionId: string) => Promise<void>
}

function ConnectionEditDrawer({
  open,
  connForm,
  connSubmitting,
  bastionServers,
  bastionServersLoading,
  loadBastionServers,
  sshKeys,
  sshKeysLoading,
  sshKeyUploading,
  loadSshKeys,
  onUploadSshKey,
  onUploadTargetSshKey,
  inputStyle,
  selectStyle,
  setConnForm,
  onClose,
  onSubmit,
  anchorRect,
}: {
  open: boolean
  connForm: any
  connSubmitting: boolean
  bastionServers: any[]
  bastionServersLoading: boolean
  loadBastionServers: () => void
  sshKeys: any[]
  sshKeysLoading: boolean
  sshKeyUploading: boolean
  loadSshKeys: () => void
  onUploadSshKey: (event: ChangeEvent<HTMLInputElement>) => void
  onUploadTargetSshKey: (event: ChangeEvent<HTMLInputElement>) => void
  inputStyle: CSSProperties
  selectStyle: CSSProperties
  setConnForm: (updater: any) => void
  onClose: () => void
  onSubmit: () => void
  anchorRect?: { top: number; left: number; right: number; bottom: number }
}) {
  const [activeSection, setActiveSection] = useState<'basic' | 'access' | 'dml' | 'ssh'>('basic')

  // 打开抽屉时自动滚动内容区到顶部
  const contentRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (open && contentRef.current) {
      contentRef.current.scrollTop = 0
    }
  }, [open, activeSection])

  if (!open) return null

  const sectionNav = (
    <div className="conn-drawer-nav">
      {[
        { key: 'basic', label: '基础信息', icon: '⚙️' },
        { key: 'access', label: '数据库访问', icon: '🔗' },
        { key: 'dml', label: 'DML 策略', icon: '📝' },
        { key: 'ssh', label: 'SSH 跳板', icon: '🔒' },
      ].map((s) => (
        <button
          key={s.key}
          className={activeSection === s.key ? 'active' : ''}
          onClick={() => setActiveSection(s.key as any)}
        >
          <span>{s.icon}</span>
          {s.label}
        </button>
      ))}
    </div>
  )

  const drawerStyle: CSSProperties | undefined = anchorRect
    ? {
        width: 720,
        maxWidth: '95vw',
        maxHeight: '90vh',
        height: 'auto',
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 12,
        boxShadow: '0 12px 40px rgba(0,0,0,0.35)',
        display: 'flex',
        flexDirection: 'column',
      }
    : undefined

  const drawerWrapperStyle: CSSProperties | undefined = anchorRect
    ? {
        position: 'fixed',
        inset: 0,
        zIndex: 200,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        animation: 'connDialogFadeIn 0.2s ease',
      }
    : undefined

  return (
    <div className="conn-drawer-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose() }} style={drawerWrapperStyle}>
      <div className="conn-drawer" style={drawerStyle}>
        <div className="conn-drawer-header">
          <div>
            <h2>{connForm.id ? '编辑数据库连接' : '新建数据库连接'}</h2>
            <p>连接信息会被 SQL 查询、SQL 执行、数据清理和表结构探测复用。</p>
          </div>
          <button className="btn btn-subtle" onClick={onClose}>关闭</button>
        </div>

        <div className="conn-drawer-body">
          {sectionNav}

          <div className="conn-drawer-content" ref={contentRef}>
            {activeSection === 'basic' && (
              <div className="conn-section">
                <div className="maintenance-form-title"><strong>基础信息</strong><span>连接名称、环境和数据库类型</span></div>
                <div className="maintenance-form-grid">
                  <label className="field-label">名称 *
                    <input style={inputStyle} value={connForm.name} onChange={e => setConnForm((f: any) => ({ ...f, name: e.target.value }))} placeholder="如 crypto-prod-maintenance" />
                  </label>
                  <label className="field-label">环境 *
                    <select style={selectStyle} value={connForm.environment} onChange={e => setConnForm((f: any) => ({ ...f, environment: e.target.value }))}>
                      <option value="dev">dev</option>
                      <option value="staging">staging</option>
                      <option value="production">production</option>
                    </select>
                  </label>
                  <label className="field-label">数据库类型
                    <select style={selectStyle} value={connForm.db_type} onChange={e => setConnForm((f: any) => ({ ...f, db_type: e.target.value }))}>
                      <option value="mysql">mysql</option>
                      <option value="postgresql">postgresql</option>
                      <option value="mariadb">mariadb</option>
                    </select>
                  </label>
                  <label className="field-label maintenance-span-2">描述
                    <input style={inputStyle} value={connForm.description || ''} onChange={e => setConnForm((f: any) => ({ ...f, description: e.target.value }))} placeholder="可选描述" />
                  </label>
                </div>
              </div>
            )}

            {activeSection === 'access' && (
              <div className="conn-section">
                <div className="maintenance-form-title"><strong>数据库访问</strong><span>填写应用侧可访问的数据库地址和维护账号</span></div>
                <div className="maintenance-form-grid maintenance-form-grid--wide">
                  <label className="field-label">主机 *
                    <input style={inputStyle} value={connForm.host} onChange={e => setConnForm((f: any) => ({ ...f, host: e.target.value }))} placeholder="127.0.0.1 / db.internal" />
                  </label>
                  <label className="field-label">端口
                    <input style={inputStyle} type="number" value={connForm.port} onChange={e => setConnForm((f: any) => ({ ...f, port: Number(e.target.value) }))} />
                  </label>
                  <label className="field-label">数据库名 *
                    <input style={inputStyle} value={connForm.database_name} onChange={e => setConnForm((f: any) => ({ ...f, database_name: e.target.value }))} />
                  </label>
                  <label className="field-label">用户名
                    <input style={inputStyle} value={connForm.username} onChange={e => setConnForm((f: any) => ({ ...f, username: e.target.value }))} />
                  </label>
                  <label className="field-label">密码
                    <input style={inputStyle} type="password" value={connForm.password} onChange={e => setConnForm((f: any) => ({ ...f, password: e.target.value }))} placeholder={connForm.id ? '留空表示不修改' : ''} />
                  </label>
                </div>
              </div>
            )}

            {activeSection === 'dml' && (
              <div className="conn-section">
                <div className={`maintenance-form-section ${connForm.allow_dml ? 'maintenance-form-section--active' : ''}`} style={{ border: 'none', padding: 0 }}>
                  <div className="maintenance-form-title maintenance-form-title--inline">
                    <div>
                      <strong>DML 执行策略</strong>
                      <span>默认关闭。仅对维护账号显式开放 INSERT / UPDATE / DELETE，并使用影响行数保护。</span>
                    </div>
                    <label className="switch-line">
                      <input type="checkbox" checked={Boolean(connForm.allow_dml)} onChange={e => setConnForm((f: any) => ({ ...f, allow_dml: e.target.checked }))} />
                      允许受控 DML
                    </label>
                  </div>
                  {connForm.allow_dml && (
                    <div className="maintenance-form-grid maintenance-form-grid--wide">
                      <label className="field-label">允许语句类型
                        <select multiple style={{ ...selectStyle, minHeight: 90 }} value={connForm.allowed_dml_types || []} onChange={e => setConnForm((f: any) => ({ ...f, allowed_dml_types: Array.from(e.target.selectedOptions).map(o => o.value) }))}>
                          <option value="insert">INSERT</option>
                          <option value="update">UPDATE</option>
                          <option value="delete">DELETE</option>
                        </select>
                      </label>
                      <label className="field-label">最大影响行数
                        <input style={inputStyle} type="number" min={1} max={1000} value={connForm.max_affected_rows_default} onChange={e => setConnForm((f: any) => ({ ...f, max_affected_rows_default: Number(e.target.value) }))} />
                      </label>
                      <label className="field-label maintenance-span-2">允许表白名单（可选，逗号或换行分隔）
                        <textarea style={{ ...inputStyle, minHeight: 70 }} value={connForm.allowed_tables_text} onChange={e => setConnForm((f: any) => ({ ...f, allowed_tables_text: e.target.value }))} placeholder="为空表示不限制；建议生产连接明确填写" />
                      </label>
                      <label className="field-label maintenance-span-2">禁止表黑名单（可选，逗号或换行分隔）
                        <textarea style={{ ...inputStyle, minHeight: 70 }} value={connForm.blocked_tables_text} onChange={e => setConnForm((f: any) => ({ ...f, blocked_tables_text: e.target.value }))} placeholder="例如 users, permissions, audit_logs" />
                      </label>
                      <label className="switch-line maintenance-span-2">
                        <input type="checkbox" checked={Boolean(connForm.require_dml_reason)} onChange={e => setConnForm((f: any) => ({ ...f, require_dml_reason: e.target.checked }))} />
                        执行 DML 必须填写原因
                      </label>
                    </div>
                  )}
                </div>
              </div>
            )}

            {activeSection === 'ssh' && (
              <div className="conn-section">
                <div className={`maintenance-form-section ${connForm.use_ssh_tunnel ? 'maintenance-form-section--active' : ''}`} style={{ border: 'none', padding: 0 }}>
                  <div className="maintenance-form-title maintenance-form-title--inline">
                    <div>
                      <strong>SSH 跳板机</strong>
                      <span>支持从服务器资产选择或手动配置跳板机。</span>
                    </div>
                    <label className="switch-line">
                      <input type="checkbox" checked={connForm.use_ssh_tunnel} onChange={e => setConnForm((f: any) => ({ ...f, use_ssh_tunnel: e.target.checked }))} />
                      启用代理
                    </label>
                  </div>
                  {connForm.use_ssh_tunnel && (
                    <div className="maintenance-form-grid maintenance-form-grid--wide">
                      <label className="field-label maintenance-span-2">配置方式
                        <div className="inline-control-row">
                          <button type="button" className={`btn ${connForm.ssh_mode === 'server' ? 'btn-primary' : 'btn-subtle'}`} onClick={() => setConnForm((f: any) => ({ ...f, ssh_mode: 'server' }))}>从服务器选择</button>
                          <button type="button" className={`btn ${connForm.ssh_mode === 'manual' ? 'btn-primary' : 'btn-subtle'}`} onClick={() => setConnForm((f: any) => ({ ...f, ssh_mode: 'manual', ssh_server_name: '' }))}>手动配置</button>
                        </div>
                      </label>

                      {connForm.ssh_mode === 'server' && (
                        <label className="field-label maintenance-span-2">从服务器资产选择跳板机
                          <div className="inline-control-row">
                            <select style={selectStyle} value={connForm.ssh_server_name || ''} onChange={e => { const name = e.target.value; const selected = bastionServers.find((srv: any) => srv.name === name); applyBastionServer(selected, setConnForm) }}>
                              <option value="">请选择服务器...</option>
                              {bastionServers.length === 0 && <option value="" disabled>{bastionServersLoading ? '正在加载...' : '暂无可选服务器'}</option>}
                              {bastionServers.map((server: any) => <option key={server.name || server.host} value={server.name}>{serverDisplayName(server)} · {serverSourceLabel(server)}</option>)}
                            </select>
                            <button type="button" className="btn btn-subtle" onClick={loadBastionServers} disabled={bastionServersLoading}>{bastionServersLoading ? '加载中...' : '刷新服务器'}</button>
                          </div>
                          <small className="field-hint">选择后会自动复用服务器资产中的主机、端口、用户名和认证信息。</small>
                        </label>
                      )}

                      {connForm.ssh_mode === 'manual' && (
                        <>
                          <label className="field-label">跳板机主机 *
                            <input style={inputStyle} value={connForm.ssh_host} onChange={e => setConnForm((f: any) => ({ ...f, ssh_host: e.target.value }))} placeholder="如 192.168.1.100" />
                          </label>
                          <label className="field-label">跳板机端口
                            <input style={inputStyle} type="number" value={connForm.ssh_port} onChange={e => setConnForm((f: any) => ({ ...f, ssh_port: Number(e.target.value) }))} />
                          </label>
                          <label className="field-label">SSH 用户名 *
                            <input style={inputStyle} value={connForm.ssh_username} onChange={e => setConnForm((f: any) => ({ ...f, ssh_username: e.target.value }))} />
                          </label>
                          <label className="field-label">SSH 密码
                            <input style={inputStyle} type="password" value={connForm.ssh_password} onChange={e => setConnForm((f: any) => ({ ...f, ssh_password: e.target.value }))} placeholder="密码认证时填写" />
                          </label>
                          <label className="field-label maintenance-span-2">SSH 私钥
                            <div className="ssh-key-picker">
                              <select style={selectStyle} value={sshKeys.some((key: any) => key.name === connForm.ssh_key_path) ? connForm.ssh_key_path : ''} onChange={e => setConnForm((f: any) => ({ ...f, ssh_key_path: e.target.value }))}>
                                <option value="">选择已保存密钥...</option>
                                {sshKeys.map((key: any) => <option key={key.name} value={key.name}>{keyDisplayName(key)}</option>)}
                              </select>
                              <input style={inputStyle} value={connForm.ssh_key_path} onChange={e => setConnForm((f: any) => ({ ...f, ssh_key_path: e.target.value }))} placeholder="密钥文件名或绝对路径" />
                              <div className="ssh-key-picker-actions">
                                <button type="button" className="btn btn-subtle" onClick={loadSshKeys} disabled={sshKeysLoading}>{sshKeysLoading ? '加载中...' : '刷新密钥'}</button>
                                <label className={`btn btn-subtle ${sshKeyUploading ? 'disabled' : ''}`}>{sshKeyUploading ? '上传中...' : '上传新密钥'}<input type="file" onChange={onUploadSshKey} disabled={sshKeyUploading} style={{ display: 'none' }} /></label>
                                <a className="btn btn-subtle" href="/servers" target="_blank" rel="noreferrer">密钥管理</a>
                              </div>
                            </div>
                            <small className="field-hint">可选：选择已保存密钥、上传新密钥文件，或手动填写路径。</small>
                          </label>
                          <label className="field-label">私钥口令
                            <input style={inputStyle} type="password" value={connForm.ssh_key_passphrase} onChange={e => setConnForm((f: any) => ({ ...f, ssh_key_passphrase: e.target.value }))} />
                          </label>
                        </>
                      )}

                      <div className="field-label maintenance-span-2" style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border-strong)' }}>
                        <strong>目标主机（第二跳）</strong>
                        <span style={{ display: 'block', color: 'var(--text-secondary)', fontSize: 12, marginBottom: 8 }}>如需从跳板机再 SSH 到目标主机访问数据库，请配置下方选项。</span>
                        <label className="field-label maintenance-span-2">从服务器资产选择目标主机
                          <div className="inline-control-row">
                            <select style={selectStyle} value={connForm.ssh_target_server_name || ''} onChange={e => { const name = e.target.value; const selected = bastionServers.find((srv: any) => srv.name === name); applyTargetServer(selected, setConnForm) }}>
                              <option value="">不使用第二跳 / 手动填写...</option>
                              {bastionServers.length === 0 && <option value="" disabled>{bastionServersLoading ? '正在加载...' : '暂无可选服务器'}</option>}
                              {bastionServers.map((server: any) => <option key={server.name || server.host} value={server.name}>{serverDisplayName(server)} · {serverSourceLabel(server)}</option>)}
                            </select>
                            <button type="button" className="btn btn-subtle" onClick={loadBastionServers} disabled={bastionServersLoading}>{bastionServersLoading ? '加载中...' : '刷新服务器'}</button>
                          </div>
                        </label>
                        <label className="field-label">目标主机地址
                          <input style={inputStyle} value={connForm.ssh_target_host || ''} onChange={e => setConnForm((f: any) => ({ ...f, ssh_target_host: e.target.value }))} placeholder="选择服务器后自动带出，可手动覆盖" />
                        </label>
                        <label className="field-label">目标主机端口
                          <input style={inputStyle} type="number" value={connForm.ssh_target_port || 22} onChange={e => setConnForm((f: any) => ({ ...f, ssh_target_port: Number(e.target.value) }))} />
                        </label>
                        <label className="field-label">目标主机 SSH 用户名
                          <input style={inputStyle} value={connForm.ssh_target_username || ''} onChange={e => setConnForm((f: any) => ({ ...f, ssh_target_username: e.target.value }))} placeholder="选择服务器后自动带出" />
                        </label>
                        <label className="field-label">目标主机 SSH 密码
                          <input style={inputStyle} type="password" value={connForm.ssh_target_password || ''} onChange={e => setConnForm((f: any) => ({ ...f, ssh_target_password: e.target.value }))} placeholder="手动覆盖时填写" />
                        </label>
                        <label className="field-label maintenance-span-2">目标主机 SSH 私钥
                          <div className="ssh-key-picker">
                            <select style={selectStyle} value={sshKeys.some((key: any) => key.name === connForm.ssh_target_key_path) ? connForm.ssh_target_key_path : ''} onChange={e => setConnForm((f: any) => ({ ...f, ssh_target_key_path: e.target.value }))}>
                              <option value="">选择已保存密钥...</option>
                              {sshKeys.map((key: any) => <option key={key.name} value={key.name}>{keyDisplayName(key)}</option>)}
                            </select>
                            <input style={inputStyle} value={connForm.ssh_target_key_path || ''} onChange={e => setConnForm((f: any) => ({ ...f, ssh_target_key_path: e.target.value }))} placeholder="密钥文件名或绝对路径" />
                            <div className="ssh-key-picker-actions">
                              <button type="button" className="btn btn-subtle" onClick={loadSshKeys} disabled={sshKeysLoading}>{sshKeysLoading ? '加载中...' : '刷新密钥'}</button>
                              <label className={`btn btn-subtle ${sshKeyUploading ? 'disabled' : ''}`}>{sshKeyUploading ? '上传中...' : '上传新密钥'}<input type="file" onChange={onUploadTargetSshKey} disabled={sshKeyUploading} style={{ display: 'none' }} /></label>
                            </div>
                          </div>
                        </label>
                        <label className="field-label">目标主机私钥口令
                          <input style={inputStyle} type="password" value={connForm.ssh_target_key_passphrase || ''} onChange={e => setConnForm((f: any) => ({ ...f, ssh_target_key_passphrase: e.target.value }))} />
                        </label>
                      </div>

                      <label className="field-label maintenance-span-2">数据库绑定地址
                        <input style={inputStyle} value={connForm.ssh_remote_bind_host} onChange={e => setConnForm((f: any) => ({ ...f, ssh_remote_bind_host: e.target.value }))} placeholder="默认使用上方数据库主机；目标主机视角访问数据库时若地址不同，在这里填写" />
                      </label>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="conn-drawer-footer">
          <button className="btn" onClick={onSubmit} disabled={connSubmitting} style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>
            {connSubmitting ? (connForm.id ? '更新中...' : '创建中...') : (connForm.id ? '更新连接' : '创建连接')}
          </button>
          <button className="btn" onClick={onClose} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>取消</button>
        </div>
      </div>
    </div>
  )
}

export default function ConnectionsTab(props: ConnectionsTabProps) {
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [anchorRect, setAnchorRect] = useState<{ top: number; left: number; right: number; bottom: number } | undefined>(undefined)

  // 分页状态
  const [connPage, setConnPage] = useState(1)
  const [connPageSize, setConnPageSize] = useState(12)

  // 保存列表滚动位置，编辑后恢复
  const savedScrollTop = useRef(0)

  // 获取滚动容器
  const getScrollContainer = useCallback(() => document.querySelector('.app-main'), [])

  // 保存当前滚动位置
  const saveScrollPosition = useCallback(() => {
    const container = getScrollContainer()
    if (container) savedScrollTop.current = container.scrollTop
  }, [getScrollContainer])

  // 恢复滚动位置
  const restoreScrollPosition = useCallback(() => {
    const container = getScrollContainer()
    if (container) {
      // 使用 setTimeout 确保在 DOM 更新后再恢复
      setTimeout(() => {
        container.scrollTop = savedScrollTop.current
      }, 50)
    }
  }, [getScrollContainer])

  const handleEdit = useCallback((connection: any, event?: React.MouseEvent) => {
    saveScrollPosition()
    if (props.onEdit) {
      props.onEdit(connection)
    }
    // 获取点击按钮的位置，作为弹窗的锚点
    if (event?.currentTarget) {
      const rect = event.currentTarget.getBoundingClientRect()
      setAnchorRect({
        top: rect.top,
        left: Math.max(0, rect.left - 300),
        right: rect.right,
        bottom: rect.bottom,
      })
    } else {
      setAnchorRect(undefined)
    }
    setDrawerOpen(true)
  }, [props.onEdit, saveScrollPosition])

  const handleCreate = useCallback(() => {
    saveScrollPosition()
    props.onShowCreate()
    setAnchorRect(undefined)
    setDrawerOpen(true)
  }, [props.onShowCreate, saveScrollPosition])

  const handleCloseDrawer = useCallback(() => {
    setDrawerOpen(false)
    setAnchorRect(undefined)
    props.onCloseForm()
    restoreScrollPosition()
  }, [props.onCloseForm, restoreScrollPosition])

  const handleSubmit = useCallback(() => {
    props.onSubmit()
    // onSubmit (handleConnSubmit) 是异步的，connSubmitting 会先变 true 再变 false
    // 关闭抽屉和恢复滚动位置交给 useEffect 监听 connSubmitting 变化处理
  }, [props.onSubmit])

  // 监听提交完成（connSubmitting 从 true 变为 false）时关闭抽屉并恢复滚动
  const prevSubmitting = useRef(false)
  useEffect(() => {
    if (prevSubmitting.current && !props.connSubmitting) {
      // 提交刚完成
      setDrawerOpen(false)
      setAnchorRect(undefined)
      restoreScrollPosition()
    }
    prevSubmitting.current = props.connSubmitting
  }, [props.connSubmitting, restoreScrollPosition])

  // 分页计算
  const totalConn = props.filteredConnections.length
  const connPageItems = props.filteredConnections.slice((connPage - 1) * connPageSize, connPage * connPageSize)

  // 搜索/筛选变化时重置到第一页
  useEffect(() => { setConnPage(1) }, [props.connSearch, props.connEnvFilter])

  const PaginationBar = ({ total, page, pageSize, onPageChange, onPageSizeChange }: {
    total: number
    page: number
    pageSize: number
    onPageChange: (p: number) => void
    onPageSizeChange: (s: number) => void
  }) => {
    const totalPages = Math.max(1, Math.ceil(total / pageSize))
    if (total <= pageSize) return null
    const start = (page - 1) * pageSize + 1
    const end = Math.min(page * pageSize, total)

    return (
      <div className="pagination-bar">
        <div className="pagination-info">
          显示 {start}-{end} / 共 {total} 条
        </div>
        <div className="pagination-controls">
          <label className="pagination-size-label">
            每页
            <select value={pageSize} onChange={e => { onPageSizeChange(Number(e.target.value)); onPageChange(1) }}>
              {[6, 12, 24, 48, 96].map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            条
          </label>
          <button className="pagination-btn" disabled={page <= 1} onClick={() => onPageChange(1)}>«</button>
          <button className="pagination-btn" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>‹</button>
          <span className="pagination-page">第 {page} / {totalPages} 页</span>
          <button className="pagination-btn" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>›</button>
          <button className="pagination-btn" disabled={page >= totalPages} onClick={() => onPageChange(totalPages)}>»</button>
        </div>
      </div>
    )
  }

  return (
    <div className="maintenance-workspace">
      <section className="maintenance-overview-grid">
        <div className="maintenance-stat-card">
          <span>连接总数</span>
          <strong>{props.connStats.total}</strong>
          <small>可用于 SQL 查询和受控执行</small>
        </div>
        <div className="maintenance-stat-card">
          <span>生产连接</span>
          <strong>{props.connStats.prod}</strong>
          <small>生产连接建议使用最小权限账号</small>
        </div>
        <div className="maintenance-stat-card">
          <span>SSH 代理</span>
          <strong>{props.connStats.tunnel}</strong>
          <small>支持跳板机到目标主机的两跳访问</small>
        </div>
      </section>

      <section className="maintenance-toolbar glass-card">
        <div>
          <h3>数据库连接配置</h3>
          <p>统一维护 SQL 查询、SQL 执行和数据清理使用的数据库连接，支持直连和 SSH 跳板机。</p>
        </div>
        <div className="maintenance-toolbar-actions">
          <input style={{ ...props.inputStyle, minWidth: 260 }} placeholder="搜索名称 / 主机 / 数据库..." value={props.connSearch} onChange={e => props.setConnSearch(e.target.value)} />
          <select style={{ ...props.selectStyle, width: 150 }} value={props.connEnvFilter} onChange={e => props.setConnEnvFilter(e.target.value)}>
            <option value="">全部环境</option>
            {props.envOptions.map(env => <option key={env} value={env}>{env}</option>)}
          </select>
          <button className="btn" onClick={props.loadConnections} disabled={props.connLoading} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
            {props.connLoading ? '刷新中...' : '刷新'}
          </button>
          <button className="btn" onClick={handleCreate} style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>
            添加连接
          </button>
        </div>
      </section>

      <ConnectionEditDrawer
        open={drawerOpen}
        connForm={props.connForm}
        connSubmitting={props.connSubmitting}
        bastionServers={props.bastionServers}
        bastionServersLoading={props.bastionServersLoading}
        loadBastionServers={props.loadBastionServers}
        sshKeys={props.sshKeys}
        sshKeysLoading={props.sshKeysLoading}
        sshKeyUploading={props.sshKeyUploading}
        loadSshKeys={props.loadSshKeys}
        onUploadSshKey={props.onUploadSshKey}
        onUploadTargetSshKey={props.onUploadTargetSshKey}
        inputStyle={props.inputStyle}
        selectStyle={props.selectStyle}
        setConnForm={props.setConnForm}
        onClose={handleCloseDrawer}
        onSubmit={handleSubmit}
        anchorRect={anchorRect}
      />

      {props.connLoading ? (
        <div className="glass-card empty-state"><strong>加载中...</strong><span>正在获取数据库连接列表。</span></div>
      ) : props.filteredConnections.length === 0 ? (
        <div className="glass-card empty-state"><strong>暂无匹配连接</strong><span>可以调整搜索条件，或新增数据库连接。</span></div>
      ) : (
        <>
          <section className="connection-card-grid">
            {connPageItems.map((c) => (
            <article key={c.id} className="connection-card">
              <div className="connection-card-head">
                <div>
                  <strong>{c.name}</strong>
                  <span>{c.db_type || 'mysql'} · {c.database_name}</span>
                </div>
                <Badge label={c.environment} value={c.environment === 'production' ? 'approved' : c.environment === 'staging' ? 'pending_approval' : 'draft'} />
              </div>
              <div className="connection-card-body">
                <div><span>地址</span><code>{c.host}:{c.port}</code></div>
                <div><span>用户</span><strong>{c.username || '-'}</strong></div>
                <div><span>访问方式</span><strong>{c.use_ssh_tunnel ? (c.ssh_mode === 'server' ? `服务器: ${c.ssh_server_name || '-'}` : `SSH ${c.ssh_host || ''}`) : '直连'}</strong></div>
                <div><span>DML 策略</span><strong>{c.allow_dml ? `已开启 · 上限 ${c.max_affected_rows_default || 100}` : '只读 / 禁止写入'}</strong></div>
              </div>
              <div className="connection-card-actions">
                <button className="btn" onClick={() => props.onTest(c.id)} style={{ background: 'var(--success-surface)', color: 'var(--success-soft)' }}>测试连接</button>
                {props.onEdit && (
                  <button className="btn" onClick={(e) => handleEdit(c, e)} style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>编辑</button>
                )}
                <button className="btn" onClick={() => props.onDelete(c.id, c.name)} style={{ background: 'var(--danger-surface)', color: 'var(--danger)' }}>删除</button>
              </div>
            </article>
          ))}
          </section>
          <PaginationBar
            total={totalConn}
            page={connPage}
            pageSize={connPageSize}
            onPageChange={setConnPage}
            onPageSizeChange={setConnPageSize}
          />
        </>
      )}
    </div>
  )
}
