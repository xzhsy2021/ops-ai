import { useState, useEffect, useCallback } from 'react'
import { serverWorkbench } from '../../api'
import { RiskConfirmDialog } from '../ui'

interface FilesTabProps {
  name: string
}

export default function FilesTab({ name }: FilesTabProps) {
  const [path, setPath] = useState('/')
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [mkdirInput, setMkdirInput] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const [renaming, setRenaming] = useState<string | null>(null)
  const [renameInput, setRenameInput] = useState('')

  const [chmodding, setChmodding] = useState<string | null>(null)
  const [chmodInput, setChmodInput] = useState('')

  const [editing, setEditing] = useState<any | null>(null)
  const [editContent, setEditContent] = useState('')
  const [editLoading, setEditLoading] = useState(false)
  const [editSaving, setEditSaving] = useState(false)

  const [tailing, setTailing] = useState<any | null>(null)
  const [tailLines, setTailLines] = useState<string[]>([])
  const [tailLoading, setTailLoading] = useState(false)
  const [pendingRiskAction, setPendingRiskAction] = useState<any | null>(null)
  const [riskConfirmValue, setRiskConfirmValue] = useState('')

  const joinPath = (dir: string, fileName: string) => `${dir.replace(/\/$/, '')}/${fileName}`.replace(/\/+/g, '/')

  const flash = (msg: string, isError = false) => {
    if (isError) {
      setError(msg)
      setTimeout(() => setError(''), 4000)
    } else {
      setSuccess(msg)
      setTimeout(() => setSuccess(''), 3000)
    }
  }

  const loadDir = useCallback(async (dirPath: string) => {
    setLoading(true)
    setError('')
    try {
      const res: any = await serverWorkbench.sftpList(name, dirPath)
      setPath(res.data.path || dirPath)
      setItems(res.data.items || [])
    } catch (e: any) {
      const msg = typeof e === 'string' ? e : e?.response?.data?.detail || e?.message || '加载失败'
      flash(msg, true)
    }
    setLoading(false)
  }, [name])

  useEffect(() => { loadDir(path) }, [])

  const navigate = (item: any) => {
    if (item.is_dir) loadDir(item.path)
  }

  const goUp = () => {
    const parent = path.split('/').filter(Boolean).slice(0, -1).join('/')
    loadDir('/' + parent)
  }

  const handleMkdir = async () => {
    if (!mkdirInput.trim()) return
    try {
      const newPath = path.replace(/\/$/, '') + '/' + mkdirInput
      await serverWorkbench.sftpMkdir(name, newPath)
      setMkdirInput('')
      flash(`目录已创建: ${mkdirInput}`)
      loadDir(path)
    } catch (e: any) {
      flash(typeof e === 'string' ? e : e?.response?.data?.detail || e?.message || '创建失败', true)
    }
  }

  const handleDelete = (item: any) => {
    setRiskConfirmValue('')
    setPendingRiskAction({
      kind: 'delete',
      item,
      title: '确认删除远端文件',
      description: item.is_dir ? '将删除远端目录。请确认该目录不是当前服务运行目录或共享目录。' : '将删除远端文件。删除后可能需要从备份恢复。',
      target: item.path,
      confirmText: `DELETE ${item.path}`,
      confirmButtonLabel: '确认删除',
      riskLevel: item.is_dir ? 'critical' : 'high',
      details: [
        { label: '服务器', value: name },
        { label: '类型', value: item.is_dir ? '目录' : '文件' },
        { label: '大小', value: item.is_dir ? '-' : formatSize(Number(item.size || 0)) },
      ],
    })
  }

  const handleDownload = async (item: any) => {
    try {
      const res: any = await serverWorkbench.sftpDownload(name, item.path)
      const b64 = res.data?.content_base64
      if (b64) {
        const byteChars = atob(b64)
        const byteNums = new Array(byteChars.length)
        for (let i = 0; i < byteChars.length; i++) byteNums[i] = byteChars.charCodeAt(i)
        const byteArr = new Uint8Array(byteNums)
        const blob = new Blob([byteArr])
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = item.name
        a.click()
        URL.revokeObjectURL(url)
        flash(`下载完成: ${item.name}`)
      }
    } catch (e: any) {
      flash(typeof e === 'string' ? e : e?.response?.data?.detail || e?.message || '下载失败', true)
    }
  }

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const uploadOnce = async (allowOverwrite = false) => {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('remote_path', path)
      if (allowOverwrite) {
        const remoteFull = joinPath(path, file.name)
        formData.append('overwrite', 'true')
        formData.append('confirm_path', remoteFull)
      }
      return serverWorkbench.sftpUpload(name, formData)
    }
    try {
      await uploadOnce(false)
      flash(`上传完成: ${file.name}`)
      loadDir(path)
    } catch (e: any) {
      const msg = typeof e === 'string' ? e : e?.response?.data?.detail || e?.message || '上传失败'
      if (String(msg).includes('File already exists')) {
        const remoteFull = joinPath(path, file.name)
        setRiskConfirmValue('')
        setPendingRiskAction({
          kind: 'overwrite',
          file,
          remoteFull,
          title: '确认覆盖远端文件',
          description: '远端已存在同名文件。确认后将覆盖目标文件，建议先确认已备份或目标文件可恢复。',
          target: remoteFull,
          confirmText: `OVERWRITE ${remoteFull}`,
          confirmButtonLabel: '确认覆盖上传',
          riskLevel: 'high',
          details: [
            { label: '服务器', value: name },
            { label: '本地文件', value: file.name },
            { label: '文件大小', value: formatSize(file.size || 0) },
          ],
        })
      } else {
        flash(msg, true)
      }
    }
    e.target.value = ''
  }

  const handleRenameStart = (item: any) => {
    setRenaming(item.path)
    setRenameInput(item.name)
  }

  const handleRenameSubmit = async (oldPath: string) => {
    if (!renameInput.trim()) { setRenaming(null); return }
    const parentPath = oldPath.split('/').slice(0, -1).join('/') || '/'
    const newPath = parentPath + '/' + renameInput
    try {
      await serverWorkbench.sftpRename(name, oldPath, newPath)
      flash(`已重命名为: ${renameInput}`)
      loadDir(path)
    } catch (e: any) {
      flash(typeof e === 'string' ? e : e?.response?.data?.detail || e?.message || '重命名失败', true)
    }
    setRenaming(null)
    setRenameInput('')
  }

  const handleChmodStart = (item: any) => {
    setChmodding(item.path)
    setChmodInput(item.mode || '644')
  }

  const handleChmodSubmit = (itemPath: string) => {
    if (!/^[0-7]{3,4}$/.test(chmodInput)) {
      flash('权限模式必须是3-4位八进制数字 (如755, 0644)', true)
      return
    }
    setRiskConfirmValue('')
    setPendingRiskAction({
      kind: 'chmod',
      itemPath,
      mode: chmodInput,
      title: '确认修改远端权限',
      description: '权限变更可能影响服务运行、日志写入或安全边界。请确认目标路径和权限值。',
      target: itemPath,
      confirmText: `CHMOD ${chmodInput} ${itemPath}`,
      confirmButtonLabel: '确认修改权限',
      riskLevel: ['777', '0777'].includes(chmodInput) ? 'critical' : 'high',
      details: [
        { label: '服务器', value: name },
        { label: '目标权限', value: chmodInput },
      ],
    })
  }

  const closeRiskDialog = () => {
    const kind = pendingRiskAction?.kind
    setPendingRiskAction(null)
    setRiskConfirmValue('')
    if (kind === 'chmod') {
      setChmodding(null)
      setChmodInput('')
    }
  }

  const confirmRiskAction = async () => {
    const action = pendingRiskAction
    if (!action) return
    setPendingRiskAction(null)
    setRiskConfirmValue('')
    try {
      if (action.kind === 'delete') {
        await serverWorkbench.sftpDelete(name, action.item.path, action.item.path)
        flash(`已删除: ${action.item.name}`)
        loadDir(path)
      } else if (action.kind === 'overwrite') {
        const formData = new FormData()
        formData.append('file', action.file)
        formData.append('remote_path', path)
        formData.append('overwrite', 'true')
        formData.append('confirm_path', action.remoteFull)
        await serverWorkbench.sftpUpload(name, formData)
        flash(`已覆盖上传: ${action.file.name}`)
        loadDir(path)
      } else if (action.kind === 'chmod') {
        await serverWorkbench.sftpChmod(name, action.itemPath, action.mode)
        flash(`权限已更新: ${action.mode}`)
        setChmodding(null)
        setChmodInput('')
        loadDir(path)
      }
    } catch (e: any) {
      const fallback = action.kind === 'delete' ? '删除失败' : action.kind === 'overwrite' ? '覆盖上传失败' : '修改权限失败'
      flash(typeof e === 'string' ? e : e?.response?.data?.detail || e?.message || fallback, true)
    }
  }

  const handleEditStart = async (item: any) => {
    setEditing(item)
    setEditLoading(true)
    setEditContent('')
    try {
      const res: any = await serverWorkbench.sftpContent(name, item.path)
      setEditContent(res.data.content || '')
    } catch (e: any) {
      flash(typeof e === 'string' ? e : e?.response?.data?.detail || e?.message || '读取文件失败', true)
      setEditing(null)
    }
    setEditLoading(false)
  }

  const handleEditSave = async () => {
    if (!editing) return
    setEditSaving(true)
    try {
      await serverWorkbench.sftpContentSave(name, editing.path, editContent)
      flash(`已保存: ${editing.name}`)
      setEditing(null)
      loadDir(path)
    } catch (e: any) {
      flash(typeof e === 'string' ? e : e?.response?.data?.detail || e?.message || '保存失败', true)
    }
    setEditSaving(false)
  }

  const handleTailStart = async (item: any) => {
    setTailing(item)
    setTailLines([])
    setTailLoading(true)
    try {
      const res: any = await serverWorkbench.sftpTail(name, item.path, 300)
      setTailLines(res.data?.lines || [])
    } catch (e: any) {
      flash(typeof e === 'string' ? e : e?.response?.data?.detail || e?.message || 'Tail 读取失败', true)
      setTailing(null)
    }
    setTailLoading(false)
  }

  const formatSize = (size: number) => {
    if (size < 1024) return size + ' B'
    if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB'
    if (size < 1024 * 1024 * 1024) return (size / (1024 * 1024)).toFixed(1) + ' MB'
    return (size / (1024 * 1024 * 1024)).toFixed(2) + ' GB'
  }

  if (tailing) {
    return (
      <div style={{ display: 'grid', gap: '16px' }}>
        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
            <div>
              <h3 style={{ margin: 0, color: 'var(--brand)', fontSize: '14px' }}>日志 Tail: {tailing.path}</h3>
              <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginTop: '4px' }}>只读取文件尾部，避免大日志拖慢页面。</div>
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button className="btn" onClick={() => handleTailStart(tailing)} style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--action-bg)', color: 'var(--action-text)' }}>刷新 Tail</button>
              <a className="btn" href={serverWorkbench.sftpStreamDownloadUrl(name, tailing.path)} target="_blank" rel="noreferrer" style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--border-strong)', color: 'var(--text-primary)', textDecoration: 'none' }}>流式下载</a>
              <button className="btn" onClick={() => setTailing(null)} style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}>返回</button>
            </div>
          </div>
          {tailLoading ? (
            <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>读取中...</div>
          ) : (
            <pre style={{ maxHeight: '520px', overflow: 'auto', background: 'var(--bg-page)', border: '1px solid var(--border-strong)', borderRadius: '8px', padding: '12px', color: 'var(--text-primary)', fontSize: '12px', lineHeight: 1.55 }}>
              {tailLines.join('\n') || '无内容'}
            </pre>
          )}
        </div>
      </div>
    )
  }

  if (editing) {
    return (
      <div style={{ display: 'grid', gap: '16px' }}>
        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
            <h3 style={{ margin: 0, color: 'var(--brand)', fontSize: '14px' }}>编辑: {editing.path}</h3>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button className="btn" onClick={() => setEditing(null)}
                style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
                取消
              </button>
              <button className="btn" onClick={handleEditSave} disabled={editSaving}
                style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--success-border)', color: 'var(--success)' }}>
                {editSaving ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
          {editLoading ? (
            <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>加载中...</div>
          ) : (
            <textarea
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              style={{
                width: '100%', minHeight: '400px', fontFamily: 'monospace', fontSize: '13px',
                background: 'var(--bg-page)', color: 'var(--text-primary)', border: '1px solid var(--border-strong)',
                borderRadius: '6px', padding: '12px', resize: 'vertical',
              }}
            />
          )}
        </div>
      </div>
    )
  }

  return (
    <div style={{ display: 'grid', gap: '16px' }}>
      <div className="card">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <button className="btn" onClick={goUp}
            style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}
            disabled={path === '/'}>
            ↑ 上级
          </button>
          <span style={{ fontFamily: 'monospace', fontSize: '14px', wordBreak: 'break-all', display: 'inline-flex', alignItems: 'center', gap: '2px' }}>
            <span style={{ cursor: 'pointer', color: 'var(--brand-soft)' }} onClick={() => loadDir('/')} title="/">/</span>
            {path !== '/' && path.split('/').filter(Boolean).map((seg: string, i: number, arr: string[]) => {
              const partial = '/' + arr.slice(0, i + 1).join('/')
              return (
                <span key={partial} style={{ display: 'inline', color: i === arr.length - 1 ? 'var(--text-primary)' : 'var(--brand-soft)' }}>
                  <span
                    style={{ cursor: i < arr.length - 1 ? 'pointer' : 'default' }}
                    onClick={() => i < arr.length - 1 && loadDir(partial)}
                    title={partial}
                  >
                    {seg}
                  </span>
                  {i < arr.length - 1 && <span style={{ color: 'var(--border-stronger)' }}>/</span>}
                </span>
              )
            })}
          </span>
          <button className="btn" onClick={() => loadDir(path)}
            style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--action-bg)', color: 'var(--action-text)' }}>
            刷新
          </button>
          <label style={{ cursor: 'pointer', padding: '4px 12px', fontSize: '13px', background: 'var(--success-border)', color: 'var(--success)', borderRadius: '6px' }}>
            上传文件
            <input type="file" onChange={handleUpload} style={{ display: 'none' }} />
          </label>
          <div style={{ display: 'flex', gap: '4px', marginLeft: 'auto' }}>
            <input
              value={mkdirInput}
              onChange={(e) => setMkdirInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleMkdir() }}
              placeholder="新建目录..."
              style={{ width: '160px', fontSize: '13px' }}
            />
            <button className="btn" onClick={handleMkdir}
              style={{ padding: '4px 12px', fontSize: '13px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
              创建
            </button>
          </div>
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
        {loading ? (
          <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>加载中...</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-strong)', textAlign: 'left', position: 'sticky', top: 0, background: 'var(--bg-surface)' }}>
                <th style={{ padding: '8px', color: 'var(--text-secondary)', width: '30%' }}>名称</th>
                <th style={{ padding: '8px', color: 'var(--text-secondary)' }}>大小</th>
                <th style={{ padding: '8px', color: 'var(--text-secondary)' }}>权限</th>
                <th style={{ padding: '8px', color: 'var(--text-secondary)' }}>修改时间</th>
                <th style={{ padding: '8px', color: 'var(--text-secondary)', textAlign: 'right' }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item: any) => (
                <tr key={item.path} style={{ borderBottom: '1px solid var(--bg-surface)' }}>
                  <td style={{ padding: '6px 8px' }}>
                    {renaming === item.path ? (
                      <input
                        value={renameInput}
                        onChange={(e) => setRenameInput(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') handleRenameSubmit(item.path); if (e.key === 'Escape') setRenaming(null) }}
                        onBlur={() => handleRenameSubmit(item.path)}
                        autoFocus
                        style={{ width: '100%', fontSize: '13px', padding: '2px 6px' }}
                      />
                    ) : (
                      <span
                        onClick={() => item.is_dir && navigate(item)}
                        style={{
                          color: item.is_dir ? 'var(--brand)' : 'var(--text-secondary)',
                          cursor: item.is_dir ? 'pointer' : 'default',
                        }}
                      >
                        {item.is_dir ? '📁' : '📄'} {item.name}
                      </span>
                    )}
                  </td>
                  <td style={{ padding: '6px 8px', color: 'var(--text-muted)' }}>
                    {item.is_dir ? '-' : formatSize(item.size)}
                  </td>
                  <td style={{ padding: '6px 8px', fontFamily: 'monospace', color: 'var(--text-muted)', fontSize: '12px', cursor: 'pointer' }}
                    onClick={() => handleChmodStart(item)} title="点击修改权限">
                    {chmodding === item.path ? (
                      <input
                        value={chmodInput}
                        onChange={(e) => setChmodInput(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') handleChmodSubmit(item.path); if (e.key === 'Escape') setChmodding(null) }}
                        onBlur={() => handleChmodSubmit(item.path)}
                        autoFocus
                        style={{ width: '50px', fontSize: '12px', padding: '1px 4px' }}
                      />
                    ) : (
                      item.permissions
                    )}
                  </td>
                  <td style={{ padding: '6px 8px', color: 'var(--text-muted)', fontSize: '12px' }}>
                    {item.mtime ? new Date(item.mtime).toLocaleString() : '-'}
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                    {!item.is_dir && (
                      <>
                        <button className="btn" onClick={() => handleTailStart(item)}
                          style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--border-strong)', color: 'var(--action-text)', marginRight: '4px' }}>
                          Tail
                        </button>
                        <button className="btn" onClick={() => handleEditStart(item)}
                          style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--action-bg)', color: 'var(--action-text)', marginRight: '4px' }}>
                          编辑
                        </button>
                        <button className="btn" onClick={() => handleDownload(item)}
                          style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--action-bg)', color: 'var(--action-text)', marginRight: '4px' }}>
                          下载
                        </button>
                      </>
                    )}
                    <button className="btn" onClick={() => handleRenameStart(item)}
                      style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--border-strong)', color: 'var(--text-secondary)', marginRight: '4px' }}>
                      重命名
                    </button>
                    <button className="btn" onClick={() => handleDelete(item)}
                      style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--danger-surface)', color: 'var(--danger)' }}>
                      删除
                    </button>
                  </td>
                </tr>
              ))}
              {items.length === 0 && (
                <tr>
                  <td colSpan={5} style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>目录为空</td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      <RiskConfirmDialog
        open={Boolean(pendingRiskAction)}
        title={pendingRiskAction?.title || '确认高风险文件操作'}
        description={pendingRiskAction?.description}
        target={pendingRiskAction?.target || '-'}
        confirmText={pendingRiskAction?.confirmText || ''}
        value={riskConfirmValue}
        onValueChange={setRiskConfirmValue}
        onCancel={closeRiskDialog}
        onConfirm={confirmRiskAction}
        riskLevel={pendingRiskAction?.riskLevel || 'high'}
        details={pendingRiskAction?.details || []}
        confirmButtonLabel={pendingRiskAction?.confirmButtonLabel || '确认执行'}
        confirmMode="one-click"
      />
    </div>
  )
}
