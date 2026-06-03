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

export default function ConnectionsTab(props: ConnectionsTabProps) {
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
          <button className="btn" onClick={props.onShowCreate} style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>
            添加连接
          </button>
        </div>
      </section>

      {props.showConnForm && (
        <section className="glass-card maintenance-form-card">
          <div className="section-title-row">
            <div>
              <h2>{props.connForm.id ? '编辑数据库连接' : '新建数据库连接'}</h2>
              <p>连接信息会被 SQL 查询、SQL 执行、数据清理和表结构探测复用。生产连接建议使用最小权限账号。</p>
            </div>
            <button className="btn btn-subtle" onClick={props.onCloseForm}>收起表单</button>
          </div>

          <div className="maintenance-form-section">
            <div className="maintenance-form-title">
              <strong>基础信息</strong>
              <span>连接名称、环境和数据库类型</span>
            </div>
            <div className="maintenance-form-grid">
              <label className="field-label">名称 *
                <input style={props.inputStyle} value={props.connForm.name} onChange={e => props.setConnForm((f: any) => ({ ...f, name: e.target.value }))} placeholder="如 crypto-prod-maintenance" />
              </label>
              <label className="field-label">环境 *
                <select style={props.selectStyle} value={props.connForm.environment} onChange={e => props.setConnForm((f: any) => ({ ...f, environment: e.target.value }))}>
                  <option value="dev">dev</option>
                  <option value="staging">staging</option>
                  <option value="production">production</option>
                </select>
              </label>
              <label className="field-label">数据库类型
                <select style={props.selectStyle} value={props.connForm.db_type} onChange={e => props.setConnForm((f: any) => ({ ...f, db_type: e.target.value }))}>
                  <option value="mysql">mysql</option>
                  <option value="postgresql">postgresql</option>
                  <option value="mariadb">mariadb</option>
                </select>
              </label>
            </div>
          </div>

          <div className="maintenance-form-section">
            <div className="maintenance-form-title">
              <strong>数据库访问</strong>
              <span>填写应用侧可访问的数据库地址和维护账号</span>
            </div>
            <div className="maintenance-form-grid maintenance-form-grid--wide">
              <label className="field-label">主机 *
                <input style={props.inputStyle} value={props.connForm.host} onChange={e => props.setConnForm((f: any) => ({ ...f, host: e.target.value }))} placeholder="127.0.0.1 / db.internal" />
              </label>
              <label className="field-label">端口
                <input style={props.inputStyle} type="number" value={props.connForm.port} onChange={e => props.setConnForm((f: any) => ({ ...f, port: Number(e.target.value) }))} />
              </label>
              <label className="field-label">数据库名 *
                <input style={props.inputStyle} value={props.connForm.database_name} onChange={e => props.setConnForm((f: any) => ({ ...f, database_name: e.target.value }))} />
              </label>
              <label className="field-label">用户名
                <input style={props.inputStyle} value={props.connForm.username} onChange={e => props.setConnForm((f: any) => ({ ...f, username: e.target.value }))} />
              </label>
              <label className="field-label">密码
                <input style={props.inputStyle} type="password" value={props.connForm.password} onChange={e => props.setConnForm((f: any) => ({ ...f, password: e.target.value }))} />
              </label>
            </div>
          </div>

          <div className={`maintenance-form-section ${props.connForm.allow_dml ? 'maintenance-form-section--active' : ''}`}>
            <div className="maintenance-form-title maintenance-form-title--inline">
              <div>
                <strong>DML 执行策略</strong>
                <span>默认关闭。仅对维护账号显式开放 INSERT / UPDATE / DELETE，并使用影响行数保护。</span>
              </div>
              <label className="switch-line">
                <input type="checkbox" checked={Boolean(props.connForm.allow_dml)} onChange={e => props.setConnForm((f: any) => ({ ...f, allow_dml: e.target.checked }))} />
                允许受控 DML
              </label>
            </div>
            {props.connForm.allow_dml && (
              <div className="maintenance-form-grid maintenance-form-grid--wide">
                <label className="field-label">允许语句类型
                  <select multiple style={{ ...props.selectStyle, minHeight: 90 }} value={props.connForm.allowed_dml_types || []} onChange={e => props.setConnForm((f: any) => ({ ...f, allowed_dml_types: Array.from(e.target.selectedOptions).map(o => o.value) }))}>
                    <option value="insert">INSERT</option>
                    <option value="update">UPDATE</option>
                    <option value="delete">DELETE</option>
                  </select>
                </label>
                <label className="field-label">最大影响行数
                  <input style={props.inputStyle} type="number" min={1} max={1000} value={props.connForm.max_affected_rows_default} onChange={e => props.setConnForm((f: any) => ({ ...f, max_affected_rows_default: Number(e.target.value) }))} />
                </label>
                <label className="field-label maintenance-span-2">允许表白名单（可选，逗号或换行分隔）
                  <textarea style={{ ...props.inputStyle, minHeight: 70 }} value={props.connForm.allowed_tables_text} onChange={e => props.setConnForm((f: any) => ({ ...f, allowed_tables_text: e.target.value }))} placeholder="为空表示不限制；建议生产连接明确填写" />
                </label>
                <label className="field-label maintenance-span-2">禁止表黑名单（可选，逗号或换行分隔）
                  <textarea style={{ ...props.inputStyle, minHeight: 70 }} value={props.connForm.blocked_tables_text} onChange={e => props.setConnForm((f: any) => ({ ...f, blocked_tables_text: e.target.value }))} placeholder="例如 users, permissions, audit_logs" />
                </label>
                <label className="switch-line maintenance-span-2">
                  <input type="checkbox" checked={Boolean(props.connForm.require_dml_reason)} onChange={e => props.setConnForm((f: any) => ({ ...f, require_dml_reason: e.target.checked }))} />
                  执行 DML 必须填写原因
                </label>
              </div>
            )}
          </div>

          <div className={`maintenance-form-section ${props.connForm.use_ssh_tunnel ? 'maintenance-form-section--active' : ''}`}>
            <div className="maintenance-form-title maintenance-form-title--inline">
              <div>
                <strong>SSH 跳板机</strong>
                <span>支持从服务器资产选择或手动配置跳板机。</span>
              </div>
              <label className="switch-line">
                <input type="checkbox" checked={props.connForm.use_ssh_tunnel} onChange={e => props.setConnForm((f: any) => ({ ...f, use_ssh_tunnel: e.target.checked }))} />
                启用代理
              </label>
            </div>
            {props.connForm.use_ssh_tunnel && (
              <div className="maintenance-form-grid maintenance-form-grid--wide">
                {/* SSH 模式切换 */}
                <label className="field-label maintenance-span-2">配置方式
                  <div className="inline-control-row">
                    <button
                      type="button"
                      className={`btn ${props.connForm.ssh_mode === 'server' ? 'btn-primary' : 'btn-subtle'}`}
                      onClick={() => {
                        props.setConnForm((f: any) => ({ ...f, ssh_mode: 'server' }))
                      }}
                    >
                      从服务器选择
                    </button>
                    <button
                      type="button"
                      className={`btn ${props.connForm.ssh_mode === 'manual' ? 'btn-primary' : 'btn-subtle'}`}
                      onClick={() => {
                        props.setConnForm((f: any) => ({ ...f, ssh_mode: 'manual', ssh_server_name: '' }))
                      }}
                    >
                      手动配置
                    </button>
                  </div>
                </label>

                {/* 服务器选择模式 */}
                {props.connForm.ssh_mode === 'server' && (
                  <label className="field-label maintenance-span-2">从服务器资产选择跳板机
                    <div className="inline-control-row">
                      <select
                        style={props.selectStyle}
                        value={props.connForm.ssh_server_name || ''}
                        onChange={e => {
                          const name = e.target.value
                          const selected = props.bastionServers.find((srv: any) => srv.name === name)
                          applyBastionServer(selected, props.setConnForm)
                        }}
                      >
                        <option value="">请选择服务器...</option>
                        {props.bastionServers.length === 0 && (
                          <option value="" disabled>{props.bastionServersLoading ? '正在加载服务器资产...' : '暂无可选服务器，请点击刷新或先到服务器模块新增'}</option>
                        )}
                        {props.bastionServers.map((server: any) => (
                          <option key={server.name || server.host} value={server.name}>{serverDisplayName(server)} · {serverSourceLabel(server)}</option>
                        ))}
                      </select>
                      <button type="button" className="btn btn-subtle" onClick={props.loadBastionServers} disabled={props.bastionServersLoading}>
                        {props.bastionServersLoading ? '加载中...' : '刷新服务器'}
                      </button>
                    </div>
                    <small className="field-hint">选择后会自动复用服务器资产中的主机、端口、用户名和认证信息。</small>
                  </label>
                )}

                {/* 手动配置模式 */}
                {props.connForm.ssh_mode === 'manual' && (
                  <>
                    <label className="field-label">跳板机主机 *
                      <input style={props.inputStyle} value={props.connForm.ssh_host} onChange={e => props.setConnForm((f: any) => ({ ...f, ssh_host: e.target.value }))} placeholder="如 192.168.1.100" />
                    </label>
                    <label className="field-label">跳板机端口
                      <input style={props.inputStyle} type="number" value={props.connForm.ssh_port} onChange={e => props.setConnForm((f: any) => ({ ...f, ssh_port: Number(e.target.value) }))} />
                    </label>
                    <label className="field-label">SSH 用户名 *
                      <input style={props.inputStyle} value={props.connForm.ssh_username} onChange={e => props.setConnForm((f: any) => ({ ...f, ssh_username: e.target.value }))} />
                    </label>
                    <label className="field-label">SSH 密码
                      <input style={props.inputStyle} type="password" value={props.connForm.ssh_password} onChange={e => props.setConnForm((f: any) => ({ ...f, ssh_password: e.target.value }))} placeholder="密码认证时填写" />
                    </label>
                    <label className="field-label maintenance-span-2">SSH 私钥
                      <div className="ssh-key-picker">
                        <select
                          style={props.selectStyle}
                          value={props.sshKeys.some((key: any) => key.name === props.connForm.ssh_key_path) ? props.connForm.ssh_key_path : ''}
                          onChange={e => props.setConnForm((f: any) => ({ ...f, ssh_key_path: e.target.value }))}
                        >
                          <option value="">选择已保存密钥...</option>
                          {props.sshKeys.map((key: any) => (
                            <option key={key.name} value={key.name}>{keyDisplayName(key)}</option>
                          ))}
                        </select>
                        <input
                          style={props.inputStyle}
                          value={props.connForm.ssh_key_path}
                          onChange={e => props.setConnForm((f: any) => ({ ...f, ssh_key_path: e.target.value }))}
                          placeholder="密钥文件名或绝对路径"
                        />
                        <div className="ssh-key-picker-actions">
                          <button type="button" className="btn btn-subtle" onClick={props.loadSshKeys} disabled={props.sshKeysLoading}>
                            {props.sshKeysLoading ? '加载中...' : '刷新密钥'}
                          </button>
                          <label className={`btn btn-subtle ${props.sshKeyUploading ? 'disabled' : ''}`}>
                            {props.sshKeyUploading ? '上传中...' : '上传新密钥'}
                            <input type="file" onChange={props.onUploadSshKey} disabled={props.sshKeyUploading} style={{ display: 'none' }} />
                          </label>
                          <a className="btn btn-subtle" href="/servers" target="_blank" rel="noreferrer">密钥管理</a>
                        </div>
                      </div>
                      <small className="field-hint">可选：选择已保存密钥、上传新密钥文件，或手动填写路径。</small>
                    </label>
                    <label className="field-label">私钥口令
                      <input style={props.inputStyle} type="password" value={props.connForm.ssh_key_passphrase} onChange={e => props.setConnForm((f: any) => ({ ...f, ssh_key_passphrase: e.target.value }))} />
                    </label>
                  </>
                )}

                {/* 目标主机（第二跳）*/}
                <div className="field-label maintenance-span-2" style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border-strong)' }}>
                  <strong>目标主机（第二跳）</strong>
                  <span style={{ display: 'block', color: 'var(--text-secondary)', fontSize: 12, marginBottom: 8 }}>如需从跳板机再 SSH 到目标主机访问数据库，请配置下方选项。</span>
                  <label className="field-label maintenance-span-2">从服务器资产选择目标主机
                    <div className="inline-control-row">
                      <select
                        style={props.selectStyle}
                        value={props.connForm.ssh_target_server_name || ''}
                        onChange={e => {
                          const name = e.target.value
                          const selected = props.bastionServers.find((srv: any) => srv.name === name)
                          applyTargetServer(selected, props.setConnForm)
                        }}
                      >
                        <option value="">不使用第二跳 / 手动填写...</option>
                        {props.bastionServers.length === 0 && (
                          <option value="" disabled>{props.bastionServersLoading ? '正在加载服务器资产...' : '暂无可选服务器'}</option>
                        )}
                        {props.bastionServers.map((server: any) => (
                          <option key={server.name || server.host} value={server.name}>{serverDisplayName(server)} · {serverSourceLabel(server)}</option>
                        ))}
                      </select>
                      <button type="button" className="btn btn-subtle" onClick={props.loadBastionServers} disabled={props.bastionServersLoading}>
                        {props.bastionServersLoading ? '加载中...' : '刷新服务器'}
                      </button>
                    </div>
                  </label>
                  <label className="field-label">目标主机地址
                    <input style={props.inputStyle} value={props.connForm.ssh_target_host || ''} onChange={e => props.setConnForm((f: any) => ({ ...f, ssh_target_host: e.target.value }))} placeholder="选择服务器后自动带出，可手动覆盖" />
                  </label>
                  <label className="field-label">目标主机端口
                    <input style={props.inputStyle} type="number" value={props.connForm.ssh_target_port || 22} onChange={e => props.setConnForm((f: any) => ({ ...f, ssh_target_port: Number(e.target.value) }))} />
                  </label>
                  <label className="field-label">目标主机 SSH 用户名
                    <input style={props.inputStyle} value={props.connForm.ssh_target_username || ''} onChange={e => props.setConnForm((f: any) => ({ ...f, ssh_target_username: e.target.value }))} placeholder="选择服务器后自动带出" />
                  </label>
                  <label className="field-label">目标主机 SSH 密码
                    <input style={props.inputStyle} type="password" value={props.connForm.ssh_target_password || ''} onChange={e => props.setConnForm((f: any) => ({ ...f, ssh_target_password: e.target.value }))} placeholder="手动覆盖时填写" />
                  </label>
                  <label className="field-label maintenance-span-2">目标主机 SSH 私钥
                    <div className="ssh-key-picker">
                      <select
                        style={props.selectStyle}
                        value={props.sshKeys.some((key: any) => key.name === props.connForm.ssh_target_key_path) ? props.connForm.ssh_target_key_path : ''}
                        onChange={e => props.setConnForm((f: any) => ({ ...f, ssh_target_key_path: e.target.value }))}
                      >
                        <option value="">选择已保存密钥...</option>
                        {props.sshKeys.map((key: any) => (
                          <option key={key.name} value={key.name}>{keyDisplayName(key)}</option>
                        ))}
                      </select>
                      <input
                        style={props.inputStyle}
                        value={props.connForm.ssh_target_key_path || ''}
                        onChange={e => props.setConnForm((f: any) => ({ ...f, ssh_target_key_path: e.target.value }))}
                        placeholder="密钥文件名或绝对路径"
                      />
                      <div className="ssh-key-picker-actions">
                        <button type="button" className="btn btn-subtle" onClick={props.loadSshKeys} disabled={props.sshKeysLoading}>
                          {props.sshKeysLoading ? '加载中...' : '刷新密钥'}
                        </button>
                        <label className={`btn btn-subtle ${props.sshKeyUploading ? 'disabled' : ''}`}>
                          {props.sshKeyUploading ? '上传中...' : '上传新密钥'}
                          <input type="file" onChange={props.onUploadTargetSshKey} disabled={props.sshKeyUploading} style={{ display: 'none' }} />
                        </label>
                      </div>
                    </div>
                  </label>
                  <label className="field-label">目标主机私钥口令
                    <input style={props.inputStyle} type="password" value={props.connForm.ssh_target_key_passphrase || ''} onChange={e => props.setConnForm((f: any) => ({ ...f, ssh_target_key_passphrase: e.target.value }))} />
                  </label>
                </div>

                <label className="field-label maintenance-span-2">数据库绑定地址
                  <input style={props.inputStyle} value={props.connForm.ssh_remote_bind_host} onChange={e => props.setConnForm((f: any) => ({ ...f, ssh_remote_bind_host: e.target.value }))} placeholder="默认使用上方数据库主机；目标主机视角访问数据库时若地址不同，在这里填写" />
                </label>
              </div>
            )}
          </div>

          <div className="maintenance-form-actions">
            <button className="btn" onClick={props.onSubmit} disabled={props.connSubmitting} style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>
              {props.connSubmitting ? (props.connForm.id ? '更新中...' : '创建中...') : (props.connForm.id ? '更新连接' : '创建连接')}
            </button>
            <button className="btn" onClick={props.onCloseForm} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
              取消
            </button>
          </div>
        </section>
      )}

      {props.connLoading ? (
        <div className="glass-card empty-state"><strong>加载中...</strong><span>正在获取数据库连接列表。</span></div>
      ) : props.filteredConnections.length === 0 ? (
        <div className="glass-card empty-state"><strong>暂无匹配连接</strong><span>可以调整搜索条件，或新增数据库连接。</span></div>
      ) : (
        <section className="connection-card-grid">
          {props.filteredConnections.map((c) => (
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
                  <button className="btn" onClick={() => props.onEdit!(c)} style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>编辑</button>
                )}
                <button className="btn" onClick={() => props.onDelete(c.id, c.name)} style={{ background: 'var(--danger-surface)', color: 'var(--danger)' }}>删除</button>
              </div>
            </article>
          ))}
        </section>
      )}
    </div>
  )
}
