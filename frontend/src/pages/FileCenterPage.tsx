import { useState, useEffect, useRef } from 'react'
import type { CSSProperties } from 'react'
import { files } from '../api'
import { ConfirmDialog, PageHeader } from '../components/ui'

const inputStyle: CSSProperties = { width: '100%', padding: '8px 10px', border: '1px solid var(--border-strong)', borderRadius: '10px', background: 'var(--bg-surface)', color: 'var(--text-primary)' }

export default function FileCenterPage() {
  const [activeTab, setActiveTab] = useState<'packages' | 'cleanup'>('packages')
  const [packages, setPackages] = useState<any[]>([])
  const [loadingPkg, setLoadingPkg] = useState(true)
  const [checksums, setChecksums] = useState<Record<string, string>>({})
  const [checking, setChecking] = useState<Record<string, boolean>>({})
  const [uploadMsg, setUploadMsg] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadSystem, setUploadSystem] = useState('')
  const [uploadService, setUploadService] = useState('')
  const [overwrite, setOverwrite] = useState(false)
  const [retention, setRetention] = useState<Record<string, any>>({})
  const [retentionMsg, setRetentionMsg] = useState('')
  const [cleanupPreview, setCleanupPreview] = useState<any>(null)
  const [cleanupBusy, setCleanupBusy] = useState(false)
  const [cleanupConfirmOpen, setCleanupConfirmOpen] = useState(false)
  const [deletePackageCandidate, setDeletePackageCandidate] = useState<any | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadPackages = () => {
    setLoadingPkg(true)
    files.deployPackages().then((res: any) => {
      setPackages(res.data || [])
    }).catch(() => {}).finally(() => setLoadingPkg(false))
  }

  const loadRetention = () => {
    files.packageRetention().then((res: any) => setRetention(res.data || {})).catch(() => {})
  }

  useEffect(() => { loadPackages(); loadRetention() }, [])

  const calcChecksum = async (fileName: string) => {
    setChecking((prev) => ({ ...prev, [fileName]: true }))
    try {
      const res: any = await files.checksum(fileName)
      setChecksums((prev) => ({ ...prev, [fileName]: res.data?.sha256 || 'N/A' }))
    } catch {
      setChecksums((prev) => ({ ...prev, [fileName]: '计算失败' }))
    }
    setChecking((prev) => ({ ...prev, [fileName]: false }))
  }

  const handleLocalUpload = async () => {
    const input = fileInputRef.current
    if (!input?.files?.length) return
    const file = input.files[0]
    const fd = new FormData()
    fd.append('file', file)
    setUploading(true)
    setUploadMsg('')
    try {
      const res: any = await files.upload(fd, { system: uploadSystem || undefined, service: uploadService || undefined, overwrite })
      setUploadMsg(`上传成功: ${res.data?.name || res.data?.package_name} (${((res.data?.size_bytes || res.data?.size || 0) / 1024 / 1024).toFixed(2)} MB)`)
      loadPackages()
    } catch (e: any) {
      setUploadMsg('上传失败: ' + (typeof e === 'string' ? e : e?.message || e))
    }
    setUploading(false)
    input.value = ''
  }

  const updateRetentionField = (key: string, value: any) => {
    setRetention((prev) => ({ ...prev, [key]: value }))
  }

  const saveRetention = async () => {
    setRetentionMsg('')
    try {
      const res: any = await files.updatePackageRetention(retention)
      setRetention(res.data || {})
      setRetentionMsg('发布包清理策略已保存')
    } catch (e: any) {
      setRetentionMsg('保存失败: ' + (typeof e === 'string' ? e : e?.message || e))
    }
  }

  const previewCleanup = async () => {
    setCleanupBusy(true)
    setRetentionMsg('')
    try {
      const res: any = await files.previewPackageCleanup({ policy: retention })
      setCleanupPreview(res.data)
      setActiveTab('cleanup')
    } catch (e: any) {
      setRetentionMsg('清理预览失败: ' + (typeof e === 'string' ? e : e?.message || e))
    } finally {
      setCleanupBusy(false)
    }
  }

  const runCleanup = async () => {
    setCleanupConfirmOpen(false)
    setCleanupBusy(true)
    try {
      const res: any = await files.cleanupPackages({ policy: retention, dry_run: false })
      setCleanupPreview(res.data)
      setRetentionMsg(`清理完成，删除 ${res.data?.removed?.length || 0} 个包`)
      loadPackages()
    } catch (e: any) {
      setRetentionMsg('清理失败: ' + (typeof e === 'string' ? e : e?.message || e))
    } finally {
      setCleanupBusy(false)
    }
  }

  const toggleProtect = async (p: any) => {
    try {
      await files.protectPackage(p.package_name || p.name, !p.protected)
      loadPackages()
    } catch (e: any) {
      setUploadMsg('保护状态更新失败: ' + (typeof e === 'string' ? e : e?.message || e))
    }
  }

  const confirmDeletePackage = async () => {
    if (!deletePackageCandidate) return
    const name = deletePackageCandidate.package_name || deletePackageCandidate.name
    setDeleteBusy(true)
    setUploadMsg('')
    try {
      const res: any = await files.deletePackage(name)
      const size = res.data?.size_bytes ? `${(Number(res.data.size_bytes) / 1024 / 1024).toFixed(2)} MB` : ''
      setUploadMsg(`文件包已删除: ${name}${size ? ` (${size})` : ''}`)
      setDeletePackageCandidate(null)
      loadPackages()
    } catch (e: any) {
      const reason = typeof e === 'string' ? e : e?.message || e
      setUploadMsg('删除文件包失败: ' + reason)
    } finally {
      setDeleteBusy(false)
    }
  }

  return (
    <div style={{ display: 'grid', gap: '20px' }}>
      <PageHeader title="文件中心" description="管理 OPS 本地发布包上传、校验、MCP 上传入口和清理策略；远程文件操作请进入服务器详情的文件功能。" />

      {(uploadMsg || retentionMsg) && (
        <div className="card" style={{
          background: (uploadMsg + retentionMsg).includes('失败') ? 'var(--danger-surface)' : 'var(--success-surface)',
          color: (uploadMsg + retentionMsg).includes('失败') ? 'var(--danger)' : 'var(--success)',
          fontSize: '14px',
        }}>
          {uploadMsg || retentionMsg}
        </div>
      )}

      <div className="tab-bar">
        <button className={`tab-btn ${activeTab === 'packages' ? 'tab-btn--active' : ''}`} onClick={() => setActiveTab('packages')}>发布包</button>
        <button className={`tab-btn ${activeTab === 'cleanup' ? 'tab-btn--active' : ''}`} onClick={() => setActiveTab('cleanup')}>清理策略</button>
      </div>

      {activeTab === 'packages' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 360px', gap: '20px', alignItems: 'start' }}>
          <div className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
              <div>
                <h3 style={{ margin: 0 }}>本地部署包 ({packages.length})</h3>
                <div style={{ color: 'var(--text-muted)', fontSize: '12px', marginTop: '4px' }}>
                  上传包会进入发布页和 MCP 工具；表格会展示是否被发布、失败重试或回滚保护。
                </div>
              </div>
              <button className="btn" onClick={loadPackages} disabled={loadingPkg} style={{ padding: '6px 12px', fontSize: '13px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}>刷新</button>
            </div>
            {loadingPkg ? <div style={{ color: 'var(--text-muted)', marginTop: '16px' }}>加载中...</div> : packages.length === 0 ? <div style={{ color: 'var(--text-muted)', marginTop: '16px' }}>暂无部署包，请先上传本地构建产物。</div> : (
              <div style={{ maxHeight: '560px', overflow: 'auto', marginTop: '16px' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                  <thead><tr style={{ borderBottom: '1px solid var(--border-strong)', textAlign: 'left' }}>
                    <th style={{ padding: '8px 6px' }}>文件名</th><th style={{ padding: '8px 6px' }}>大小</th><th style={{ padding: '8px 6px' }}>服务/版本</th><th style={{ padding: '8px 6px' }}>使用</th><th style={{ padding: '8px 6px' }}>保护</th><th style={{ padding: '8px 6px' }}>SHA256</th><th style={{ padding: '8px 6px' }}>操作</th>
                  </tr></thead>
                  <tbody>{packages.map((p) => {
                    const protectedReasons = p.retention?.reasons || []
                    return <tr key={p.name} style={{ borderBottom: '1px solid var(--bg-surface)' }}>
                      <td style={{ padding: '8px 6px', color: 'var(--brand)', fontFamily: 'monospace', wordBreak: 'break-all' }}>{p.name || p.package_name}</td>
                      <td style={{ padding: '8px 6px', whiteSpace: 'nowrap' }}>{p.size_mb} MB</td>
                      <td style={{ padding: '8px 6px', fontFamily: 'monospace' }}>{p.service_hint || '-'}<br /><small>{p.version_hint || '-'}</small></td>
                      <td style={{ padding: '8px 6px' }}>{p.used_count || 0}<br /><small>{p.last_used_at?.slice(0, 16) || '未使用'}</small></td>
                      <td style={{ padding: '8px 6px', minWidth: 160 }}>
                        <span style={{ color: protectedReasons.length ? 'var(--success)' : 'var(--text-muted)' }}>{protectedReasons.length ? '已保护' : '可按规则清理'}</span>
                        {protectedReasons.length > 0 && <div style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.5 }}>{protectedReasons.slice(0, 2).join('；')}</div>}
                      </td>
                      <td style={{ padding: '8px 6px' }}>{checksums[p.name] ? <span style={{ fontSize: '11px', fontFamily: 'monospace', color: checksums[p.name] === '计算失败' ? 'var(--danger)' : 'var(--success)', wordBreak: 'break-all' }}>{checksums[p.name]}</span> : p.sha256 ? <span style={{ fontSize: '11px', fontFamily: 'monospace', color: 'var(--text-muted)', wordBreak: 'break-all' }}>{String(p.sha256).slice(0, 12)}...</span> : <button className="btn" onClick={() => calcChecksum(p.name)} disabled={checking[p.name]} style={{ padding: '2px 8px', fontSize: '12px' }}>{checking[p.name] ? '...' : '校验'}</button>}</td>
                      <td style={{ padding: '8px 6px', minWidth: 120 }}>
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                          <button className="btn" onClick={() => toggleProtect(p)} style={{ padding: '2px 8px', fontSize: 12 }}>{p.protected ? '取消保护' : '保护'}</button>
                          <button
                            className="btn btn-danger"
                            onClick={() => setDeletePackageCandidate(p)}
                            disabled={protectedReasons.length > 0}
                            title={protectedReasons.length > 0 ? '该包仍受保护，需先取消手动保护或解除引用后再删除' : '手动删除文件包'}
                            style={{ padding: '2px 8px', fontSize: 12 }}
                          >
                            删除
                          </button>
                        </div>
                      </td>
                    </tr>
                  })}</tbody>
                </table>
              </div>
            )}
          </div>

          <div className="card" style={{ display: 'grid', gap: '12px' }}>
            <h3 style={{ margin: 0 }}>上传本地包</h3>
            <div style={{ color: 'var(--text-muted)', fontSize: 13, lineHeight: 1.7 }}>支持页面上传，也支持 stdio MCP 先调用 <code>ops_inspect_local_package</code> 检查本地路径，再调用 <code>ops_upload_package</code> 流式上传到文件中心。</div>
            <label>系统标识<input style={inputStyle} placeholder="crypto-trader" value={uploadSystem} onChange={(e) => setUploadSystem(e.target.value)} /></label>
            <label>服务标识<input style={inputStyle} placeholder="crypto-system / system" value={uploadService} onChange={(e) => setUploadService(e.target.value)} /></label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}><input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} /> 允许覆盖同名包</label>
            <input ref={fileInputRef} type="file" style={{ display: 'none' }} onChange={() => handleLocalUpload()} />
            <button className="btn" onClick={() => fileInputRef.current?.click()} disabled={uploading} style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>{uploading ? '上传中...' : '选择并上传'}</button>
            <div style={{ color: 'var(--text-muted)', fontSize: 12, lineHeight: 1.6 }}>MCP 示例：先用 <code>ops_inspect_local_package</code> 检查 <code>D:\packages\system.tar.gz</code>，确认后用 <code>ops_upload_package</code> 上传并继续生成发布计划和预检。</div>
            <div style={{ borderTop: '1px solid var(--border-strong)', paddingTop: 12, display: 'grid', gap: 8 }}>
              <strong>推荐 MCP 本地包发布工作流</strong>
              <ol style={{ margin: 0, paddingLeft: 18, color: 'var(--text-muted)', fontSize: 12, lineHeight: 1.7 }}>
                <li><code>ops_inspect_local_package</code>：本地包检查和 SHA256</li>
                <li><code>ops_prepare_release_from_local_package</code>：上传、创建计划、预检、返回确认</li>
                <li><code>ops_execute_deploy_plan</code>：用户明确确认后才执行</li>
                <li><code>ops_get_deployment_report</code>：发布后查看报告</li>
              </ol>
              <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>该工作流不会自动执行发布，确认文本仍由后端生成。</div>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'cleanup' && (
        <div style={{ display: 'grid', gridTemplateColumns: '420px minmax(0, 1fr)', gap: 20, alignItems: 'start' }}>
          <div className="card" style={{ display: 'grid', gap: 12 }}>
            <h3 style={{ margin: 0 }}>发布包清理策略</h3>
            <div style={{ color: 'var(--text-muted)', fontSize: 13, lineHeight: 1.7 }}>清理基于引用关系，不会删除运行中、失败重试期、回滚候选和最近成功发布依赖的包。</div>
            {[
              ['unused_package_keep_days', '未使用包保留天数'], ['test_success_keep_days', '测试成功包保留天数'], ['prod_success_keep_days', '生产成功包保留天数'], ['failed_package_keep_days', '失败包保留天数'], ['rollback_package_keep_days', '回滚包保留天数'], ['keep_latest_success_per_service', '每服务最近成功包数'], ['keep_latest_prod_success_per_service', '生产最近成功包数'], ['package_keep_max', '最大包数量'], ['max_upload_size_mb', '最大上传 MB'], ['min_keep_days', '最短保护天数']
            ].map(([key, label]) => <label key={key}>{label}<input style={inputStyle} type="number" value={retention[key] ?? ''} onChange={(e) => updateRetentionField(key, Number(e.target.value))} /></label>)}
            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}><input type="checkbox" checked={!!retention.protect_running_deployments} onChange={(e) => updateRetentionField('protect_running_deployments', e.target.checked)} /> 保护运行中发布包</label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}><input type="checkbox" checked={!!retention.protect_failed_deployments} onChange={(e) => updateRetentionField('protect_failed_deployments', e.target.checked)} /> 保护失败重试期包</label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}><input type="checkbox" checked={!!retention.protect_rollback_candidates} onChange={(e) => updateRetentionField('protect_rollback_candidates', e.target.checked)} /> 保护回滚候选包</label>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button className="btn" onClick={loadRetention}>刷新策略</button>
              <button className="btn" onClick={saveRetention}>保存策略</button>
              <button className="btn" onClick={previewCleanup} disabled={cleanupBusy} style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>清理预览</button>
            </div>
          </div>
          <div className="card" style={{ display: 'grid', gap: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <div><h3 style={{ margin: 0 }}>清理预览</h3><small style={{ color: 'var(--text-muted)' }}>真实删除前请先确认候选和保护原因</small></div>
              <button className="btn btn-danger" onClick={() => setCleanupConfirmOpen(true)} disabled={cleanupBusy || !cleanupPreview?.summary?.cleanup_count}>按预览清理</button>
            </div>
            {cleanupPreview ? <>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
                <div className="stat-card"><span>可清理</span><strong>{cleanupPreview.summary?.cleanup_count || 0}</strong><small>{cleanupPreview.summary?.cleanup_size_mb || 0} MB</small></div>
                <div className="stat-card"><span>受保护</span><strong>{cleanupPreview.summary?.protected_count || 0}</strong><small>不会删除</small></div>
                <div className="stat-card"><span>总包数</span><strong>{cleanupPreview.summary?.total_packages || 0}</strong><small>本地文件中心</small></div>
              </div>
              <h4>候选包</h4>
              <div style={{ maxHeight: 420, overflow: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}><tbody>
                  {(cleanupPreview.candidates || []).map((p: any) => <tr key={p.package_name} style={{ borderBottom: '1px solid var(--bg-surface)' }}><td style={{ padding: 8, fontFamily: 'monospace', color: 'var(--brand)' }}>{p.package_name}</td><td style={{ padding: 8 }}>{p.size_mb} MB</td><td style={{ padding: 8 }}>{p.reason}</td></tr>)}
                  {(!cleanupPreview.candidates || cleanupPreview.candidates.length === 0) && <tr><td style={{ padding: 12, color: 'var(--text-muted)' }}>没有可清理包</td></tr>}
                </tbody></table>
              </div>
            </> : <div style={{ color: 'var(--text-muted)' }}>点击“清理预览”查看候选包和保护原因。</div>}
          </div>
        </div>
      )}
      <ConfirmDialog
        open={cleanupConfirmOpen}
        title="确认清理发布包"
        description="将按当前预览结果清理候选发布包；受保护包不会删除，元数据会保留。"
        confirmLabel={cleanupBusy ? '清理中...' : '确认清理'}
        danger
        onCancel={() => setCleanupConfirmOpen(false)}
        onConfirm={runCleanup}
      />
      <ConfirmDialog
        open={!!deletePackageCandidate}
        title="确认删除文件包"
        description="将从文件中心手动删除该发布包文件，并把元数据标记为已删除；已受保护的包需要先取消保护。"
        confirmLabel={deleteBusy ? '删除中...' : '确认删除'}
        danger
        onCancel={() => setDeletePackageCandidate(null)}
        onConfirm={confirmDeletePackage}
      >
        {deletePackageCandidate && (
          <div style={{ display: 'grid', gap: 6, fontSize: 13 }}>
            <div>文件包：<code>{deletePackageCandidate.package_name || deletePackageCandidate.name}</code></div>
            <div>大小：{deletePackageCandidate.size_mb ?? '-'} MB</div>
            <div>使用次数：{deletePackageCandidate.used_count || 0}</div>
            <div>最后使用：{deletePackageCandidate.last_used_at?.slice(0, 16) || '未使用'}</div>
          </div>
        )}
      </ConfirmDialog>
    </div>
  )
}
