import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { serverManagement, adminMaintenance } from '../api'
import { RiskConfirmDialog, PageHeader } from '../components/ui'
import { EnhancedDataTable } from '../components/EnhancedDataTable'
import type { EnhancedColumn } from '../components/EnhancedDataTable'
import { useCachedResource } from '../hooks/useCachedResource'

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
  config_status?: { status?: string; complete?: boolean; missing?: string[]; suggestions?: string[] }
  health_probe?: { status?: string; disk?: { available_kb: number } }
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
}

interface BatchForm {
  description: string
  tags: string
  jump_host: string
  username: string
  auth_type: string
  port: string
  group: string
}

const emptyForm: ServerForm = {
  name: '', host: '', port: 22, username: 'root', auth_type: 'password',
  password: '', key: '', key_content: '', jump_host: '', description: '', tags: '', group: '',
  sftp_allowed_roots: '/data, /opt, /var/log, /tmp',
}

const emptyBatchForm: BatchForm = {
  description: '', tags: '', jump_host: '', username: '', auth_type: '', port: '', group: '',
}

export default function ServerListPage() {
  const navigate = useNavigate()
  const [servers, setServers] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

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
  const [healthChecking, setHealthChecking] = useState<Record<string, boolean>>({})
  const [riskAction, setRiskAction] = useState<{
    title: string
    description?: string
    target: string
    confirmText: string
    confirmButtonLabel?: string
    riskLevel?: string
    details?: Array<{ label: string; value: React.ReactNode }>
    onConfirm: () => Promise<void> | void
  } | null>(null)

  const flash = (msg: string, isError = false) => {
    if (isError) { setError(msg); setTimeout(() => setError(''), 5000) }
    else { setSuccess(msg); setTimeout(() => setSuccess(''), 3000) }
  }

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const res: any = await serverManagement.list()
      setServers(res.data || [])
    } catch (e: any) {
      flash(typeof e === 'string' ? e : e?.message || '加载失败', true)
    }
    setLoading(false)
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

  const filteredServers = configFilter === 'all'
    ? groupedServers
    : groupedServers.filter((s) => (s.config_status?.status || (s.config_status?.complete ? 'passed' : 'blocked')) === configFilter)

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
    })
    setUploadedKeyName(keyPath ? keyPath.split('/').pop() || keyPath : '')
    setFormError('')
    setShowModal(true)
  }

  const handleDelete = async (s: any) => {
    setRiskAction({
      title: '删除服务器',
      description: '服务器资产会从平台移除，已有发布记录不会删除。',
      target: `${s.name} (${s.host})`,
      confirmText: `DELETE_SERVER ${s.name}`,
      confirmButtonLabel: '删除服务器',
      riskLevel: 'high',
      details: [{ label: '主机', value: s.host }, { label: '分组', value: s.group || '未分组' }],
      onConfirm: async () => {
        try {
          await serverManagement.delete(s.name)
          flash(`已删除: ${s.name}`)
          setSelected((prev) => { const next = new Set(prev); next.delete(s.name); return next })
          load()
        } catch (e: any) {
          flash(typeof e === 'string' ? e : '删除失败', true)
        }
      },
    })
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
      if (form.jump_host) {
        const selectedSrv = servers.find((s) => s.name === form.jump_host)
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
        }
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
    if (batchForm.jump_host) {
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
      onConfirm: async () => {
        try {
          await adminMaintenance.sshKeys.delete(name)
          if (form.key === name) updateField('key', '')
          flash(`已删除密钥: ${name}`)
          loadKeys()
        } catch (err: any) {
          flash(typeof err === 'string' ? err : '删除密钥失败', true)
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
      onConfirm: async () => {
        try {
          await serverManagement.groups.delete(name)
          flash(`已删除分组: ${name}`)
          if (selectedGroup === name) setSelectedGroup(null)
          load()
          loadGroups()
        } catch (err: any) {
          flash(typeof err === 'string' ? err : '删除分组失败', true)
        }
      },
    })
  }

  const ungroupedCount = servers.filter((s) => !s.group).length

  const serverColumns: EnhancedColumn<ServerRow>[] = [
    {
      key: 'name', title: '名称',
      render: (s: ServerRow) => (
        <span onClick={() => window.open(`/servers/${encodeURIComponent(s.name)}?standalone`, '_blank')}
          style={{ color: 'var(--brand)', cursor: 'pointer', fontWeight: 'bold' }}>
          {s.name}
        </span>
      ),
    },
    { key: 'host', title: '地址', render: (s: ServerRow) => <span style={{ fontFamily: 'monospace', color: 'var(--text-secondary)' }}>{s.host}</span> },
    { key: 'port', title: '端口', render: (s: ServerRow) => <span style={{ color: 'var(--text-muted)' }}>{s.port || 22}</span> },
    { key: 'user', title: '用户', render: (s: ServerRow) => <span style={{ color: 'var(--text-secondary)' }}>{s.username || s.user || 'root'}</span> },
    {
      key: 'auth', title: '认证',
      render: (s: ServerRow) => {
        const authMode = s.auth_type || (s.key_content ? 'key_content' : s.key || s.key_file ? 'key_file' : 'password')
        return (
          <span style={{
            color: authMode === 'password' ? 'var(--warning)' : authMode === 'key_file' ? 'var(--success)' : 'var(--purple-text)',
            background: authMode === 'password' ? 'var(--warning-surface)' : authMode === 'key_file' ? 'var(--success-surface)' : 'var(--purple-surface)',
            padding: '1px 8px', borderRadius: '4px', fontSize: '12px',
          }}>
            {authMode === 'password' ? '密码' : authMode === 'key_file' ? '密钥文件' : '密钥内容'}
          </span>
        )
      },
    },
    {
      key: 'group', title: '分组',
      render: (s: ServerRow) => {
        if (!s.group) return <span style={{ color: 'var(--border-stronger)', fontSize: '12px' }}>—</span>
        const groupColor = getGroupColor(s.group)
        return <span style={{ color: groupColor?.text, background: groupColor?.bg, padding: '1px 8px', borderRadius: '4px', fontSize: '12px' }}>{s.group}</span>
      },
    },
    {
      key: 'jump', title: '跳板机',
      render: (s: ServerRow) => {
        const jumpLabel = typeof s.jump_host === 'string' ? s.jump_host : s.jump_host?.name || ''
        return <span style={{ color: jumpLabel ? 'var(--action-text)' : 'var(--text-muted)', fontSize: '12px' }}>{jumpLabel || '-'}</span>
      },
    },
    {
      key: 'desc', title: '描述',
      render: (s: ServerRow) => <span style={{ color: 'var(--text-muted)', maxWidth: '180px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'block' }}>{s.description || '-'}</span>,
    },
    {
      key: 'config', title: '配置/健康',
      render: (s: ServerRow) => {
        const cfg = s.config_status || {}
        const cfgStatus = cfg.status || (cfg.complete ? 'passed' : 'blocked')
        const health = s.health_probe || {}
        const healthLabel = health.status === 'passed' ? '在线' : health.status === 'blocked' ? '异常' : '未检查'
        return (
          <div style={{ display: 'grid', gap: '4px' }}>
            <span title={(cfg.missing || []).join(', ') || (cfg.suggestions || []).join('；')} style={{
              display: 'inline-block', width: 'fit-content', padding: '1px 8px', borderRadius: '4px', fontSize: '12px',
              color: cfgStatus === 'blocked' ? 'var(--danger)' : cfgStatus === 'warning' ? 'var(--warning)' : 'var(--success)',
              background: cfgStatus === 'blocked' ? 'var(--danger-surface)' : cfgStatus === 'warning' ? 'var(--warning-surface)' : 'var(--success-surface)',
            }}>{cfgStatus === 'blocked' ? '配置缺失' : cfgStatus === 'warning' ? '有建议' : '配置完整'}</span>
            <span style={{ color: health.status === 'passed' ? 'var(--success)' : health.status === 'blocked' ? 'var(--danger)' : 'var(--text-muted)', fontSize: '12px' }}>
              {healthLabel}{health.disk?.available_kb ? ` · 可用 ${Math.round(health.disk.available_kb / 1024 / 1024)}GB` : ''}
            </span>
          </div>
        )
      },
    },
    {
      key: 'actions', title: '操作', align: 'right',
      render: (s: ServerRow) => (
        <div style={{ whiteSpace: 'nowrap' }}>
          <button className="btn" onClick={() => handleHealthProbe(s.name)} disabled={healthChecking[s.name]}
            style={{ padding: '4px 12px', fontSize: '12px', background: 'var(--border-strong)', color: 'var(--action-text)', marginRight: '6px' }}>
            {healthChecking[s.name] ? '检查中' : '健康'}
          </button>
          <button className="btn" onClick={() => navigate(`/servers/${encodeURIComponent(s.name)}`)}
            style={{ padding: '4px 12px', fontSize: '12px', background: 'var(--action-bg)', color: 'var(--action-text)', marginRight: '6px' }}>
            详情
          </button>
          <button className="btn" onClick={() => openEdit(s)}
            style={{ padding: '4px 12px', fontSize: '12px', background: 'var(--border-strong)', color: 'var(--text-secondary)', marginRight: '6px' }}>
            编辑
          </button>
          <button className="btn" onClick={() => handleDelete(s)}
            style={{ padding: '4px 12px', fontSize: '12px', background: 'var(--danger-surface)', color: 'var(--danger)' }}>
            删除
          </button>
        </div>
      ),
    },
  ]

  return (
    <div style={{ display: 'grid', gap: '16px' }}>
      <PageHeader
        title="服务器管理"
        description={`共 ${servers.length} 台服务器`}
        badge={<span className="tag">{selectedGroup || '全部'}</span>}
        breadcrumbs={[
          { label: '基础设施', href: '/servers' },
          { label: '服务器列表' },
        ]}
      />
    <div style={{ display: 'flex', gap: '16px', minHeight: 0 }}>
      <div style={{
        width: '220px', minWidth: '220px', background: 'var(--bg-surface)',
        borderRadius: '8px', padding: '12px 0', display: 'flex', flexDirection: 'column',
        border: '1px solid var(--border-strong)',
      }}>
        <div style={{ padding: '0 12px 10px', borderBottom: '1px solid var(--border-strong)', marginBottom: '4px' }}>
          <span style={{ fontSize: '13px', fontWeight: 'bold', color: 'var(--text-secondary)' }}>服务器分组</span>
        </div>

        <div style={{ flex: 1, overflowY: 'auto' }}>
          <div
            onClick={() => setSelectedGroup(null)}
            onDragOver={(e) => handleGroupDragOver(e, '__all__')}
            onDragLeave={handleGroupDragLeave}
            onDrop={(e) => handleGroupDrop(e, '__all__')}
            style={{
              padding: '8px 12px', cursor: 'pointer', display: 'flex',
              justifyContent: 'space-between', alignItems: 'center',
              background: selectedGroup === null ? 'rgba(59, 130, 246, 0.15)' : dragOverGroup === '__all__' ? 'rgba(59, 130, 246, 0.1)' : undefined,
              borderLeft: selectedGroup === null ? '3px solid var(--brand)' : '3px solid transparent',
              transition: 'background 0.15s',
            }}
          >
            <span style={{ fontSize: '13px', color: selectedGroup === null ? 'var(--text-primary)' : 'var(--text-secondary)' }}>全部</span>
            <span style={{
              fontSize: '11px', background: 'var(--border-strong)', color: 'var(--text-secondary)',
              padding: '1px 7px', borderRadius: '10px',
            }}>{servers.length}</span>
          </div>

          {groupNames.map((name: string) => {
            const color = getGroupColor(name)
            const groupData = groups.find((g) => g.name === name)
            const count = groupData?.server_count || 0
            return (
              <div
                key={name}
                onClick={() => setSelectedGroup(name)}
                onDragOver={(e) => handleGroupDragOver(e, name)}
                onDragLeave={handleGroupDragLeave}
                onDrop={(e) => handleGroupDrop(e, name)}
                style={{
                  padding: '8px 12px', cursor: 'pointer', display: 'flex',
                  justifyContent: 'space-between', alignItems: 'center', position: 'relative',
                  background: selectedGroup === name ? 'rgba(59, 130, 246, 0.15)' : dragOverGroup === name ? 'rgba(59, 130, 246, 0.1)' : undefined,
                  borderLeft: selectedGroup === name ? '3px solid var(--brand)' : '3px solid transparent',
                  transition: 'background 0.15s',
                }}
                onMouseEnter={(e) => {
                  const el = e.currentTarget.querySelector('.group-actions') as HTMLElement
                  if (el) el.style.opacity = '1'
                }}
                onMouseLeave={(e) => {
                  const el = e.currentTarget.querySelector('.group-actions') as HTMLElement
                  if (el) el.style.opacity = '0'
                  setGroupActionsOpen(null)
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', overflow: 'hidden', flex: 1 }}>
                  <span style={{
                    width: '8px', height: '8px', borderRadius: '50%', flexShrink: 0,
                    background: color.text,
                  }} />
                  <span style={{
                    fontSize: '13px', color: selectedGroup === name ? 'var(--text-primary)' : 'var(--text-secondary)',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>{name}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <span style={{
                    fontSize: '11px', background: 'var(--border-strong)', color: 'var(--text-secondary)',
                    padding: '1px 7px', borderRadius: '10px',
                  }}>{count}</span>
                  <div className="group-actions" style={{ opacity: 0, transition: 'opacity 0.15s', position: 'relative' }}>
                    <button
                      className="btn"
                      onClick={(e) => { e.stopPropagation(); setGroupActionsOpen(groupActionsOpen === name ? null : name) }}
                      style={{ padding: '0 4px', fontSize: '14px', background: 'transparent', color: 'var(--text-muted)', lineHeight: 1 }}
                    >
                      ⋮
                    </button>
                    {groupActionsOpen === name && (
                      <div style={{
                        position: 'absolute', right: 0, top: '100%', zIndex: 100,
                        background: 'var(--bg-surface)', border: '1px solid var(--border-stronger)', borderRadius: '6px',
                        padding: '4px 0', minWidth: '80px', boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
                      }}>
                        <button className="btn" onClick={(e) => { e.stopPropagation(); setGroupActionsOpen(null); handleRenameGroup(name) }}
                          style={{ display: 'block', width: '100%', textAlign: 'left', padding: '6px 12px', fontSize: '12px', background: 'transparent', color: 'var(--text-secondary)' }}>
                          重命名
                        </button>
                        <button className="btn" onClick={(e) => { e.stopPropagation(); setGroupActionsOpen(null); handleDeleteGroup(name) }}
                          style={{ display: 'block', width: '100%', textAlign: 'left', padding: '6px 12px', fontSize: '12px', background: 'transparent', color: 'var(--danger)' }}>
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
            onClick={() => setSelectedGroup('')}
            onDragOver={(e) => handleGroupDragOver(e, '__ungrouped__')}
            onDragLeave={handleGroupDragLeave}
            onDrop={(e) => handleGroupDrop(e, '__ungrouped__')}
            style={{
              padding: '8px 12px', cursor: 'pointer', display: 'flex',
              justifyContent: 'space-between', alignItems: 'center',
              background: selectedGroup === '' ? 'rgba(59, 130, 246, 0.15)' : dragOverGroup === '__ungrouped__' ? 'rgba(59, 130, 246, 0.1)' : undefined,
              borderLeft: selectedGroup === '' ? '3px solid var(--brand)' : '3px solid transparent',
              borderTop: groupNames.length > 0 ? '1px solid var(--border-strong)' : undefined,
              marginTop: groupNames.length > 0 ? '4px' : undefined,
              transition: 'background 0.15s',
            }}
          >
            <span style={{ fontSize: '13px', color: selectedGroup === '' ? 'var(--text-primary)' : 'var(--text-muted)', fontStyle: 'italic' }}>未分组</span>
            <span style={{
              fontSize: '11px', background: 'var(--border-strong)', color: 'var(--text-muted)',
              padding: '1px 7px', borderRadius: '10px',
            }}>{ungroupedCount}</span>
          </div>
        </div>

        <div style={{ padding: '8px 12px 0', borderTop: '1px solid var(--border-strong)', marginTop: '4px', paddingTop: '8px' }}>
          <button className="btn" onClick={handleCreateGroup}
            style={{
              width: '100%', padding: '6px 0', fontSize: '12px',
              background: 'var(--bg-page)', color: 'var(--text-muted)', border: '1px dashed var(--border-strong)',
              borderRadius: '6px',
            }}>
            + 新建分组
          </button>
        </div>
      </div>

      <div style={{ flex: 1, display: 'grid', gap: '16px', minWidth: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
          <h2 style={{ margin: 0 }}>
            服务器管理
            {selectedGroup !== null && (
              <span style={{ fontSize: '13px', fontWeight: 'normal', color: 'var(--text-muted)', marginLeft: '8px' }}>
                — {selectedGroup === '' ? '未分组' : selectedGroup} ({filteredServers.length})
              </span>
            )}
          </h2>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
            <select value={configFilter} onChange={(e) => setConfigFilter(e.target.value as any)}
              style={{ padding: '7px 10px', borderRadius: '8px', background: 'var(--bg-surface)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)' }}>
              <option value="all">全部配置</option>
              <option value="passed">配置完整 ({configCounts.passed || 0})</option>
              <option value="warning">有建议 ({configCounts.warning || 0})</option>
              <option value="blocked">配置缺失 ({configCounts.blocked || 0})</option>
            </select>
            {selected.size >= 2 && (
              <button className="btn" onClick={openBatchEdit}
                style={{ padding: '8px 20px', background: 'var(--purple)', color: 'var(--purple-text)', fontSize: '14px', fontWeight: 'bold' }}>
                批量编辑 ({selected.size})
              </button>
            )}
            {selected.size > 0 && (
              <button className="btn" onClick={() => setSelected(new Set())}
                style={{ padding: '8px 16px', background: 'var(--border-strong)', color: 'var(--text-secondary)', fontSize: '13px' }}>
                取消选择
              </button>
            )}
            <button className="btn" onClick={openKeyCreate}
              style={{ padding: '8px 16px', background: 'var(--bg-surface)', color: 'var(--action-text)', border: '1px solid var(--border-strong)', fontSize: '13px' }}>
              🔑 密钥管理 ({sshKeys.length})
            </button>
            <button className="btn" onClick={openCreate}
              style={{ padding: '8px 20px', background: 'var(--success-border)', color: 'var(--success)', fontSize: '14px', fontWeight: 'bold' }}>
              + 新增服务器
            </button>
          </div>
        </div>

        {error && (
          <div className="card" style={{ background: 'var(--danger-surface)', borderColor: 'var(--danger-border)', color: 'var(--danger)', fontSize: '13px' }}>
            {error}
          </div>
        )}
        {success && (
          <div className="card" style={{ background: 'var(--success-surface)', borderColor: 'var(--success-border)', color: 'var(--success)', fontSize: '13px' }}>
            {success}
          </div>
        )}

        <div className="card" style={{ padding: 0, overflow: 'auto' }}>
          <EnhancedDataTable
            rows={filteredServers}
            columns={serverColumns}
            rowKey={(s: ServerRow) => s.name}
            loading={loading}
            emptyTitle={selectedGroup !== null ? '该分组下暂无服务器' : '暂无服务器配置，点击上方 "新增服务器" 添加'}
            selectedKeys={Array.from(selected)}
            onSelectionChange={(keys) => {
              const newSet = new Set<string>(keys)
              setSelected(newSet)
            }}
          />
        </div>
      </div>

      {showModal && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }} onClick={(e) => { if (e.target === e.currentTarget) setShowModal(false) }}>
          <div className="card" style={{ width: '620px', maxHeight: '90vh', overflowY: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
              <h3 style={{ margin: 0 }}>{editing ? `编辑服务器: ${editing}` : '新增服务器'}</h3>
              <button className="btn" onClick={() => setShowModal(false)}
                style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--border-strong)', color: 'var(--text-secondary)' }}>
                ✕
              </button>
            </div>

            {formError && (
              <div style={{ background: 'var(--danger-surface)', color: 'var(--danger)', padding: '10px', borderRadius: '6px', marginBottom: '16px', fontSize: '13px' }}>
                {formError}
              </div>
            )}

            <form onSubmit={handleSubmit} style={{ display: 'grid', gap: '14px' }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <FormField label="名称 *" value={form.name} onChange={(v) => updateField('name', v)} placeholder="server-name"
                  disabled={!!editing} />
                <FormField label="主机地址 *" value={form.host} onChange={(v) => updateField('host', v)} placeholder="10.0.0.1 或 hostname" />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
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
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1100,
        }} onClick={(e) => { if (e.target === e.currentTarget) setShowKeyManager(false) }}>
          <div className="card" style={{ width: '760px', maxHeight: '90vh', overflowY: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
              <div>
                <h3 style={{ margin: 0 }}>SSH 密钥管理</h3>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>密钥保存在项目 keys/ 目录，列表仅展示元数据；编辑时才读取内容。</div>
              </div>
              <button className="btn" onClick={() => setShowKeyManager(false)}
                style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--border-strong)', color: 'var(--text-secondary)' }}>✕</button>
            </div>

            {keyError && (
              <div style={{ background: 'var(--danger-surface)', color: 'var(--danger)', padding: '10px', borderRadius: '6px', marginBottom: '12px', fontSize: '13px' }}>{keyError}</div>
            )}

            <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr', gap: '16px' }}>
              <div style={{ border: '1px solid var(--border-strong)', borderRadius: '8px', overflow: 'hidden' }}>
                <div style={{ padding: '10px 12px', background: 'var(--bg-page)', color: 'var(--text-secondary)', fontSize: '13px', fontWeight: 'bold' }}>已保存密钥</div>
                <div style={{ maxHeight: '360px', overflowY: 'auto' }}>
                  {sshKeys.length === 0 ? (
                    <div style={{ padding: '18px 12px', color: 'var(--text-muted)', fontSize: '13px' }}>暂无保存的密钥</div>
                  ) : sshKeys.map((k) => (
                    <div key={k.name} style={{ padding: '10px 12px', borderTop: '1px solid var(--bg-surface)', display: 'grid', gap: '6px' }}>
                      <div style={{ color: 'var(--text-primary)', fontSize: '13px', fontFamily: 'monospace', wordBreak: 'break-all' }}>{k.name}</div>
                      <div style={{ color: 'var(--text-muted)', fontSize: '12px' }}>{k.size} bytes · {new Date(k.modified * 1000).toLocaleString()}</div>
                      <div style={{ display: 'flex', gap: '6px' }}>
                        <button type="button" className="btn" onClick={() => { updateField('key', k.name); setUploadedKeyName(k.name); setShowKeyManager(false) }}
                          style={{ padding: '4px 8px', fontSize: '12px', background: 'var(--success-surface)', color: 'var(--success)' }}>选择</button>
                        <button type="button" className="btn" onClick={() => openKeyEdit(k.name)}
                          style={{ padding: '4px 8px', fontSize: '12px', background: 'var(--border-strong)', color: 'var(--action-text)' }}>编辑</button>
                        <button type="button" className="btn" onClick={() => handleKeyDelete(k.name)}
                          style={{ padding: '4px 8px', fontSize: '12px', background: 'var(--danger-surface)', color: 'var(--danger)' }}>删除</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <form onSubmit={handleKeyManagerSubmit} style={{ display: 'grid', gap: '12px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ color: 'var(--text-primary)' }}>{keyEditingName ? `编辑: ${keyEditingName}` : '新增密钥'}</strong>
                  {keyEditingName && (
                    <button type="button" className="btn" onClick={() => { setKeyEditingName(null); setKeyFormName(''); setKeyFormContent(''); setKeyError('') }}
                      style={{ padding: '4px 10px', fontSize: '12px', background: 'var(--border-strong)', color: 'var(--text-secondary)' }}>新建</button>
                  )}
                </div>
                <FormField label="密钥名称" value={keyFormName} onChange={setKeyFormName} placeholder="prod-web.pem 或 prod-web-id_rsa" />
                <div>
                  <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px' }}>密钥内容</label>
                  <textarea value={keyFormContent} onChange={(e) => setKeyFormContent(e.target.value)}
                    placeholder={"-----BEGIN OPENSSH PRIVATE KEY-----\n..."}
                    style={{ width: '100%', height: '230px', fontFamily: 'monospace', fontSize: '12px', background: 'var(--bg-page)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)', borderRadius: '6px', padding: '8px', resize: 'vertical' }} />
                </div>
                <div style={{ background: 'var(--warning-surface)', border: '1px solid var(--warning-border)', color: 'var(--warning)', padding: '8px 10px', borderRadius: '6px', fontSize: '12px' }}>
                  安全提示：删除或重命名密钥不会自动更新已引用旧名称的服务器，请先确认引用关系。
                </div>
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                  <button type="button" className="btn" onClick={() => setShowKeyManager(false)}
                    style={{ padding: '8px 18px', background: 'var(--border-strong)', color: 'var(--text-primary)', fontSize: '14px' }}>关闭</button>
                  <button type="submit" className="btn" disabled={keySaving}
                    style={{ padding: '8px 22px', background: 'var(--success-border)', color: 'var(--success)', fontSize: '14px', fontWeight: 'bold' }}>
                    {keySaving ? '保存中...' : keyEditingName ? '保存密钥' : '新增密钥'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}

      {showBatchModal && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }} onClick={(e) => { if (e.target === e.currentTarget) setShowBatchModal(false) }}>
          <div className="card" style={{ width: '560px', maxHeight: '90vh', overflowY: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
              <h3 style={{ margin: 0 }}>批量编辑服务器 ({selected.size} 台)</h3>
              <button className="btn" onClick={() => setShowBatchModal(false)}
                style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--border-strong)', color: 'var(--text-secondary)' }}>
                ✕
              </button>
            </div>

            <div style={{
              background: 'rgba(124, 58, 237, 0.15)', border: '1px solid var(--purple)',
              padding: '10px 14px', borderRadius: '8px', marginBottom: '16px', fontSize: '13px', color: 'var(--purple-text)',
            }}>
              已选择 {selected.size} 台服务器：{Array.from(selected).join(', ')}
              <div style={{ marginTop: '6px', fontSize: '12px', color: 'var(--purple-text)' }}>
                留空的字段将保持不变，只更新填写的字段。
              </div>
            </div>

            {batchError && (
              <div style={{ background: 'var(--danger-surface)', color: 'var(--danger)', padding: '10px', borderRadius: '6px', marginBottom: '16px', fontSize: '13px' }}>
                {batchError}
              </div>
            )}

            <form onSubmit={handleBatchSubmit} style={{ display: 'grid', gap: '14px' }}>
              <FormField label="用户名" value={batchForm.username}
                onChange={(v) => updateBatchField('username', v)} placeholder="留空保持不变" />
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
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
        onCancel={() => setRiskAction(null)}
        onConfirm={async () => {
          const action = riskAction?.onConfirm
          setRiskAction(null)
          await action?.()
        }}
      />
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
      <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px' }}>{label}</label>
      <input type={type} value={value} onChange={(e) => onChange(e.target.value)}
        disabled={disabled} placeholder={placeholder}
        style={{ width: '100%', background: disabled ? 'var(--bg-page)' : 'var(--bg-surface)', color: disabled ? 'var(--text-muted)' : 'var(--text-primary)',
          opacity: disabled ? 0.6 : 1 }} />
    </div>
  )
}
