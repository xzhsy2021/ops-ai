import { useState, useEffect, useRef, useCallback, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { serverManagement, adminMaintenance } from '../api'
import { RiskConfirmDialog } from '../components/ui'
import { RiskActionGuard } from '../components/RiskActionGuard'
import type { RiskActionResult } from '../components/RiskActionGuard'
import { EnhancedDataTable } from '../components/EnhancedDataTable'
import { ROUTES } from '../routes'
import type { EnhancedColumn } from '../components/EnhancedDataTable'
import { useCachedResource } from '../hooks/useCachedResource'
import { formatTime } from '../utils/datetime.js'

const GROUP_COLORS = [
  { bg: 'var(--brand-surface)', text: 'var(--action-text)' },
  { bg: 'var(--success-surface)', text: 'var(--success)' },
  { bg: 'var(--warning-surface)', text: 'var(--warning)' },
  { bg: 'var(--purple-surface)', text: 'var(--purple-text)' },
  { bg: 'var(--danger-surface)', text: 'var(--danger)' },
  { bg: 'var(--teal-surface)', text: 'var(--teal-text)' },
  { bg: 'var(--purple-surface)', text: 'var(--purple)' },
  { bg: 'var(--warning-surface)', text: 'var(--warning)' },
]

function getGroupColor(name: string) {
  let hash = 0
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash)
  return GROUP_COLORS[Math.abs(hash) % GROUP_COLORS.length]
}

interface KeyMeta {
  name: string
  size: number
  modified: number
}

interface ServerRow {
  name: string
  host: string
  port: number
  username?: string
  user?: string
  auth_type?: string
  key_content?: string
  key?: string
  key_file?: string
  group?: string
  jump_host?: string | { name: string }
  description?: string
  status?: string
  enabled?: boolean
  config_status?: { status?: string; complete?: boolean; missing?: string[]; suggestions?: string[] }
  health_probe?: { status?: string; disk?: { available_kb: number } }
  security_monitor?: {
    enabled?: boolean
    source?: string
    enabled_at?: string
    updated_at?: string
    installed?: boolean
    fail2ban_active?: boolean
    auditd_active?: boolean
    installer_present?: boolean
    checked_at?: string
  }
}

interface ServerForm {
  name: string
  host: string
  port: number
  username: string
  auth_type: string
  password: string
  key: string
  key_content: string
  jump_host: string
  description: string
  tags: string
  group: string
  sftp_allowed_roots: string
  status: string
}

interface BatchForm {
  description: string
  tags: string
  jump_host: string
  username: string
  auth_type: string
  port: string
  group: string
  status: string
}


function normalizeServerStatus(s: any): 'online' | 'disabled' | 'offline' {
  const raw = String(s?.status || '').toLowerCase()
  if (s?.enabled === false || ['disabled', 'inactive', 'off', '停用'].includes(raw)) return 'disabled'
  if (['offline', '离线'].includes(raw)) return 'offline'
  return 'online'
}

function serverStatusChipClass(status: string) {
  if (status === 'disabled') return 'cc-chip cc-chip--ghost'
  if (status === 'offline') return 'cc-chip cc-chip--danger'
  return 'cc-chip cc-chip--ok'
}

function serverStatusLabel(status: string) {
  if (status === 'disabled') return '停用'
  if (status === 'offline') return '离线'
  return '在线'
}

function authModeChipClass(mode: string) {
  if (mode === 'password') return 'cc-chip cc-chip--warn'
  if (mode === 'key_file') return 'cc-chip cc-chip--ok'
  return 'cc-chip cc-chip--info'
}

function cfgChipClass(cfgStatus: string) {
  if (cfgStatus === 'blocked') return 'cc-chip cc-chip--danger'
  if (cfgStatus === 'warning') return 'cc-chip cc-chip--warn'
  return 'cc-chip cc-chip--ok'
}

const emptyForm: ServerForm = {
  name: '', host: '', port: 22, username: 'root', auth_type: 'password',
  password: '', key: '', key_content: '', jump_host: '', description: '', tags: '', group: '',
  sftp_allowed_roots: '/data, /opt, /var/log, /tmp',
  status: 'online',
}

const emptyBatchForm: BatchForm = {
  description: '', tags: '', jump_host: '', username: '', auth_type: '', port: '', group: '', status: '',
}

/**
 * 操作列图标按钮：用 React Portal 渲染 tooltip，避免被 sticky 末列、stacking context、overflow 裁剪遮挡
 * - position: fixed + z-index 10000 强制浮在所有元素之上
 * - 动态计算 button 位置；底部空间不足时翻转到上方
 * - 鼠标移入/键盘聚焦时显示，移出/失焦时关闭
 */
function ActionButton({
  tip,
  onClick,
  variant,
  disabled,
  children,
}: {
  tip: string
  onClick: (e: React.MouseEvent) => void
  variant?: 'success' | 'danger'
  disabled?: boolean
  children: ReactNode
}) {
  const ref = useRef<HTMLButtonElement | null>(null)
  const [show, setShow] = useState(false)
  const [placement, setPlacement] = useState<'below' | 'above'>('below')
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 })

  const updatePos = useCallback(() => {
    const el = ref.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const TIP_H = 30
    const above = rect.bottom + TIP_H > window.innerHeight && rect.top - TIP_H > 0
    setPlacement(above ? 'above' : 'below')
    setPos({
      top: above ? rect.top - 6 : rect.bottom + 6,
      left: rect.left + rect.width / 2,
    })
  }, [])

  const handleEnter = useCallback(() => {
    updatePos()
    setShow(true)
  }, [updatePos])

  const handleLeave = useCallback(() => setShow(false), [])

  const cls = `cc-icon-btn${variant === 'success' ? ' cc-icon-btn--success' : ''}${variant === 'danger' ? ' cc-icon-btn--danger' : ''}`

  return (
    <>
      <button
        ref={ref}
        className={cls}
        onClick={onClick}
        disabled={disabled}
        onMouseEnter={handleEnter}
        onMouseLeave={handleLeave}
        onFocus={handleEnter}
        onBlur={handleLeave}
        data-stop-row-click
      >
        {children}
      </button>
      {show &&
        createPortal(
          <div
            className={`cc-tooltip-portal${placement === 'above' ? ' cc-tooltip-portal--above' : ''}`}
            style={{ top: pos.top, left: pos.left }}
            role="tooltip"
          >
            {tip}
          </div>,
          document.body,
        )}
    </>
  )
}

export default function ServerListPage() {
  const navigate = useNavigate()
  const [authFilter, setAuthFilter] = useState('') // K1
  const [monitorFilter, setMonitorFilter] = useState('') // K2
  // TOPSTATES

  const [servers, setServers] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  // 分页状态
  const [serverPage, setServerPage] = useState(1)
  const [serverPageSize, setServerPageSize] = useState(20)

  const [showModal, setShowModal] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  const [form, setForm] = useState<ServerForm>({ ...emptyForm })
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState('')

  const [uploadingKey, setUploadingKey] = useState(false)
  const [uploadedKeyName, setUploadedKeyName] = useState('')
  const [sshKeys, setSshKeys] = useState<KeyMeta[]>([])
  const [showKeyManager, setShowKeyManager] = useState(false)
  const [keyEditingName, setKeyEditingName] = useState<string | null>(null)
  const [keyFormName, setKeyFormName] = useState('')
  const [keyFormContent, setKeyFormContent] = useState('')
  const [keySaving, setKeySaving] = useState(false)
  const [keyError, setKeyError] = useState('')

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set())
  const [showBatchModal, setShowBatchModal] = useState(false)
  const [batchForm, setBatchForm] = useState<BatchForm>({ ...emptyBatchForm })
  const [batchSaving, setBatchSaving] = useState(false)
  const [batchError, setBatchError] = useState('')

  const groupsResource = useCachedResource<any[]>({
    fetcher: async () => {
      const res: any = await serverManagement.groups.list()
      return res.data || []
    },
    key: 'server-groups',
    ttlMs: 60000,
  })
  const groups = groupsResource.data || []
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null)
  const [dragOverGroup, setDragOverGroup] = useState<string | null>(null)
  const [groupActionsOpen, setGroupActionsOpen] = useState<string | null>(null)
  const [configFilter, setConfigFilter] = useState<'all' | 'passed' | 'warning' | 'blocked'>('all')
  const [serverStatusFilter, setServerStatusFilter] = useState<'all' | 'online' | 'disabled' | 'offline'>('all')
  const [serverFilter, setServerFilter] = useState<string>('')
  const [healthChecking, setHealthChecking] = useState<Record<string, boolean>>({})
  const [riskAction, setRiskAction] = useState<{
    title: string
    description?: string
    target: string
    confirmText: string
    confirmButtonLabel?: string
    riskLevel?: string
    details?: Array<{ label: string; value: React.ReactNode }>
    result?: RiskActionResult | null
    onConfirm: () => Promise<RiskActionResult | void> | RiskActionResult | void
  } | null>(null)

  const flash = (msg: string, isError = false) => {
    if (isError) { setError(msg); setTimeout(() => setError(''), 5000) }
    else { setSuccess(msg); setTimeout(() => setSuccess(''), 3000) }
  }

  const load = async (skipLoading = false) => {
const scrollEl = document.querySelector('.table-scroll') || document.querySelector('.enhanced-table-wrap')
  const prevScrollTop = scrollEl?.scrollTop || 0
    if (!skipLoading) setLoading(true)
    setError('')
    try {
      const res: any = await serverManagement.list()
      setServers(res.data || [])
requestAnimationFrame(() => {
          const el = document.querySelector('.table-scroll') || document.querySelector('.enhanced-table-wrap')
          if (el) el.scrollTop = prevScrollTop
        })
    } catch (e: any) {
      flash(typeof e === 'string' ? e : e?.message || '加载失败', true)
    }
    if (!skipLoading) setLoading(false)
requestAnimationFrame(() => {
        const el = document.querySelector('.table-scroll') || document.querySelector('.enhanced-table-wrap')
        if (el) el.scrollTop = prevScrollTop
      })
  }

  const loadGroups = () => { groupsResource.refresh(true) }

  const loadKeys = async () => {
    try {
      const res: any = await adminMaintenance.sshKeys.list()
      setSshKeys(res.data || [])
    } catch (_e: any) {
      setSshKeys([])
    }
  }

  useEffect(() => { load(); loadKeys() }, [])

  const groupNames = groups
    .filter((g) => !g.is_default)
    .map((g) => g.name)

  const groupedServers = selectedGroup === null
    ? servers
    : selectedGroup === ''
      ? servers.filter((s) => !s.group)
      : servers.filter((s) => s.group === selectedGroup)

  const filteredByName = !serverFilter
    ? groupedServers
    : groupedServers.filter((s) => {
        const q = serverFilter.toLowerCase()
        return (s.name || '').toLowerCase().includes(q)
          || (s.host || '').toLowerCase().includes(q)
          || (s.description || '').toLowerCase().includes(q)
      })

  const byStatusServers = serverStatusFilter === 'all'
    ? filteredByName
    : filteredByName.filter((s) => normalizeServerStatus(s) === serverStatusFilter)

  const filteredServers = configFilter === 'all'
    ? byStatusServers
    : byStatusServers.filter((s) => (s.config_status?.status || (s.config_status?.complete ? 'passed' : 'blocked')) === configFilter)

  const monitorStateOf = (s: any) => {
    const mon = s.security_monitor || {}
    const probed = typeof mon.installed === 'boolean'
    const installed = Boolean(mon.installed)
    const enabled = Boolean(mon.enabled)
    return !probed ? '未探测' : !installed ? '未安装' : enabled ? '已启用' : '未启用'
  }
  const monitorFilteredServers = monitorFilter === '' ? filteredServers : filteredServers.filter((s) => monitorStateOf(s) === monitorFilter)
  const authModeOf = (s: any) => s.auth_type || (s.key_content ? 'key_content' : (s.key || s.key_file) ? 'key_file' : 'password')
  const tableRows = authFilter === '' ? monitorFilteredServers : monitorFilteredServers.filter((s) => authModeOf(s) === authFilter)

  // 筛选/页大小变化时重置并夹紧当前页码（覆盖所有筛选条件，含表头筛选）
  useEffect(() => {
    setServerPage((p) => {
      const totalPages = Math.max(1, Math.ceil(tableRows.length / serverPageSize))
      return Math.min(Math.max(1, p), totalPages)
    })
  }, [selectedGroup, configFilter, serverStatusFilter, serverFilter, authFilter, monitorFilter, serverPageSize, tableRows.length])

  const statusCounts = servers.reduce((acc: Record<string, number>, s: any) => {
    const status = normalizeServerStatus(s)
    acc[status] = (acc[status] || 0) + 1
    return acc
  }, { online: 0, disabled: 0, offline: 0 })

  const configCounts = servers.reduce((acc: Record<string, number>, s: any) => {
    const status = s.config_status?.status || (s.config_status?.complete ? 'passed' : 'blocked')
    acc[status] = (acc[status] || 0) + 1
    return acc
  }, { passed: 0, warning: 0, blocked: 0 })

  const openCreate = () => {
    setEditing(null)
    setForm({ ...emptyForm })
    setFormError('')
    setUploadedKeyName('')
    setShowModal(true)
  }

  const openEdit = (s: any) => {
    setEditing(s.name)
    const jumpHost = typeof s.jump_host === 'string' ? s.jump_host : s.jump_host?.name || ''
    const keyPath = s.key || s.key_file || ''
    const rawRoots = s.sftp_allowed_roots ?? s.allowed_roots ?? s.file_roots ?? ''
    const rootsText = Array.isArray(rawRoots)
      ? rawRoots.join(', ')
      : (typeof rawRoots === 'string' ? rawRoots : '')
    setForm({
      name: s.name || '',
      host: s.host || '',
      port: s.port || 22,
      username: s.username || s.user || 'root',
      auth_type: s.auth_type || 'password',
      password: s.password || '',
      key: keyPath,
      key_content: s.key_content || '',
      jump_host: jumpHost,
      description: s.description || '',
      tags: Array.isArray(s.tags) ? s.tags.join(', ') : (s.tags || ''),
      group: s.group || '',
      sftp_allowed_roots: rootsText || '/data, /opt, /var/log, /tmp',
      status: normalizeServerStatus(s),
    })
    setUploadedKeyName(keyPath ? keyPath.split('/').pop() || keyPath : '')
    setFormError('')
    setShowModal(true)
  }

  const doDeleteServer = async (s: any): Promise<RiskActionResult> => {
    try {
      // 1. 先从分组中解绑（避免 "referenced by server_group/xxx" 409 报错）
      if (s.group) {
        try {
          await serverManagement.update(s.name, { group: '' })
        } catch (ungroupErr: any) {
          // 如果 update 接口不支持置空 group，则忽略继续删除（后端会兜底校验）
          console.warn('unbind group failed, continue with delete:', ungroupErr)
        }
      }
      // 2. 删除服务器
      await serverManagement.delete(s.name)
      flash(`已删除: ${s.name}`)
      setSelected((prev) => { const next = new Set(prev); next.delete(s.name); return next })
      load()
      return {
        success: true,
        message: `服务器 ${s.name} 已删除`,
        auditId: `srv-delete-${s.name}`,
        links: [
          { label: '查看审计', to: ROUTES.audit, tone: 'brand' },
          { label: '服务器列表', to: ROUTES.servers, tone: 'neutral' },
        ],
      }
    } catch (e: any) {
      const msg = typeof e === 'string' ? e : '删除失败'
      flash(msg, true)
      return { success: false, message: msg }
    }
  }

  const handleKeyUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploadingKey(true)
    try {
      const res: any = await adminMaintenance.uploadKey(file)
      updateField('key', res.data.name || res.data.path)
      setUploadedKeyName(res.data.name || res.data.filename || file.name)
      await loadKeys()
      flash('密钥已上传，可在密钥管理中继续编辑')
    } catch (err: any) {
      flash(typeof err === 'string' ? err : '密钥上传失败', true)
    }
    setUploadingKey(false)
    e.target.value = ''
  }

  const handleKeyContentRead = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      updateField('key_content', reader.result as string)
      setUploadedKeyName(file.name)
    }
    reader.onerror = () => flash('文件读取失败', true)
    reader.readAsText(file)
    e.target.value = ''
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setFormError('')
    if (!form.name.trim()) { setFormError('名称不能为空'); return }
    if (!form.host.trim()) { setFormError('主机地址不能为空'); return }
    setSaving(true)
    try {
      const payload: any = {
        name: form.name.trim(),
        host: form.host.trim(),
        port: form.port || 22,
        username: form.username || 'root',
        auth_type: form.auth_type || 'password',
        description: form.description,
        tags: form.tags ? form.tags.split(',').map((t: string) => t.trim()).filter(Boolean) : [],
        group: form.group || '',
        status: form.status || 'online',
        sftp_allowed_roots: form.sftp_allowed_roots
          ? form.sftp_allowed_roots.split(/[,\n]/).map((r: string) => r.trim()).filter(Boolean)
          : [],
      }
      if (form.auth_type === 'password') {
        payload.password = form.password || null
      } else if (form.auth_type === 'key_file') {
        payload.key = form.key || null
      } else if (form.auth_type === 'key_content') {
        payload.key_content = form.key_content || null
      }
      const selectedSrv = form.jump_host ? servers.find((s) => s.name === form.jump_host) : undefined
      if (selectedSrv) {
        const jumpHostConfig: Record<string, any> = {
          name: selectedSrv.name,
          host: selectedSrv.host,
          port: selectedSrv.port,
          username: selectedSrv.username || selectedSrv.user,
        }
        if (selectedSrv.key || selectedSrv.key_file) {
          jumpHostConfig.key = selectedSrv.key || selectedSrv.key_file
        }
        if (selectedSrv.password) {
          jumpHostConfig.password = selectedSrv.password
        }
        if (selectedSrv.key_content) {
          jumpHostConfig.key_content = selectedSrv.key_content
        }
        payload.jump_host = jumpHostConfig
      } else {
        payload.jump_host = ''
      }
      if (editing) {
        await serverManagement.update(editing, payload)
        flash(`已更新: ${form.name}`)
      } else {
        await serverManagement.create(payload)
        flash(`已创建: ${form.name}`)
      }
      setShowModal(false)
      load()
      loadGroups()
    } catch (e: any) {
      setFormError(typeof e === 'string' ? e : '保存失败')
    }
    setSaving(false)
  }

  const updateField = (field: keyof ServerForm, value: any) => {
    setForm((prev) => ({ ...prev, [field]: value }))
  }

  const openBatchEdit = () => {
    setBatchForm({ ...emptyBatchForm })
    setBatchError('')
    setShowBatchModal(true)
  }

  const handleBatchSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBatchError('')
    const names = Array.from(selected)
    if (names.length < 2) { setBatchError('至少选择 2 台服务器'); return }

    const updates: Record<string, any> = {}
    if (batchForm.description.trim()) updates.description = batchForm.description.trim()
    if (batchForm.tags.trim()) updates.tags = batchForm.tags.split(',').map((t) => t.trim()).filter(Boolean)
    if (batchForm.jump_host === '__clear__') {
      updates.jump_host = '__clear__'
    } else if (batchForm.jump_host) {
      const jumpSrv = servers.find((s) => s.name === batchForm.jump_host)
      if (jumpSrv) {
        const jumpCfg: Record<string, any> = { name: jumpSrv.name, host: jumpSrv.host, port: jumpSrv.port, username: jumpSrv.username || jumpSrv.user }
        if (jumpSrv.key || jumpSrv.key_file) jumpCfg.key = jumpSrv.key || jumpSrv.key_file
        if (jumpSrv.password) jumpCfg.password = jumpSrv.password
        if (jumpSrv.key_content) jumpCfg.key_content = jumpSrv.key_content
        updates.jump_host = jumpCfg
      }
    }
    if (batchForm.username.trim()) updates.username = batchForm.username.trim()
    if (batchForm.auth_type) updates.auth_type = batchForm.auth_type
    if (batchForm.port) updates.port = parseInt(batchForm.port, 10)
    if (batchForm.status) updates.status = batchForm.status
    if (batchForm.group === '__clear__') {
      updates.group = ''
    } else if (batchForm.group) {
      updates.group = batchForm.group
    }

    if (Object.keys(updates).length === 0) { setBatchError('至少填写一项要修改的字段'); return }

    setRiskAction({
      title: '批量编辑服务器',
      description: '即将对多台服务器资产进行批量修改，请确认目标数量和变更字段。',
      target: `${names.length} 台服务器`,
      confirmText: `BATCH_UPDATE_SERVERS ${names.length}`,
      confirmButtonLabel: '执行批量编辑',
      riskLevel: 'high',
      details: [
        { label: '服务器', value: names.join(', ') },
        { label: '变更字段', value: Object.keys(updates).join(', ') },
      ],
      onConfirm: async () => {
        setBatchSaving(true)
        try {
          const res: any = await serverManagement.batchUpdate(names, updates)
          const result = res.data || res
          const updatedCount = result.updated?.length || 0
          const failedCount = result.failed?.length || 0

          let msg = `批量编辑完成: ${updatedCount} 台成功`
          if (failedCount > 0) {
            msg += `, ${failedCount} 台失败`
            const failedNames = result.failed.map((f: any) => f.name).join(', ')
            flash(`${msg} (失败: ${failedNames})`, true)
          } else {
            flash(msg)
          }
          setShowBatchModal(false)
          setSelected(new Set())
          load()
          loadGroups()
        } catch (e: any) {
          setBatchError(typeof e === 'string' ? e : '批量编辑失败')
        }
        setBatchSaving(false)
      },
    })
  }

  const updateBatchField = (field: keyof BatchForm, value: any) => {
    setBatchForm((prev) => ({ ...prev, [field]: value }))
  }

  const openKeyCreate = () => {
    setKeyEditingName(null)
    setKeyFormName('')
    setKeyFormContent('')
    setKeyError('')
    setShowKeyManager(true)
  }

  const openKeyEdit = async (name: string) => {
    setKeyError('')
    try {
      const res: any = await adminMaintenance.sshKeys.get(name)
      setKeyEditingName(name)
      setKeyFormName(res.data?.name || name)
      setKeyFormContent(res.data?.content || '')
      setShowKeyManager(true)
    } catch (err: any) {
      flash(typeof err === 'string' ? err : '读取密钥失败', true)
    }
  }

  const handleKeyManagerSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setKeyError('')
    if (!keyFormName.trim()) { setKeyError('密钥名称不能为空'); return }
    if (!keyFormContent.trim()) { setKeyError('密钥内容不能为空'); return }
    setKeySaving(true)
    try {
      if (keyEditingName) {
        await adminMaintenance.sshKeys.update(keyEditingName, { new_name: keyFormName.trim(), content: keyFormContent })
        if (form.key === keyEditingName) updateField('key', keyFormName.trim())
        flash(`已更新密钥: ${keyFormName.trim()}`)
      } else {
        await adminMaintenance.sshKeys.create({ name: keyFormName.trim(), content: keyFormContent })
        flash(`已保存密钥: ${keyFormName.trim()}`)
      }
      await loadKeys()
      setKeyEditingName(null)
      setKeyFormName('')
      setKeyFormContent('')
    } catch (err: any) {
      setKeyError(typeof err === 'string' ? err : '保存密钥失败')
    }
    setKeySaving(false)
  }

  const handleKeyDelete = async (name: string) => {
    setRiskAction({
      title: '删除 SSH 密钥',
      description: '已引用该密钥的服务器将无法继续使用密钥文件认证。',
      target: name,
      confirmText: `DELETE_KEY ${name}`,
      confirmButtonLabel: '删除密钥',
      riskLevel: 'high',
      onConfirm: async (): Promise<RiskActionResult> => {
        try {
          await adminMaintenance.sshKeys.delete(name)
          if (form.key === name) updateField('key', '')
          flash(`已删除密钥: ${name}`)
          loadKeys()
          return {
            success: true,
            message: `密钥 ${name} 已删除`,
            links: [
              { label: '查看审计', to: ROUTES.audit, tone: 'brand' },
              { label: '密钥管理', to: ROUTES.servers, tone: 'neutral' },
            ],
          }
        } catch (err: any) {
          const msg = typeof err === 'string' ? err : '删除密钥失败'
          flash(msg, true)
          return { success: false, message: msg }
        }
      },
    })
  }

  const handleHealthProbe = async (serverName: string) => {
    setHealthChecking((prev) => ({ ...prev, [serverName]: true }))
    try {
      const res: any = await serverManagement.opsHealth(serverName, true)
      const data = res.data || res
      setServers((prev) => prev.map((s) => s.name === serverName ? { ...s, health_probe: data } : s))
      flash(`${serverName}: ${data.message || '检查完成'}`, data.status === 'blocked')
    } catch (err: any) {
      flash(typeof err === 'string' ? err : '健康检查失败', true)
    } finally {
      setHealthChecking((prev) => ({ ...prev, [serverName]: false }))
    }
  }

  const [monitorSetup, setMonitorSetup] = useState<{ name: string; host: string; optionsText: string } | null>(null)
  const [probeState, setProbeState] = useState<Record<string, 'idle' | 'loading'>>({})
  const [monitorMenuFor, setMonitorMenuFor] = useState<string | null>(null)
  const [monitorMenuPos, setMonitorMenuPos] = useState<{ left: number; top: number } | null>(null)
  const [groupsModalOpen, setGroupsModalOpen] = useState(false)
// REMOVED-DUP

  const openMonitorSetup = (s: ServerRow) => {
    setMonitorSetup({
      name: s.name,
      host: s.host,
      optionsText: 'INSTALL_FAIL2BAN=yes\nINSTALL_AUDITD=yes\nINSTALL_DAILY=yes\nINSTALL_RESOURCE_MONITOR=yes\nALERT_CHANNELS=telegram\n# TG_BOT_TOKEN=...\n# TG_CHAT_ID=...\n# INSTALL_SCRIPT_URL=https://raw.githubusercontent.com/deanchou/server_security_monitor/master/install_security_monitor.sh',
    })
  }

  const confirmMonitorSetup = async () => {
    if (!monitorSetup) return
    const options: Record<string, string> = {}
    for (const line of monitorSetup.optionsText.split('\n')) {
      const t = line.trim()
      if (!t || t.startsWith('#')) continue
      const eq = t.indexOf('=')
      if (eq <= 0) continue
      const k = t.slice(0, eq).trim()
      const v = t.slice(eq + 1).trim().replace(/^['"]|['"]$/g, '')
      if (k) options[k] = v
    }
    try {
      const res: any = await serverManagement.securityMonitorSetup(monitorSetup.name, 'CONFIRM ops.security_module.setup', options)
      const job = res.data?.job || {}
      flash(`已提交 ${monitorSetup.name} 安全监控安装任务（${job.status || 'queued'}），可在任务中心查看进度`, false)
      setMonitorSetup(null)
    } catch (err: any) {
      flash(typeof err === 'string' ? err : '安全监控安装提交失败', true)
    }
  }

  const handleSecurityMonitorProbe = async (serverName: string) => {
    setProbeState((prev) => ({ ...prev, [serverName]: 'loading' }))
    try {
      const res: any = await serverManagement.securityMonitorProbe(serverName)
      const data = res.data || res
      const parts = [
        `脚本已安装: ${data.security_monitor_installed ? '是' : '否'}`,
        `fail2ban: ${data.fail2ban_active ? '运行中' : '未运行'}`,
        `auditd: ${data.auditd_active ? '运行中' : '未运行'}`,
      ]
      flash(`${serverName} 探测: ${parts.join(' | ')}`, !data.security_monitor_installed)
      load()
    } catch (err: any) {
      flash(typeof err === 'string' ? err : '安全监控探测失败', true)
    } finally {
      setProbeState((prev) => ({ ...prev, [serverName]: 'idle' }))
    }
  }

  const handleSecurityMonitorStatus = async (serverName: string, enabled: boolean) => {
    try {
      const res: any = await serverManagement.securityMonitorStatus(serverName, enabled)
      flash(res.data?.message || `已手动标记 ${serverName} 安全监控${enabled ? '启用' : '停用'}`, false)
      load()
    } catch (err: any) {
      flash(typeof err === 'string' ? err : '状态修改失败', true)
    }
  }
const requestEnableMonitor = (s: ServerRow) => {
      setRiskAction({
        title: '启用安全监控',
        description: `手动将 ${s.name} 标记为「已启用安全监控」（仅改平台登记状态，不执行安装）。探测确认脚本已安装，可登记启用。`,
        target: s.name,
        confirmText: `ENABLE_SECURITY_MONITOR ${s.name}`,
        confirmButtonLabel: '确认启用',
        riskLevel: 'medium',
        details: [{ label: '主机', value: s.host }, { label: '操作', value: '启用（手动登记）' }],
        onConfirm: async () => {
          await handleSecurityMonitorStatus(s.name, true)
          return { success: true, message: `${s.name} 已启用安全监控` }
        },
      })
    }

    const requestDisableMonitor = (s: ServerRow) => {
      setRiskAction({
        title: '停用安全监控',
        description: `手动将 ${s.name} 标记为「未启用安全监控」（仅改平台登记状态，不卸载服务器上的脚本/服务）。适用于手动安装的服务器人工登记。`,
        target: s.name,
        confirmText: `DISABLE_SECURITY_MONITOR ${s.name}`,
        confirmButtonLabel: '确认停用',
        riskLevel: 'medium',
        details: [{ label: '主机', value: s.host }, { label: '操作', value: '停用（手动登记）' }],
        onConfirm: async () => {
          await handleSecurityMonitorStatus(s.name, false)
          return { success: true, message: `${s.name} 已停用安全监控` }
        },
      })
    }

  const jumpOptions = servers.filter((s) => !editing || s.name !== editing)

  const handleGroupDragOver = (e: React.DragEvent, groupName: string) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    setDragOverGroup(groupName)
  }

  const handleGroupDragLeave = () => {
    setDragOverGroup(null)
  }

  const handleGroupDrop = async (e: React.DragEvent, groupName: string) => {
    e.preventDefault()
    setDragOverGroup(null)
    const serverName = e.dataTransfer.getData('text/plain')
    if (!serverName) return
    try {
      const res: any = await serverManagement.groups.assign([serverName], groupName === '__ungrouped__' ? '' : groupName)
      const result = res.data || res
      if (result.failed?.length > 0) {
        flash(`移动失败: ${result.failed[0].error}`, true)
      } else {
        flash(`已将 ${serverName} 移动到${groupName === '__ungrouped__' ? '未分组' : groupName}`)
      }
      load()
      loadGroups()
    } catch (err: any) {
      flash(typeof err === 'string' ? err : '移动失败', true)
    }
  }

  const handleCreateGroup = async () => {
    const name = prompt('请输入分组名称:')
    if (!name?.trim()) return
    try {
      const res: any = await serverManagement.groups.create(name.trim())
      const existed = res.data?.existed
      if (existed) {
        flash(`分组 "${name.trim()}" 已存在`)
      } else {
        flash(`已创建分组: ${name.trim()}`)
      }
      loadGroups()
    } catch (err: any) {
      flash(typeof err === 'string' ? err : '创建分组失败', true)
    }
  }

  const handleRenameGroup = async (oldName: string) => {
    const newName = prompt('请输入新的分组名称:', oldName)
    if (!newName?.trim() || newName.trim() === oldName) return
    try {
      await serverManagement.groups.rename(oldName, newName.trim())
      flash(`已重命名: ${oldName} → ${newName.trim()}`)
      if (selectedGroup === oldName) setSelectedGroup(newName.trim())
      load()
      loadGroups()
    } catch (err: any) {
      flash(typeof err === 'string' ? err : '重命名失败', true)
    }
  }

  const handleDeleteGroup = async (name: string) => {
    setRiskAction({
      title: '删除服务器分组',
      description: '分组会被删除，组内服务器不会删除，但会变为未分组。',
      target: name,
      confirmText: `DELETE_GROUP ${name}`,
      confirmButtonLabel: '删除分组',
      riskLevel: 'medium',
      onConfirm: async (): Promise<RiskActionResult> => {
        try {
          await serverManagement.groups.delete(name)
          flash(`已删除分组: ${name}`)
          if (selectedGroup === name) setSelectedGroup(null)
          load()
          loadGroups()
          return {
            success: true,
            message: `分组 ${name} 已删除`,
            links: [
              { label: '查看审计', to: ROUTES.audit, tone: 'brand' },
              { label: '服务器列表', to: ROUTES.servers, tone: 'neutral' },
            ],
          }
        } catch (err: any) {
          const msg = typeof err === 'string' ? err : '删除分组失败'
          flash(msg, true)
          return { success: false, message: msg }
        }
      },
    })
  }

  const ungroupedCount = servers.filter((s) => !s.group).length

  const quickToggleServerStatus = async (s: ServerRow) => {
    const next = normalizeServerStatus(s) === 'disabled' ? 'online' : 'disabled'
    try {
      await serverManagement.update(s.name, { ...s, username: s.username || s.user || 'root', status: next })
      flash(`${s.name} 已${next === 'disabled' ? '停用' : '启用'}`)
      load()
    } catch (err: any) {
      flash(typeof err === 'string' ? err : '状态更新失败', true)
    }
  }


  const serverColumns: EnhancedColumn<ServerRow>[] = [
    {
      key: 'name', title: '名称', width: 200, sticky: 'start',
      render: (s: ServerRow) => (
        <span
          onClick={(e) => { e.stopPropagation(); window.open(`/servers/${encodeURIComponent(s.name)}?standalone`, '_blank') }}
          style={{ color: 'var(--brand)', cursor: 'pointer', fontWeight: 600, fontFamily: 'var(--font-display)', fontSize: 13 }}
          data-stop-row-click
        >
          {s.name}
        </span>
      ),
    },
    { key: 'host', title: '地址', width: 180, render: (s: ServerRow) => <span className="cc-status" style={{ color: 'var(--text-secondary)', letterSpacing: 0, textTransform: 'none' }}>{s.host}</span> },
    { key: 'port', title: '端口', width: 80, className: 'col-hide-md', render: (s: ServerRow) => <span style={{ color: 'var(--text-muted)' }}>{s.port || 22}</span> },
    { key: 'user', title: '用户', width: 100, className: 'col-hide-md', render: (s: ServerRow) => <span style={{ color: 'var(--text-secondary)' }}>{s.username || s.user || 'root'}</span> },
    {
      key: 'auth', title: '认证', width: 110,
        filter: {
          options: [
            { value: '', label: '全部' },
            { value: 'password', label: '密码' },
            { value: 'key_file', label: '密钥文件' },
            { value: 'key_content', label: '密钥内容' },
          ],
          value: authFilter,
          onChange: setAuthFilter,
        },
      render: (s: ServerRow) => {
        const authMode = s.auth_type || (s.key_content ? 'key_content' : s.key || s.key_file ? 'key_file' : 'password')
        const label = authMode === 'password' ? '密码' : authMode === 'key_file' ? '密钥文件' : '密钥内容'
        return <span className={authModeChipClass(authMode)}>{label}</span>
      },
    },
    {
      key: 'group', title: '分组', width: 130, className: 'col-hide-sm',
        filter: {
          options: [
            { value: '', label: '全部分组' },
            ...groupNames.map((g: string) => ({ value: g, label: g })),
            { value: '__ungrouped__', label: '未分组' },
          ],
          value: selectedGroup === null ? '' : selectedGroup === '' ? '__ungrouped__' : selectedGroup,
          onChange: (v: string) => {
            if (v === '') setSelectedGroup(null)
            else if (v === '__ungrouped__') setSelectedGroup('')
            else setSelectedGroup(v)
          },
        },
      render: (s: ServerRow) => {
        if (!s.group) return <span style={{ color: 'var(--text-faint)' }}>—</span>
        const groupColor = getGroupColor(s.group)
        return (
          <span className="cc-chip" style={{ color: groupColor?.text, background: 'transparent', borderColor: 'var(--border)' }}>
            <span style={{ width: 6, height: 6, borderRadius: 999, background: groupColor?.text, marginRight: 4 }} />
            {s.group}
          </span>
        )
      },
    },
    {
      key: 'jump', title: '跳板机', width: 140, className: 'col-hide-md',
      render: (s: ServerRow) => {
        const jumpLabel = typeof s.jump_host === 'string' ? s.jump_host : s.jump_host?.name || ''
        return <span style={{ color: jumpLabel ? 'var(--text-secondary)' : 'var(--text-faint)', fontSize: 11.5 }}>{jumpLabel || '—'}</span>
      },
    },
    {
      key: 'desc', title: '描述', className: 'col-hide-sm',
      render: (s: ServerRow) => <span style={{ color: 'var(--text-muted)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'block', fontSize: 12 }}>{s.description || '—'}</span>,
    },
    {
      key: 'status', title: '状态', width: 90,
        filter: {
          options: [
            { value: '', label: '全部' },
            { value: 'online', label: '在线' },
            { value: 'disabled', label: '停用' },
            { value: 'offline', label: '离线' },
          ],
          value: serverStatusFilter === 'all' ? '' : serverStatusFilter,
          onChange: (v: string) => setServerStatusFilter(v === '' ? 'all' : (v as any)),
        },
      render: (s: ServerRow) => {
        const status = normalizeServerStatus(s)
        return <span className={serverStatusChipClass(status)}>{serverStatusLabel(status)}</span>
      },
    },
    {
      key: 'securityMonitor', title: '安全监控', width: 250,
        filter: {
          options: [
            { value: '', label: '全部' },
            { value: '未探测', label: '未探测' },
            { value: '未安装', label: '未安装' },
            { value: '已启用', label: '已启用' },
            { value: '未启用', label: '未启用' },
          ],
          value: monitorFilter,
          onChange: setMonitorFilter,
        },
      render: (s: ServerRow) => {
        const mon = s.security_monitor || {}
        const enabled = Boolean(mon.enabled)
        const probing = probeState[s.name] === 'loading'
        // 探测结论已持久化到后端，刷新后仍可见
        const probed = typeof mon.installed === 'boolean'
        const installed = Boolean(mon.installed)
        const checkedTitle = mon.checked_at
          ? `上次探测: ${formatTime(mon.checked_at)}`
          : 'SSH 探测实际安装状态'
        // 单按钮：标签=当前建议的主动作，菜单内聚合全部适用动作
        const primaryLabel = probing ? '探测中…' : enabled ? '停用' : installed ? '启用' : probed ? '安装' : '探测'
          const stateLabel = probing ? '探测中…' : !probed ? '未探测' : !installed ? '未安装' : enabled ? '已启用' : '未启用'
          const stateBg = probing ? 'var(--bg-surface, #f0f0f0)' : !probed ? 'var(--bg-surface, #f0f0f0)' : !installed ? 'var(--danger-surface, rgba(229,62,62,.1))' : enabled ? 'var(--success-surface, rgba(56,161,105,.12))' : 'var(--warning-surface, rgba(236,201,75,.12))'
          const stateColor = probing ? 'var(--text-muted)' : !probed ? 'var(--text-muted)' : !installed ? 'var(--danger, #e53e3e)' : enabled ? 'var(--success, #38a169)' : 'var(--warning, #d69e2e)'
        const menuOpen = monitorMenuFor === s.name
        const closeMenu = () => { setMonitorMenuFor(null); setMonitorMenuPos(null) }
        const itemStyle: React.CSSProperties = {
          display: 'block', width: '100%', textAlign: 'left', padding: '6px 12px',
          fontSize: 12, background: 'none', border: 'none', cursor: 'pointer',
          color: 'var(--text-primary)', whiteSpace: 'nowrap',
        }
        return (
          <div className="security-monitor-merged" style={{ position: 'relative', display: 'inline-flex', maxWidth: '100%' }} data-stop-row-click>
            <span className="cc-chip" style={enabled
              ? { color: 'var(--success, #38a169)', borderColor: 'var(--success, #38a169)', fontWeight: 600 }
              : { color: 'var(--text-muted)', borderColor: 'var(--border)' }}>
              {enabled ? '已启用' : '未启用'}
            </span>
            {probed && (
              <span className="cc-chip" title={checkedTitle} style={installed
                ? { color: 'var(--success, #38a169)', borderColor: 'var(--success, #38a169)', fontSize: 11 }
                : { color: 'var(--danger, #e53e3e)', borderColor: 'var(--danger, #e53e3e)', fontSize: 11 }}>
                {installed ? '已安装' : '未安装'}
              </span>
            )}
            <div style={{ position: 'relative' }}>
              <button className="btn btn-subtle" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, padding: '3px 8px', whiteSpace: 'nowrap', maxWidth: 240, borderColor: enabled ? 'var(--success, #38a169)' : 'var(--border)', color: enabled ? 'var(--success, #38a169)' : 'var(--text-primary)', background: enabled ? 'var(--success-surface, rgba(56,161,105,.08))' : 'var(--bg-surface, #fff)' }} disabled={probing}
                onClick={(e) => { e.stopPropagation(); if (menuOpen) { closeMenu() } else { const r = e.currentTarget.getBoundingClientRect(); setMonitorMenuPos({ left: Math.max(8, r.right - 150), top: r.bottom + 4 }); setMonitorMenuFor(s.name) } }}
                title={`${stateLabel} · ${primaryLabel} · 点击查看全部操作`}>
                <span style={{ fontWeight: 600, padding: '1px 6px', borderRadius: 4, background: stateBg, color: stateColor }}>{stateLabel}</span>
                  
                  <span style={{ color: 'var(--text-faint, #aaa)', margin: '0 2px' }}>→</span>
                  <span style={{ fontWeight: 600 }}>{probing ? '探测中…' : primaryLabel}</span>
                  <span style={{ fontSize: 9, color: 'var(--text-faint, #aaa)' }}>▾</span>
              </button>
              {menuOpen && monitorMenuPos && createPortal(
                <>
                  <div style={{ position: 'fixed', inset: 0, zIndex: 9990 }} onClick={closeMenu} />
                  <div style={{
                    position: 'fixed', left: monitorMenuPos.left, top: monitorMenuPos.top, zIndex: 9991, minWidth: 150, maxWidth: 260,
                    background: 'var(--bg-elevated, var(--bg-card, #fff))', border: '1px solid var(--border)',
                    borderRadius: 8, boxShadow: '0 8px 24px rgba(0,0,0,.14)', overflow: 'hidden', padding: 4,
                  }}>
                    <button style={itemStyle} disabled={probing}
                      onClick={(e) => { e.stopPropagation(); closeMenu(); handleSecurityMonitorProbe(s.name) }}>
                      {probing ? '探测中…' : probed ? '重探（核对实际状态）' : '探测（核对实际状态）'}
                    </button>
{!enabled && installed && (
                        <button style={{ ...itemStyle, color: 'var(--success, #38a169)' }}
                          onClick={(e) => { e.stopPropagation(); closeMenu(); requestEnableMonitor(s) }}>启用（登记）</button>
                      )}
                      {enabled && (
                        <button style={{ ...itemStyle, color: 'var(--danger, #e53e3e)' }}
                          onClick={(e) => { e.stopPropagation(); closeMenu(); requestDisableMonitor(s) }}>停用（登记）</button>
                      )}
                    {false && !enabled && installed && (
                      <RiskActionGuard
                        riskLevel="medium"
                        title="启用安全监控"
                        description={`手动将 ${s.name} 标记为「已启用安全监控」（仅改平台登记状态，不执行安装）。探测确认脚本已安装，可登记启用。`}
                        target={s.name}
                        confirmText={`ENABLE_SECURITY_MONITOR ${s.name}`}
                        confirmMode="one-click"
                        details={[{ label: '主机', value: s.host }, { label: '操作', value: '启用（手动登记）' }]}
                        onConfirm={() => handleSecurityMonitorStatus(s.name, true)}
                      >
                        {(open) => (
                          <button style={{ ...itemStyle, color: 'var(--success, #38a169)' }}
                            onClick={(e) => { e.stopPropagation(); closeMenu(); open() }}>启用（登记）</button>
                        )}
                      </RiskActionGuard>
                    )}
                    {!enabled && !installed && (
                      <button style={{ ...itemStyle, color: 'var(--brand)' }}
                        onClick={(e) => { e.stopPropagation(); closeMenu(); openMonitorSetup(s) }}>安装（自定义选项）</button>
                    )}
                    {false && enabled && (
                      <RiskActionGuard
                        riskLevel="medium"
                        title="停用安全监控"
                        description={`手动将 ${s.name} 标记为「未启用安全监控」（仅改平台登记状态，不卸载服务器上的脚本/服务）。适用于手动安装的服务器人工登记。`}
                        target={s.name}
                        confirmText={`DISABLE_SECURITY_MONITOR ${s.name}`}
                        confirmMode="one-click"
                        details={[{ label: '主机', value: s.host }, { label: '操作', value: '停用（手动登记）' }]}
                        onConfirm={() => handleSecurityMonitorStatus(s.name, false)}
                      >
                        {(open) => (
                          <button style={{ ...itemStyle, color: 'var(--danger, #e53e3e)' }}
                            onClick={(e) => { e.stopPropagation(); closeMenu(); open() }}>停用（登记）</button>
                        )}
                      </RiskActionGuard>
                    )}
                  </div>
                </>
                  , document.body
              )}
            </div>
          </div>
        )
      },
    },
    {
      key: 'config', title: '配置 / 健康', width: 200, className: 'col-hide-xs',
      render: (s: ServerRow) => {
        const cfg = s.config_status || {}
        const cfgStatus = cfg.status || (cfg.complete ? 'passed' : 'blocked')
        const health = s.health_probe || {}
        const healthLabel = health.status === 'passed' ? '在线' : health.status === 'blocked' ? '异常' : '未检查'
        const cfgLabel = cfgStatus === 'blocked' ? '配置缺失' : cfgStatus === 'warning' ? '有建议' : '配置完整'
        return (
          <div style={{ display: 'grid', gap: 4 }}>
            <span title={(cfg.missing || []).join(', ') || (cfg.suggestions || []).join('；')} className={cfgChipClass(cfgStatus)}>
              {cfgLabel}
            </span>
            <span className={`cc-status ${health.status === 'passed' ? 'cc-status--ok' : health.status === 'blocked' ? 'cc-status--danger' : 'cc-status--muted'}`} style={{ letterSpacing: 0, textTransform: 'none', fontSize: 10.5 }}>
              {healthLabel}{health.disk?.available_kb ? ` · 可用 ${Math.round(health.disk.available_kb / 1024 / 1024)}GB` : ''}
            </span>
          </div>
        )
      },
    },
    {
      key: 'actions', title: '操作', align: 'right', width: 170, sticky: 'end',
      render: (s: ServerRow) => {
        const status = normalizeServerStatus(s)
        const toggleTip = status === 'disabled' ? '启用' : '停用'
        return (
          <div className="col-actions" data-stop-row-click>
            <ActionButton tip={toggleTip} onClick={(e) => { e.stopPropagation(); quickToggleServerStatus(s) }}>{status === 'disabled' ? '▶' : '⏸'}</ActionButton>
            <ActionButton tip={healthChecking[s.name] ? '检查中' : '健康检查'} disabled={healthChecking[s.name]} onClick={(e) => { e.stopPropagation(); handleHealthProbe(s.name) }}>{healthChecking[s.name] ? '…' : '♥'}</ActionButton>
            <ActionButton tip="详情" variant="success" onClick={(e) => { e.stopPropagation(); navigate(`/servers/${encodeURIComponent(s.name)}`) }}>↗</ActionButton>
            <ActionButton tip="编辑" onClick={(e) => { e.stopPropagation(); openEdit(s) }}>✎</ActionButton>
            <RiskActionGuard
              riskLevel="high"
              title="删除服务器"
              description="服务器资产会从平台移除，已有发布记录不会删除。"
              target={`${s.name} (${s.host})`}
              confirmText={`DELETE_SERVER ${s.name}`}
              confirmMode="one-click"
              details={[{ label: '主机', value: s.host }, { label: '分组', value: s.group || '未分组' }]}
              onConfirm={() => doDeleteServer(s)}
            >
              {(open) => (
                <ActionButton tip="删除" variant="danger" onClick={(e) => { e.stopPropagation(); open() }}>×</ActionButton>
              )}
            </RiskActionGuard>
          </div>
        )
      },
    },
  ]

  return (
    <div className="cc-grid-bg" style={{ display: 'grid', gap: 16, padding: '4px 0 24px' }}>
      <div className="cc-hero" style={{ padding: '8px 16px', gap: 12, alignItems: 'center' }}>
        <div>
          {null}
          <h1 style={{ margin: 0, fontSize: 16, lineHeight: 1.3 }}>服务器管理</h1>
          <p className="cc-hero-desc" style={{ margin: 0, fontSize: 12 }}>共 {servers.length} 台 · 分组 {groupNames.length} 个{selectedGroup !== null ? ` · 当前：${selectedGroup === '' ? '未分组' : selectedGroup}` : ''}</p>
        </div>
        <div className="cc-hero-stats" style={{ minWidth: 0, gap: 8 }}>
          <div className="cc-hero-stat cc-hero-stat--ok" style={{ padding: '2px 8px', fontSize: 11 }}>
            <strong>{statusCounts.online || 0}</strong><span>在线</span>
          </div>
          <div className="cc-hero-stat cc-hero-stat--warn" style={{ padding: '2px 8px', fontSize: 11 }}>
            <strong>{statusCounts.disabled || 0}</strong><span>停用</span>
          </div>
          <div className="cc-hero-stat cc-hero-stat--risk" style={{ padding: '2px 8px', fontSize: 11 }}>
            <strong>{statusCounts.offline || 0}</strong><span>离线</span>
          </div>
        </div>
      </div>
    <div className="cc-server-layout">
      {false && (<aside className="cc-server-side">
        <div className="cc-server-side-head">
          <strong>服务器分组</strong>
          <small>{servers.length} 台</small>
        </div>

        <div className={`cc-server-group${selectedGroup === null ? ' cc-server-group--active' : ''}`}
          onClick={() => setSelectedGroup(null)}
          onDragOver={(e) => handleGroupDragOver(e, '__all__')}
          onDragLeave={handleGroupDragLeave}
          onDrop={(e) => handleGroupDrop(e, '__all__')}
        >
          <span className="cc-server-group-name">全部</span>
          <span className="cc-server-group-count">{servers.length}</span>
        </div>

        {groupNames.map((name: string) => {
          const color = getGroupColor(name)
          const groupData = groups.find((g) => g.name === name)
          const count = groupData?.server_count || 0
          return (
            <div
              key={name}
              className={`cc-server-group${selectedGroup === name ? ' cc-server-group--active' : ''}${dragOverGroup === name ? ' cc-server-group--active' : ''}`}
              onClick={() => setSelectedGroup(name)}
              onDragOver={(e) => handleGroupDragOver(e, name)}
              onDragLeave={handleGroupDragLeave}
              onDrop={(e) => handleGroupDrop(e, name)}
              onMouseEnter={(e) => {
                const el = e.currentTarget.querySelector('.group-actions') as HTMLElement
                if (el) el.style.opacity = '1'
              }}
              onMouseLeave={(e) => {
                const el = e.currentTarget.querySelector('.group-actions') as HTMLElement
                if (el) el.style.opacity = '0'
                setGroupActionsOpen(null)
              }}
              style={{ position: 'relative' }}
            >
              <span className="cc-server-group-name">
                <span style={{ width: 8, height: 8, borderRadius: 999, background: color?.text, boxShadow: `0 0 6px ${color?.text}` }} />
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</span>
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span className="cc-server-group-count">{count}</span>
                <div className="group-actions" style={{ opacity: 0, transition: 'opacity 0.15s', position: 'relative' }}>
                  <button
                    className="cc-icon-btn"
                    onClick={(e) => { e.stopPropagation(); setGroupActionsOpen(groupActionsOpen === name ? null : name) }}
                    style={{ padding: '0 4px', fontSize: 14, minWidth: 22, height: 22 }}
                  >⋮</button>
                  {groupActionsOpen === name && (
                    <div style={{
                      position: 'absolute', right: 0, top: '100%', zIndex: 100,
                      background: 'var(--bg-surface)', border: '1px solid var(--border-stronger)', borderRadius: 6,
                      padding: '4px 0', minWidth: 90, boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
                    }}>
                      <button className="cc-icon-btn" onClick={(e) => { e.stopPropagation(); setGroupActionsOpen(null); handleRenameGroup(name) }}
                        style={{ display: 'block', width: '100%', justifyContent: 'flex-start', fontSize: 12 }}>
                        重命名
                      </button>
                      <button className="cc-icon-btn cc-icon-btn--danger" onClick={(e) => { e.stopPropagation(); setGroupActionsOpen(null); handleDeleteGroup(name) }}
                        style={{ display: 'block', width: '100%', justifyContent: 'flex-start', fontSize: 12 }}>
                        删除分组
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )
        })}

        <div
          className={`cc-server-group${selectedGroup === '' ? ' cc-server-group--active' : ''}${dragOverGroup === '__ungrouped__' ? ' cc-server-group--active' : ''}`}
          onClick={() => setSelectedGroup('')}
          onDragOver={(e) => handleGroupDragOver(e, '__ungrouped__')}
          onDragLeave={handleGroupDragLeave}
          onDrop={(e) => handleGroupDrop(e, '__ungrouped__')}
          style={{ marginTop: 6, borderTop: '1px solid var(--border)', paddingTop: 10 }}
        >
          <span className="cc-server-group-name" style={{ fontStyle: 'italic', color: 'var(--text-muted)' }}>未分组</span>
          <span className="cc-server-group-count">{ungroupedCount}</span>
        </div>

        <button className="cc-icon-btn" onClick={handleCreateGroup}
          style={{
            marginTop: 6, width: '100%', justifyContent: 'center', fontSize: 12,
            background: 'transparent', border: '1px dashed var(--border-strong)', color: 'var(--text-muted)',
          }}>
          + 新建分组
        </button>
      </aside>)}

      <div style={{ flex: 1, display: 'grid', gap: 14, minWidth: 0 }}>
        <div className="cc-toolbar">
          <div className="cc-toolbar-search">
            <span className="cc-toolbar-search-icon">⌕</span>
            <input
              value={serverFilter}
              onChange={(e) => setServerFilter(e.target.value)}
              placeholder="搜索名称 / IP / 描述"
              aria-label="搜索服务器"
            />
            {serverFilter && (
              <button className="cc-icon-btn" style={{ padding: '0 8px', height: 20, fontSize: 10 }} onClick={() => setServerFilter('')}>清空</button>
            )}
          </div>
          <div className="cc-toolbar-divider" />
          <div className="cc-toolbar-actions">
            <span className="cc-toolbar-summary">状态</span>
            <select className="cc-select" value={serverStatusFilter} onChange={(e) => setServerStatusFilter(e.target.value as any)}>
              <option value="all">全部 ({statusCounts.online + statusCounts.disabled + statusCounts.offline || servers.length})</option>
              <option value="online">在线 ({statusCounts.online || 0})</option>
              <option value="disabled">停用 ({statusCounts.disabled || 0})</option>
              <option value="offline">离线 ({statusCounts.offline || 0})</option>
            </select>
            <span className="cc-toolbar-summary">配置</span>
            <select className="cc-select" value={configFilter} onChange={(e) => setConfigFilter(e.target.value as any)}>
              <option value="all">全部</option>
              <option value="passed">配置完整 ({configCounts.passed || 0})</option>
              <option value="warning">有建议 ({configCounts.warning || 0})</option>
              <option value="blocked">配置缺失 ({configCounts.blocked || 0})</option>
            </select>
          </div>
          <div className="cc-toolbar-cta">
            {selected.size >= 2 && (
              <button className="cc-icon-btn" onClick={openBatchEdit} style={{ height: 30, padding: '0 12px', background: 'color-mix(in srgb, var(--brand) 14%, transparent)', color: 'var(--brand)', borderColor: 'color-mix(in srgb, var(--brand) 32%, var(--border))' }}>
                批量编辑 ({selected.size})
              </button>
            )}
            {selected.size > 0 && (
              <button className="cc-icon-btn" onClick={() => setSelected(new Set())} style={{ height: 30, padding: '0 10px' }}>
                取消选择
              </button>
            )}
              <button className="cc-icon-btn" onClick={() => setGroupsModalOpen(true)} style={{ height: 30, padding: '0 10px' }}>
                分组 ({groupNames.length}){selectedGroup !== null ? ` · ${selectedGroup === '' ? '未分组' : selectedGroup}` : ''}
              </button>
            <button className="cc-icon-btn" onClick={openKeyCreate} style={{ height: 30, padding: '0 10px' }}>
              密钥管理 ({sshKeys.length})
            </button>
            <button className="cc-icon-btn cc-icon-btn--success" onClick={openCreate} style={{ height: 30, padding: '0 14px' }}>
              + 新增服务器
            </button>
          </div>
        </div>

        {false && error && (
          <div className="card" style={{ background: 'var(--danger-surface)', borderColor: 'var(--danger-border)', color: 'var(--danger)', fontSize: '13px' }}>
            {error}
          </div>
        )}
        {false && success && (
          <div className="card" style={{ background: 'var(--success-surface)', borderColor: 'var(--success-border)', color: 'var(--success)', fontSize: '13px' }}>
            {success}
          </div>
        )}

        <div className="cc-table-wrap" style={{ padding: 0, overflow: 'hidden' }}>
          <EnhancedDataTable
            rows={tableRows}
            columns={serverColumns}
            rowKey={(s: ServerRow) => s.name}
            loading={loading}
            emptyTitle={
              (selectedGroup !== null || configFilter !== 'all' || serverStatusFilter !== 'all' || serverFilter || authFilter || monitorFilter)
                ? '当前筛选条件下无匹配服务器'
                : '暂无服务器配置，点击上方 "新增服务器" 添加'
            }
            selectedKeys={Array.from(selected)}
            onSelectionChange={(keys) => {
              const newSet = new Set<string>(keys)
              setSelected(newSet)
            }}
            onRowClick={(s: ServerRow) => navigate(`/servers/${encodeURIComponent(s.name)}`)}
            expandRow={(s: ServerRow) => (
              <div className="row-expanded-content">
                <div><strong>地址</strong><span>{s.host}:{s.port || 22}</span></div>
                <div><strong>用户</strong><span>{s.username || s.user || 'root'}</span></div>
                <div><strong>认证</strong><span>{s.auth_type === 'password' ? '密码' : s.auth_type === 'key_file' ? '密钥文件' : (s.key || s.key_content ? '密钥内容' : '密码')}</span></div>
                <div><strong>跳板机</strong><span>{typeof s.jump_host === 'string' ? s.jump_host : s.jump_host?.name || '—'}</span></div>
                <div><strong>分组</strong><span>{s.group || '—'}</span></div>
                <div style={{ gridColumn: '1 / -1' }}><strong>描述</strong><span>{s.description || '—'}</span></div>
                {s.config_status && s.config_status.suggestions && s.config_status.suggestions.length > 0 && (
                  <div style={{ gridColumn: '1 / -1' }}><strong>配置建议</strong><span>{s.config_status.suggestions.join('；')}</span></div>
                )}
              </div>
            )}
            expandedKeys={Array.from(expandedRows)}
            onExpandChange={(keys) => setExpandedRows(new Set(keys))}
            pageSize={serverPageSize}
            currentPage={serverPage}
            totalCount={tableRows.length}
            onPageChange={setServerPage}
            onPageSizeChange={setServerPageSize}
            stackOnNarrow
          />
        </div>
      </div>

      {showModal && (
        <div className="cc-modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) setShowModal(false) }}>
          <div className="cc-modal-shell">
            <div className="cc-modal-head">
              <h3>
                {editing ? `编辑服务器: ${editing}` : '新增服务器'}
                <small>SERVER · FORM</small>
              </h3>
              <button className="cc-modal-close" onClick={() => setShowModal(false)}>✕</button>
            </div>

            {formError && (
              <div style={{ background: 'var(--danger-surface)', color: 'var(--danger)', padding: '10px', borderRadius: '6px', marginBottom: '16px', fontSize: '13px' }}>
                {formError}
              </div>
            )}

            <form onSubmit={handleSubmit} style={{ display: 'grid', gap: '14px' }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: '12px' }}>
                <FormField label="名称 *" value={form.name} onChange={(v) => updateField('name', v)} placeholder="server-name"
                  disabled={!!editing} />
                <FormField label="主机地址 *" value={form.host} onChange={(v) => updateField('host', v)} placeholder="10.0.0.1 或 hostname" />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: '12px' }}>
                <FormField label="端口" value={String(form.port)} onChange={(v) => updateField('port', Number(v) || 22)} type="number" />
                <FormField label="用户名" value={form.username} onChange={(v) => updateField('username', v)} placeholder="root" />
              </div>

              <div>
                <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '6px' }}>分组</label>
                <select value={form.group} onChange={(e) => updateField('group', e.target.value)}
                  style={{
                    width: '100%', padding: '8px 12px', background: 'var(--bg-surface)', color: 'var(--text-primary)',
                    border: '1px solid var(--border-strong)', borderRadius: '6px', fontSize: '13px',
                  }}>
                  <option value="">未分组</option>
                  {groupNames.map((name: string) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </select>
              </div>

              <div>
                <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '6px' }}>服务器状态</label>
                <select value={form.status} onChange={(e) => updateField('status', e.target.value)}
                  style={{
                    width: '100%', padding: '8px 12px', background: 'var(--bg-surface)', color: 'var(--text-primary)',
                    border: '1px solid var(--border-strong)', borderRadius: '6px', fontSize: '13px',
                  }}>
                  <option value="online">在线 / 启用</option>
                  <option value="disabled">停用</option>
                  <option value="offline">离线</option>
                </select>
              </div>

              <div>
                <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '6px' }}>跳板机</label>
                <select value={form.jump_host} onChange={(e) => updateField('jump_host', e.target.value)}
                  style={{
                    width: '100%', padding: '8px 12px', background: 'var(--bg-surface)', color: 'var(--text-primary)',
                    border: '1px solid var(--border-strong)', borderRadius: '6px', fontSize: '13px',
                  }}>
                  <option value="">不使用跳板机</option>
                  {jumpOptions.map((s) => (
                    <option key={s.name} value={s.name}>
                      {s.name} ({s.host}:{s.port || 22})
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px' }}>认证方式</label>
                <div style={{ display: 'flex', gap: '8px' }}>
                  {(['password', 'key_file', 'key_content'] as const).map((m) => (
                    <label key={m} style={{
                      padding: '6px 16px', borderRadius: '6px', fontSize: '13px', cursor: 'pointer',
                      background: form.auth_type === m ? 'var(--action-bg)' : 'var(--bg-surface)',
                      color: form.auth_type === m ? 'var(--action-text)' : 'var(--text-muted)',
                      border: form.auth_type === m ? '1px solid var(--brand)' : '1px solid var(--border-strong)',
                    }}>
                      <input type="radio" name="auth_type" value={m} checked={form.auth_type === m}
                        onChange={(e) => { updateField('auth_type', e.target.value); setUploadedKeyName('') }}
                        style={{ display: 'none' }} />
                      {m === 'password' ? '密码' : m === 'key_file' ? '密钥文件' : '密钥内容'}
                    </label>
                  ))}
                </div>
              </div>

              {form.auth_type === 'password' && (
                <FormField label="密码" value={form.password}
                  onChange={(v) => updateField('password', v)} type="password" placeholder="留空则不修改" />
              )}

              {form.auth_type === 'key_file' && (
                <div>
                  <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px' }}>密钥文件</label>
                  <div style={{ display: 'grid', gap: '8px' }}>
                    <select value={sshKeys.some((k) => k.name === form.key) ? form.key : ''}
                      onChange={(e) => { updateField('key', e.target.value); setUploadedKeyName(e.target.value) }}
                      style={{ width: '100%', padding: '8px 12px', background: 'var(--bg-surface)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)', borderRadius: '6px', fontSize: '13px' }}>
                      <option value="">选择已保存密钥文件...</option>
                      {sshKeys.map((k) => (
                        <option key={k.name} value={k.name}>{k.name} ({Math.ceil(k.size / 1024)}KB)</option>
                      ))}
                    </select>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                      <input value={form.key} onChange={(e) => updateField('key', e.target.value)}
                        placeholder="保存的密钥名称，或绝对路径 / ~/.ssh/id_rsa"
                        style={{ flex: 1, background: 'var(--bg-surface)', color: 'var(--text-primary)' }} />
                      <label style={{
                        cursor: 'pointer', padding: '8px 14px', fontSize: '13px',
                        background: uploadingKey ? 'var(--border-strong)' : 'var(--action-bg)',
                        color: uploadingKey ? 'var(--text-muted)' : 'var(--action-text)', borderRadius: '6px', whiteSpace: 'nowrap',
                      }}>
                        {uploadingKey ? '上传中...' : '上传新密钥'}
                        <input type="file" onChange={handleKeyUpload} disabled={uploadingKey}
                          style={{ display: 'none' }} />
                      </label>
                      <button type="button" className="btn" onClick={() => setShowKeyManager(true)}
                        style={{ padding: '8px 12px', fontSize: '13px', background: 'var(--border-strong)', color: 'var(--action-text)' }}>
                        管理
                      </button>
                    </div>
                  </div>
                  <div style={{ marginTop: '6px', fontSize: '12px', color: 'var(--text-muted)' }}>
                    推荐选择已保存密钥名称；后端会从项目 keys/ 目录安全解析。也兼容绝对路径与 ~/.ssh 路径。
                  </div>
                  {uploadedKeyName && (
                    <div style={{ marginTop: '6px', fontSize: '12px', color: 'var(--success)', background: 'var(--success-surface)', padding: '4px 10px', borderRadius: '4px', display: 'inline-block' }}>
                      ✓ {uploadedKeyName}
                    </div>
                  )}
                </div>
              )}

              {form.auth_type === 'key_content' && (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                    <label style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>密钥内容</label>
                    <label style={{
                      cursor: 'pointer', padding: '4px 12px', fontSize: '12px',
                      background: 'var(--action-bg)', color: 'var(--action-text)', borderRadius: '6px',
                    }}>
                      从文件读取
                      <input type="file" onChange={handleKeyContentRead} style={{ display: 'none' }} />
                    </label>
                  </div>
                  <textarea value={form.key_content} onChange={(e) => updateField('key_content', e.target.value)}
                    placeholder="-----BEGIN RSA PRIVATE KEY-----&#10;..."
                    style={{ width: '100%', height: '80px', fontFamily: 'monospace', fontSize: '12px', background: 'var(--bg-page)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)', borderRadius: '6px', padding: '8px', resize: 'vertical' }} />
                  {uploadedKeyName && (
                    <div style={{ marginTop: '6px', fontSize: '12px', color: 'var(--success)', background: 'var(--success-surface)', padding: '4px 10px', borderRadius: '4px', display: 'inline-block' }}>
                      ✓ {uploadedKeyName}
                    </div>
                  )}
                </div>
              )}

              <FormField label="描述" value={form.description}
                onChange={(v) => updateField('description', v)} placeholder="服务器用途说明（可选）" />
              <FormField label="标签" value={form.tags}
                onChange={(v) => updateField('tags', v)} placeholder="逗号分隔，如: production, api（可选）" />

              <div>
                <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px' }}>
                  SFTP 安全目录
                  <span style={{ color: 'var(--text-muted)', fontSize: '12px', marginLeft: '6px', fontWeight: 'normal' }}>
                    限制文件浏览/上传/删除可访问的根目录，用逗号或换行分隔；留空则放开为 /
                  </span>
                </label>
                <textarea
                  value={form.sftp_allowed_roots}
                  onChange={(e) => updateField('sftp_allowed_roots', e.target.value)}
                  placeholder="/data, /opt, /var/log, /tmp"
                  rows={2}
                  style={{ width: '100%', background: 'var(--bg-surface)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)', borderRadius: '6px', padding: '8px 10px', fontFamily: 'monospace', fontSize: '13px', resize: 'vertical' }}
                />
              </div>

              <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', marginTop: '8px' }}>
                <button type="button" className="btn" onClick={() => setShowModal(false)}
                  style={{ padding: '8px 20px', background: 'var(--border-strong)', color: 'var(--text-primary)', fontSize: '14px' }}>
                  取消
                </button>
                <button type="submit" className="btn" disabled={saving}
                  style={{ padding: '8px 24px', background: 'var(--success-border)', color: 'var(--success)', fontSize: '14px', fontWeight: 'bold' }}>
                  {saving ? '保存中...' : editing ? '保存修改' : '创建服务器'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}


      {showKeyManager && (
        <div className="cc-modal-overlay" style={{ zIndex: 1100 }} onClick={(e) => { if (e.target === e.currentTarget) setShowKeyManager(false) }}>
          <div className="cc-modal-shell cc-modal-shell--wide">
            <div className="cc-modal-head">
              <div>
                <h3>SSH 密钥管理</h3>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4, fontFamily: 'var(--font-mono)' }}>密钥保存在项目 keys/ 目录，列表仅展示元数据</div>
              </div>
              <button className="cc-modal-close" onClick={() => setShowKeyManager(false)}>✕</button>
            </div>

            {keyError && (
              <div className="cc-modal-callout cc-modal-callout--danger" style={{ marginBottom: 12 }}>{keyError}</div>
            )}

            <div style={{ display: 'grid', gridTemplateColumns: '260px minmax(0, 1fr)', gap: 16 }}>
              <div style={{ border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
                <div style={{ padding: '10px 12px', background: 'var(--bg-surface-2)', color: 'var(--text-secondary)', fontSize: 12, fontFamily: 'var(--font-mono)', fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', borderBottom: '1px solid var(--border)' }}>已保存密钥</div>
                <div style={{ maxHeight: 360, overflowY: 'auto' }}>
                  {sshKeys.length === 0 ? (
                    <div className="cc-empty-state" style={{ padding: 18 }}><span>暂无保存的密钥</span></div>
                  ) : sshKeys.map((k) => (
                    <div key={k.name} style={{ padding: '10px 12px', borderTop: '1px solid var(--border)', display: 'grid', gap: 6 }}>
                      <div style={{ color: 'var(--text-primary)', fontSize: 12, fontFamily: 'var(--font-mono)', wordBreak: 'break-all' }}>{k.name}</div>
                      <div style={{ color: 'var(--text-muted)', fontSize: 11, fontFamily: 'var(--font-mono)' }}>{k.size} bytes · {new Date(k.modified * 1000).toLocaleString()}</div>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button type="button" className="cc-icon-btn" onClick={() => { updateField('key', k.name); setUploadedKeyName(k.name); setShowKeyManager(false) }}
                          style={{ height: 26, padding: '0 8px', fontSize: 11 }}>选择</button>
                        <button type="button" className="cc-icon-btn" onClick={() => openKeyEdit(k.name)} style={{ height: 26, padding: '0 8px', fontSize: 11 }}>编辑</button>
                        <button type="button" className="cc-icon-btn cc-icon-btn--danger" onClick={() => handleKeyDelete(k.name)} style={{ height: 26, padding: '0 8px', fontSize: 11 }}>删除</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <form onSubmit={handleKeyManagerSubmit} style={{ display: 'grid', gap: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-display)' }}>{keyEditingName ? `编辑: ${keyEditingName}` : '新增密钥'}</strong>
                  {keyEditingName && (
                    <button type="button" className="cc-icon-btn" onClick={() => { setKeyEditingName(null); setKeyFormName(''); setKeyFormContent(''); setKeyError('') }}
                      style={{ height: 28, padding: '0 10px', fontSize: 12 }}>新建</button>
                  )}
                </div>
                <FormField label="密钥名称" value={keyFormName} onChange={setKeyFormName} placeholder="prod-web.pem" />
                <div>
                  <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: 12, marginBottom: 4, fontFamily: 'var(--font-mono)', fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase' }}>密钥内容</label>
                  <textarea value={keyFormContent} onChange={(e) => setKeyFormContent(e.target.value)}
                    placeholder={"-----BEGIN OPENSSH PRIVATE KEY-----\n..."}
                    style={{ width: '100%', height: 220, fontFamily: 'var(--font-mono)', fontSize: 12, background: 'var(--bg-panel-deep)', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 10, padding: 10, resize: 'vertical', outline: 'none' }} />
                </div>
                <div className="cc-modal-callout cc-modal-callout--warning" style={{ fontSize: 11 }}>
                  安全提示：删除或重命名密钥不会自动更新已引用旧名称的服务器，请先确认引用关系。
                </div>
                <div className="cc-modal-foot" style={{ marginTop: 0, borderTop: 0, paddingTop: 0 }}>
                  <button type="button" className="cc-icon-btn" onClick={() => setShowKeyManager(false)} style={{ height: 32, padding: '0 14px' }}>关闭</button>
                  <button type="submit" className="cc-icon-btn cc-icon-btn--success" disabled={keySaving} style={{ height: 32, padding: '0 16px' }}>
                    {keySaving ? '保存中...' : keyEditingName ? '保存密钥' : '新增密钥'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}

      {showBatchModal && (
        <div className="cc-modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) setShowBatchModal(false) }}>
          <div className="cc-modal-shell cc-modal-shell--narrow">
            <div className="cc-modal-head">
              <h3>批量编辑服务器 <small>BATCH · {selected.size} 台</small></h3>
              <button className="cc-modal-close" onClick={() => setShowBatchModal(false)}>✕</button>
            </div>

            <div className="cc-modal-callout" style={{ marginBottom: 12 }}>
              已选择 <strong>{selected.size}</strong> 台：<span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, wordBreak: 'break-all' }}>{Array.from(selected).join(', ')}</span>
              <div style={{ marginTop: 4, fontSize: 11 }}>留空的字段将保持不变，只更新填写的字段。</div>
            </div>

            {batchError && (
              <div style={{ background: 'var(--danger-surface)', color: 'var(--danger)', padding: '10px', borderRadius: '6px', marginBottom: '16px', fontSize: '13px' }}>
                {batchError}
              </div>
            )}

            <form onSubmit={handleBatchSubmit} style={{ display: 'grid', gap: '14px' }}>
              <FormField label="用户名" value={batchForm.username}
                onChange={(v) => updateBatchField('username', v)} placeholder="留空保持不变" />
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: '12px' }}>
                <FormField label="端口" value={batchForm.port}
                  onChange={(v) => updateBatchField('port', v)} type="number" placeholder="留空保持不变" />
                <div>
                  <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px' }}>认证方式</label>
                  <select value={batchForm.auth_type} onChange={(e) => updateBatchField('auth_type', e.target.value)}
                    style={{
                      width: '100%', padding: '8px 12px', background: 'var(--bg-surface)', color: 'var(--text-primary)',
                      border: '1px solid var(--border-strong)', borderRadius: '6px', fontSize: '13px',
                    }}>
                    <option value="">保持不变</option>
                    <option value="password">密码</option>
                    <option value="key_file">密钥文件</option>
                    <option value="key_content">密钥内容</option>
                  </select>
                </div>
              </div>
              <div>
                <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '6px' }}>服务器状态</label>
                <select value={batchForm.status} onChange={(e) => updateBatchField('status', e.target.value)}
                  style={{
                    width: '100%', padding: '8px 12px', background: 'var(--bg-surface)', color: 'var(--text-primary)',
                    border: '1px solid var(--border-strong)', borderRadius: '6px', fontSize: '13px',
                  }}>
                  <option value="">保持不变</option>
                  <option value="online">在线 / 启用</option>
                  <option value="disabled">停用</option>
                  <option value="offline">离线</option>
                </select>
              </div>

              <div>
                <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '6px' }}>跳板机</label>
                <select value={batchForm.jump_host} onChange={(e) => updateBatchField('jump_host', e.target.value)}
                  style={{
                    width: '100%', padding: '8px 12px', background: 'var(--bg-surface)', color: 'var(--text-primary)',
                    border: '1px solid var(--border-strong)', borderRadius: '6px', fontSize: '13px',
                  }}>
                  <option value="">保持不变</option>
                  <option value="__clear__">清除跳板机</option>
                  {servers.map((s) => (
                    <option key={s.name} value={s.name}>{s.name} ({s.host}:{s.port || 22})</option>
                  ))}
                </select>
              </div>
              <div>
                <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '6px' }}>分组</label>
                <select value={batchForm.group} onChange={(e) => updateBatchField('group', e.target.value)}
                  style={{
                    width: '100%', padding: '8px 12px', background: 'var(--bg-surface)', color: 'var(--text-primary)',
                    border: '1px solid var(--border-strong)', borderRadius: '6px', fontSize: '13px',
                  }}>
                  <option value="">保持不变</option>
                  <option value="__clear__">移至未分组</option>
                  {groupNames.map((name: string) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </select>
              </div>
              <FormField label="描述" value={batchForm.description}
                onChange={(v) => updateBatchField('description', v)} placeholder="留空保持不变" />
              <FormField label="标签" value={batchForm.tags}
                onChange={(v) => updateBatchField('tags', v)} placeholder="逗号分隔，留空保持不变" />

              <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', marginTop: '8px' }}>
                <button type="button" className="btn" onClick={() => setShowBatchModal(false)}
                  style={{ padding: '8px 20px', background: 'var(--border-strong)', color: 'var(--text-primary)', fontSize: '14px' }}>
                  取消
                </button>
                <button type="submit" className="btn" disabled={batchSaving}
                  style={{ padding: '8px 24px', background: 'var(--purple)', color: 'var(--purple-text)', fontSize: '14px', fontWeight: 'bold' }}>
                  {batchSaving ? '保存中...' : `批量保存 (${selected.size} 台)`}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {monitorSetup && (
        <div className="cc-modal-overlay" style={{ zIndex: 1200 }} onClick={(e) => { if (e.target === e.currentTarget) setMonitorSetup(null) }}>
          <div className="cc-modal-shell" style={{ maxWidth: 560 }}>
            <div className="cc-modal-head">
              <h3>安装安全监控：{monitorSetup.name}<small>SECURITY MONITOR · SETUP</small></h3>
              <button className="cc-modal-close" onClick={() => setMonitorSetup(null)}>✕</button>
            </div>
            <div style={{ padding: '16px 20px' }}>
              <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 10 }}>
                自定义安装选项变量（每行 <code>KEY=VALUE</code>，<code>#</code> 开头为注释，注入安装脚本环境变量）。留空键使用默认选项；不依赖系统 .env。安装为远端写操作，提交后在任务中心查看进度。
              </p>
              <textarea
                value={monitorSetup.optionsText}
                onChange={(e) => setMonitorSetup((prev) => prev ? { ...prev, optionsText: e.target.value } : prev)}
                spellCheck={false}
                style={{ width: '100%', minHeight: 220, fontFamily: 'var(--font-mono)', fontSize: 12, padding: 10, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg, #f8f9fa)' }}
              />
              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 12 }}>
                <button className="btn" onClick={() => setMonitorSetup(null)} style={{ padding: '6px 16px' }}>取消</button>
                <button className="btn primary" onClick={confirmMonitorSetup} style={{ padding: '6px 16px' }}>确认安装</button>
              </div>
            </div>
          </div>
        </div>
      )}
        {groupsModalOpen && (
          <div className="cc-modal-overlay" style={{ zIndex: 1100 }} onClick={(e) => { if (e.target === e.currentTarget) setGroupsModalOpen(false) }}>
            <div className="cc-modal-shell cc-modal-shell--narrow">
              <div className="cc-modal-head">
                <h3>服务器分组 <small style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 6 }}>点击分组筛选列表</small></h3>
                <button className="cc-modal-close" onClick={() => setGroupsModalOpen(false)}>✕</button>
              </div>
              <div style={{ padding: '12px 20px', display: 'grid', gap: 6, maxHeight: 420, overflowY: 'auto' }}>
                <div
                  onClick={() => { setSelectedGroup(null); setGroupsModalOpen(false) }}
                  style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '9px 12px', borderRadius: 8, cursor: 'pointer',
                    border: `1px solid ${selectedGroup === null ? 'var(--brand, #3b82f6)' : 'var(--border)'}`,
                    background: selectedGroup === null ? 'var(--brand-surface, rgba(59,130,246,.08))' : 'transparent',
                    fontWeight: selectedGroup === null ? 600 : 400, fontSize: 13,
                  }}
                >
                  <span>全部服务器</span>
                  <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>{servers.length}</span>
                </div>
                <div
                  onClick={() => { setSelectedGroup(''); setGroupsModalOpen(false) }}
                  style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '9px 12px', borderRadius: 8, cursor: 'pointer',
                    border: `1px solid ${selectedGroup === '' ? 'var(--brand, #3b82f6)' : 'var(--border)'}`,
                    background: selectedGroup === '' ? 'var(--brand-surface, rgba(59,130,246,.08))' : 'transparent',
                    fontWeight: selectedGroup === '' ? 600 : 400, fontSize: 13,
                  }}
                >
                  <span style={{ fontStyle: 'italic', color: 'var(--text-muted)' }}>未分组</span>
                  <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>{ungroupedCount}</span>
                </div>
                {groupNames.map((name: string) => {
                  const active = selectedGroup === name
                  const count = groups.find((g: any) => g.name === name)?.server_count || 0
                  const color = getGroupColor(name)
                  return (
                    <div
                      key={name}
                      onClick={() => { setSelectedGroup(name); setGroupsModalOpen(false) }}
                      style={{
                        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                        padding: '9px 12px', borderRadius: 8, cursor: 'pointer',
                        border: `1px solid ${active ? 'var(--brand, #3b82f6)' : 'var(--border)'}`,
                        background: active ? 'var(--brand-surface, rgba(59,130,246,.08))' : 'transparent',
                        fontWeight: active ? 600 : 400, fontSize: 13,
                      }}
                    >
                      <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                        <span style={{ width: 8, height: 8, borderRadius: 999, background: color?.text, flexShrink: 0 }} />
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</span>
                      </span>
                      <span style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                        <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>{count}</span>
                        <button className="cc-icon-btn" title="重命名" onClick={(e) => { e.stopPropagation(); handleRenameGroup(name) }} style={{ height: 22, padding: '0 6px', fontSize: 11 }}>✎</button>
                        <button className="cc-icon-btn cc-icon-btn--danger" title="删除" onClick={(e) => { e.stopPropagation(); handleDeleteGroup(name) }} style={{ height: 22, padding: '0 6px', fontSize: 11 }}>×</button>
                      </span>
                    </div>
                  )
                })}
                <button className="cc-icon-btn" onClick={handleCreateGroup}
                  style={{ marginTop: 4, width: '100%', justifyContent: 'center', fontSize: 12, height: 32, background: 'transparent', border: '1px dashed var(--border-strong)', color: 'var(--text-muted)' }}>
                  + 新建分组
                </button>
                <p style={{ margin: '4px 0 0', fontSize: 11, color: 'var(--text-muted)' }}>
                  提示：服务器的分组归属可在行操作「编辑」中修改。
                </p>
              </div>
            </div>
          </div>
        )}
      <RiskConfirmDialog
        open={Boolean(riskAction)}
        title={riskAction?.title || ''}
        description={riskAction?.description}
        target={riskAction?.target || '-'}
        confirmText={riskAction?.confirmText || 'CONFIRM'}
        value=""
        onValueChange={() => {}}
        confirmMode="one-click"
        riskLevel={riskAction?.riskLevel || 'high'}
        details={riskAction?.details}
        confirmButtonLabel={riskAction?.confirmButtonLabel || '确认执行'}
        result={riskAction?.result}
        onCancel={() => setRiskAction(null)}
        onConfirm={async () => {
          const action = riskAction?.onConfirm
          if (!action) return
          const res = await action()
          setRiskAction((prev) => prev ? { ...prev, result: res || { success: true, message: '操作已完成' } } : null)
        }}
      />
        {(error || success) && (
          <div style={{
            position: 'fixed',
            bottom: 24,
            right: 24,
            zIndex: 9999,
            maxWidth: 360,
            padding: '10px 16px',
            borderRadius: 8,
            boxShadow: '0 8px 24px rgba(0,0,0,.18)',
            fontSize: 13,
            background: error ? 'var(--danger-surface)' : 'var(--success-surface)',
            border: `1px solid ${error ? 'var(--danger-border)' : 'var(--success-border)'}`,
            color: error ? 'var(--danger)' : 'var(--success)',
          }}>
            {error || success}
          </div>
        )}
    </div>
    </div>
  )
}

function FormField({ label, value, onChange, type = 'text', placeholder, disabled }: {
  label: string
  value: string
  onChange: (v: string) => void
  type?: string
  placeholder?: string
  disabled?: boolean
}) {
  return (
    <div>
      <label style={{ display: 'block', color: 'var(--text-muted)', fontSize: 10.5, marginBottom: 4, fontFamily: 'var(--font-mono)', fontWeight: 600, letterSpacing: '0.1em', textTransform: 'uppercase' }}>{label}</label>
      <input className="cc-input" type={type} value={value} onChange={(e) => onChange(e.target.value)}
        disabled={disabled} placeholder={placeholder}
        style={{ width: '100%', opacity: disabled ? 0.6 : 1 }} />
    </div>
  )
}
