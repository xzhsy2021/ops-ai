import { useState, useEffect } from 'react'
import { serverGroups } from '../api'
import { useAuthStore, useNotificationStore } from '../store'
import { ConfirmDialog } from '../components/ui'

export default function ServerGroupsPage() {
  const { user } = useAuthStore()
  const notify = useNotificationStore((state) => state.addMessage)
  const isAdmin = user?.is_admin
  const [groups, setGroups] = useState<any[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [editId, setEditId] = useState<string | null>(null)
  const [form, setForm] = useState({ name: '', display_name: '', description: '', server_names: '', tags: '' })
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null)

  const load = () => {
    serverGroups.list().then((res: any) => setGroups(res.data || [])).catch(() => {})
  }

  useEffect(() => { load() }, [])

  const handleCreate = async () => {
    if (!form.name) return notify('请输入组名', 'error')
    try {
      await serverGroups.create({
        name: form.name,
        display_name: form.display_name,
        description: form.description,
        server_names: form.server_names.split(',').map((s: string) => s.trim()).filter(Boolean),
        tags: form.tags.split(',').map((s: string) => s.trim()).filter(Boolean),
      })
      setShowCreate(false)
      resetForm()
      load()
      notify('服务器组已创建', 'success')
    } catch (e: any) { notify('创建失败', 'error') }
  }

  const handleUpdate = async () => {
    if (!editId || !form.name) return notify('请输入组名', 'error')
    try {
      await serverGroups.update(editId, {
        name: form.name,
        display_name: form.display_name,
        description: form.description,
        server_names: form.server_names.split(',').map((s: string) => s.trim()).filter(Boolean),
        tags: form.tags.split(',').map((s: string) => s.trim()).filter(Boolean),
      })
      setEditId(null)
      resetForm()
      load()
      notify('服务器组已更新', 'success')
    } catch (e: any) { notify('更新失败', 'error') }
  }

  const startEdit = (g: any) => {
    setEditId(g.id)
    setShowCreate(false)
    setForm({
      name: g.name,
      display_name: g.display_name || '',
      description: g.description || '',
      server_names: (g.server_names || []).join(', '),
      tags: (g.tags || []).join(', '),
    })
  }

  const resetForm = () => {
    setForm({ name: '', display_name: '', description: '', server_names: '', tags: '' })
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try { await serverGroups.delete(deleteTarget.id); setDeleteTarget(null); load(); notify('服务器组已删除', 'success') } catch (e: any) { notify('删除失败', 'error') }
  }

  return (
    <div style={{ display: 'grid', gap: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}>服务器组</h2>
        {isAdmin && (
          <button className="btn btn-primary" onClick={() => { setShowCreate(true); setEditId(null); resetForm() }}>+ 新建组</button>
        )}
      </div>

      {(showCreate || editId) && isAdmin && (
        <div className="card">
          <h3>{editId ? '编辑服务器组' : '新建服务器组'}</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginTop: '12px' }}>
            <input placeholder="组名 *" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input placeholder="显示名" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
            <input placeholder="服务器名(逗号分隔)" value={form.server_names} onChange={(e) => setForm({ ...form, server_names: e.target.value })} />
            <input placeholder="标签(逗号分隔)" value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} />
          </div>
          <div style={{ display: 'flex', gap: '12px', marginTop: '16px' }}>
            <button className="btn btn-primary" onClick={editId ? handleUpdate : handleCreate}>
              {editId ? '保存' : '确认'}
            </button>
            <button className="btn" onClick={() => { setShowCreate(false); setEditId(null); resetForm() }}>取消</button>
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gap: '12px' }}>
        {groups.map((g: any) => (
          <div key={g.id} className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontSize: '16px', fontWeight: 'bold' }}>{g.display_name || g.name}</div>
              <div style={{ color: 'var(--text-muted)', fontSize: '13px', marginTop: '4px' }}>{g.name}</div>
              {g.description && <div style={{ color: 'var(--text-secondary)', fontSize: '13px', marginTop: '4px' }}>{g.description}</div>}
              <div style={{ display: 'flex', gap: '6px', marginTop: '8px', flexWrap: 'wrap' }}>
                {(g.server_names || []).map((n: string) => (
                  <span key={n} style={{ background: 'var(--bg-page)', padding: '2px 8px', borderRadius: '4px', fontSize: '12px', color: 'var(--brand)' }}>{n}</span>
                ))}
              </div>
              {(g.tags || []).length > 0 && (
                <div style={{ display: 'flex', gap: '4px', marginTop: '6px', flexWrap: 'wrap' }}>
                  {(g.tags || []).map((t: string) => (
                    <span key={t} style={{ background: 'var(--brand-surface)', padding: '1px 6px', borderRadius: '3px', fontSize: '11px', color: 'var(--action-text)' }}>{t}</span>
                  ))}
                </div>
              )}
            </div>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              <span style={{ color: 'var(--text-muted)', fontSize: '13px' }}>{g.server_names?.length || 0} 台服务器</span>
              {isAdmin && (
                <>
                  <button className="btn" onClick={() => startEdit(g)}
                    style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}>编辑</button>
                  <button className="btn" onClick={() => setDeleteTarget({ id: g.id, name: g.name })}
                    style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--danger-surface)', color: 'var(--danger)' }}>删除</button>
                </>
              )}
            </div>
          </div>
        ))}
        {groups.length === 0 && <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '40px' }}>暂无服务器组</div>}
      </div>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除服务器组"
        description={`确认删除服务器组 "${deleteTarget?.name || ''}"？组内服务器不会删除，只会变为未分组。`}
        confirmLabel="删除分组"
        danger
        onCancel={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
      />
    </div>
  )
}
