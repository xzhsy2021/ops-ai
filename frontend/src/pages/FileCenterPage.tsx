import { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { files } from '../api'
import { ConfirmDialog } from '../components/ui'

function formatTime(value?: string | null): string {
  if (!value) return '-'
  try {
    const d = new Date(value)
    if (isNaN(d.getTime())) return value
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch {
    return value
  }
}

function relativeFromNow(value?: string | null): string {
  if (!value) return ''
  const t = new Date(value).getTime()
  if (isNaN(t)) return ''
  const diff = Date.now() - t
  if (diff < 0) return ''
  const minute = 60_000, hour = 3_600_000, day = 86_400_000
  if (diff < minute) return '刚刚'
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`
  if (diff < day * 30) return `${Math.floor(diff / day)} 天前`
  if (diff < day * 365) return `${Math.floor(diff / (day * 30))} 个月前`
  return `${Math.floor(diff / (day * 365))} 年前`
}

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
  // 弹窗：上传本地包 / 清理预览
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false)
  const [cleanupPreviewOpen, setCleanupPreviewOpen] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const fileInputDialogRef = useRef<HTMLInputElement>(null)

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

  const handleLocalUpload = async (input: HTMLInputElement | null) => {
    const el = input || fileInputRef.current
    if (!el?.files?.length) return
    const file = el.files[0]
    el.value = ''
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
    el.value = ''
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
      setCleanupPreviewOpen(true)
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
    <div className="file-page-shell">
      <header className="cc-hero">
        <div>
          <span className="cc-hero-eyebrow">File Center · Local Artifacts</span>
          <h1 className="cc-hero-title">文件中心</h1>
          <p className="cc-hero-desc">管理 OPS 本地发布包上传、SHA256 校验、MCP 上传入口和清理策略；远程文件操作请进入服务器详情的文件功能。</p>
        </div>
        <div className="cc-hero-stats" aria-label="文件中心状态">
          <div className="cc-hero-stat cc-hero-stat--info">
            <strong>{packages.length}</strong>
            <span>本地包</span>
          </div>
          <div className="cc-hero-stat cc-hero-stat--ok">
            <strong>{Object.values(checksums).filter((c) => c && c !== '计算失败').length}</strong>
            <span>已校验</span>
          </div>
          <div className="cc-hero-stat cc-hero-stat--warn">
            <strong>{packages.filter((p) => p.retention?.reasons?.length).length}</strong>
            <span>受保护</span>
          </div>
        </div>
      </header>

      {(uploadMsg || retentionMsg) && (
        <div className="alert-card" role="status" style={{
          background: (uploadMsg + retentionMsg).includes('失败') ? 'var(--danger-surface)' : 'var(--success-surface)',
          color: (uploadMsg + retentionMsg).includes('失败') ? 'var(--danger)' : 'var(--success)',
        }}>
          {uploadMsg || retentionMsg}
        </div>
      )}

      <div className="file-tab-bar" role="tablist">
        <button className={activeTab === 'packages' ? 'is-active' : ''} onClick={() => setActiveTab('packages')}>发布包</button>
        <button className={activeTab === 'cleanup' ? 'is-active' : ''} onClick={() => setActiveTab('cleanup')}>清理策略</button>
      </div>

      {activeTab === 'packages' && (
        <section className="glass-card">
          <div className="section-title-row">
            <div>
              <h2>本地部署包 <small style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 12, marginLeft: 6 }}>· {packages.length} 个</small></h2>
              <p>上传包会进入发布页和 MCP 工具；表格会展示是否被发布、失败重试或回滚保护。</p>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button className="cc-icon-btn" onClick={loadPackages} disabled={loadingPkg}>刷新</button>
              <button
                className="cc-icon-btn cc-icon-btn--success"
                onClick={() => setUploadDialogOpen(true)}
                disabled={uploading}
              >
                上传本地包
              </button>
            </div>
          </div>
          {uploadMsg && (
            <div className="file-meta" style={{ marginTop: 8, color: uploadMsg.startsWith('上传失败') ? 'var(--error, #d9534f)' : 'var(--text-muted)' }}>
              {uploadMsg}
            </div>
          )}
          {loadingPkg ? (
            <div style={{ color: 'var(--text-muted)', marginTop: '16px' }}>加载中...</div>
          ) : packages.length === 0 ? (
            <div style={{ color: 'var(--text-muted)', marginTop: '16px' }}>暂无部署包，请先点击右上角"上传本地包"。</div>
          ) : (
            <div className="file-table-scroll" style={{ marginTop: '16px' }}>
              <table className="file-table">
                <thead>
                  <tr>
                    <th style={{ minWidth: 160 }}>文件名</th>
                    <th style={{ width: 90 }}>大小</th>
                    <th style={{ minWidth: 120 }}>服务 / 版本</th>
                    <th style={{ minWidth: 150, width: 170 }}>上传时间</th>
                    <th style={{ minWidth: 130, width: 150 }}>使用</th>
                    <th style={{ width: 120 }}>保护</th>
                    <th style={{ minWidth: 150 }}>SHA256</th>
                    <th style={{ width: 180 }}>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {packages.map((p) => {
                    const protectedReasons = p.retention?.reasons || []
                    const fileName = p.name || p.package_name
                    const uploadedAt = formatTime(p.uploaded_at)
                    const uploadedRel = relativeFromNow(p.uploaded_at)
                    const uploader = p.uploaded_by || '-'
                    return (
                      <tr key={fileName}>
                        <td><span className="file-name" title={fileName}>{fileName}</span></td>
                        <td><span className="file-size">{p.size_mb} MB</span></td>
                        <td>
                          <span className="file-name" title={p.service_hint}>{p.service_hint || '-'}</span>
                          <div className="file-meta">{p.version_hint || '-'}</div>
                        </td>
                        <td>
                          <span className="file-time" title={p.uploaded_at || ''}>{uploadedAt}</span>
                          <div className="file-meta">
                            {uploadedRel && <span className="file-time-rel">· {uploadedRel}</span>}
                            {uploader !== '-' && <span className="file-time-uploader" title={`上传者：${uploader}`}> · {uploader}</span>}
                          </div>
                        </td>
                        <td>
                          <span className="file-meta">{p.used_count || 0} 次</span>
                          <div className="file-meta">{p.last_used_at ? formatTime(p.last_used_at) : '未使用'}</div>
                        </td>
                        <td>
                          <span className={`cc-chip ${protectedReasons.length ? 'cc-chip--ok' : 'cc-chip--ghost'}`}>
                            {protectedReasons.length ? '已保护' : '可清理'}
                          </span>
                          {protectedReasons.length > 0 && (
                            <div className="file-meta" style={{ marginTop: 4 }}>{protectedReasons.slice(0, 2).join('；')}</div>
                          )}
                        </td>
                        <td>
                          {checksums[fileName] ? (
                            <span
                              className={`file-checksum ${checksums[fileName] === '计算失败' ? 'file-checksum--err' : 'file-checksum--ok'}`}
                              title={checksums[fileName]}
                            >
                              {checksums[fileName]}
                            </span>
                          ) : p.sha256 ? (
                            <span className="file-checksum" title={p.sha256}>{String(p.sha256).slice(0, 12)}…</span>
                          ) : (
                            <button className="cc-icon-btn" onClick={() => calcChecksum(fileName)} disabled={checking[fileName]}>
                              {checking[fileName] ? '…' : '校验'}
                            </button>
                          )}
                        </td>
                        <td>
                          <div className="file-row-actions">
                            <button className="file-action" onClick={() => toggleProtect(p)}>
                              {p.protected ? '取消保护' : '保护'}
                            </button>
                            <button
                              className="file-action file-action--danger"
                              onClick={() => setDeletePackageCandidate(p)}
                              disabled={protectedReasons.length > 0}
                              title={protectedReasons.length > 0 ? '该包仍受保护，需先取消手动保护或解除引用后再删除' : '手动删除文件包'}
                            >
                              删除
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {activeTab === 'cleanup' && (
        <section className="glass-card">
          <div className="section-title-row">
            <div>
              <h2>发布包清理策略</h2>
              <p>清理基于引用关系，不会删除运行中、失败重试期、回滚候选和最近成功发布依赖的包。</p>
            </div>
            <div className="file-toolbar">
              <button className="cc-icon-btn" onClick={loadRetention}>刷新策略</button>
              <button className="cc-icon-btn" onClick={saveRetention}>保存策略</button>
              <button className="cc-icon-btn cc-icon-btn--success" onClick={previewCleanup} disabled={cleanupBusy}>
                {cleanupBusy ? '预览中…' : '启动清理预览'}
              </button>
            </div>
          </div>
          {retentionMsg && (
            <div className="file-meta file-meta--msg" style={{ color: retentionMsg.includes('失败') ? 'var(--error, #d9534f)' : 'var(--text-muted)' }}>
              {retentionMsg}
            </div>
          )}

          <div className="file-cleanup-block">
            <div className="file-cleanup-block-title">当前生效概览</div>
            <div className="file-cleanup-stat-grid">
              <div className="cc-hero-stat">
                <strong>{retention.unused_package_keep_days ?? '-'}</strong>
                <span>未使用 · 天</span>
              </div>
              <div className="cc-hero-stat">
                <strong>{retention.test_success_keep_days ?? '-'}</strong>
                <span>测试成功 · 天</span>
              </div>
              <div className="cc-hero-stat">
                <strong>{retention.prod_success_keep_days ?? '-'}</strong>
                <span>生产成功 · 天</span>
              </div>
            </div>
          </div>

          <div className="file-cleanup-block">
            <div className="file-cleanup-block-title">保留时长与上限</div>
            <div className="form-grid form-grid--cleanup">
              {[
                ['unused_package_keep_days', '未使用包保留天数'],
                ['test_success_keep_days', '测试成功包保留天数'],
                ['prod_success_keep_days', '生产成功包保留天数'],
                ['failed_package_keep_days', '失败包保留天数'],
                ['rollback_package_keep_days', '回滚包保留天数'],
                ['min_keep_days', '最短保护天数'],
                ['keep_latest_success_per_service', '每服务最近成功包数'],
                ['keep_latest_prod_success_per_service', '生产最近成功包数'],
                ['package_keep_max', '最大包数量'],
                ['max_upload_size_mb', '最大上传 MB'],
              ].map(([key, label]) => (
                <label key={key} className="form-field">
                  <span className="form-field-label">{label}</span>
                  <input type="number" value={retention[key] ?? ''} onChange={(e) => updateRetentionField(key, Number(e.target.value))} />
                </label>
              ))}
            </div>
          </div>

          <div className="file-cleanup-block">
            <div className="file-cleanup-block-title">保护策略</div>
            <div className="file-cleanup-checks">
              <label className="inline-check">
                <input type="checkbox" checked={!!retention.protect_running_deployments} onChange={(e) => updateRetentionField('protect_running_deployments', e.target.checked)} />
                <span>保护运行中发布包</span>
              </label>
              <label className="inline-check">
                <input type="checkbox" checked={!!retention.protect_failed_deployments} onChange={(e) => updateRetentionField('protect_failed_deployments', e.target.checked)} />
                <span>保护失败重试期包</span>
              </label>
              <label className="inline-check">
                <input type="checkbox" checked={!!retention.protect_rollback_candidates} onChange={(e) => updateRetentionField('protect_rollback_candidates', e.target.checked)} />
                <span>保护回滚候选包</span>
              </label>
            </div>
          </div>
        </section>
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

      {/* 上传本地包 弹窗 */}
      {uploadDialogOpen && createPortal(
        <div className="cc-modal-overlay" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) setUploadDialogOpen(false) }}>
          <div className="cc-modal-shell" role="dialog" aria-modal="true" aria-label="上传本地包" style={{ maxWidth: 520 }}>
            <div className="cc-modal-header">
              <h3>上传本地包</h3>
              <button className="cc-icon-btn" onClick={() => setUploadDialogOpen(false)} aria-label="关闭">×</button>
            </div>
            <div className="cc-modal-body" style={{ display: 'grid', gap: 12 }}>
              <p style={{ margin: 0, color: 'var(--text-muted)', fontSize: 13, lineHeight: 1.7 }}>
                支持页面上传，也支持 MCP 先调用 <code>ops_inspect_local_package</code> 检查本地路径，再调用 <code>ops_upload_package</code> 流式上传到文件中心。
              </p>
              <label>系统标识
                <input placeholder="crypto-trader" value={uploadSystem} onChange={(e) => setUploadSystem(e.target.value)} />
              </label>
              <label>服务标识
                <input placeholder="crypto-system / system" value={uploadService} onChange={(e) => setUploadService(e.target.value)} />
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} /> 允许覆盖同名包
              </label>
              <input ref={fileInputDialogRef} type="file" style={{ display: 'none' }} onChange={() => handleLocalUpload(fileInputDialogRef.current)} />
              <button
                className="cc-icon-btn cc-icon-btn--success"
                style={{ height: 36, fontSize: 12.5, justifyContent: 'center' }}
                onClick={() => fileInputDialogRef.current?.click()}
                disabled={uploading}
              >
                {uploading ? '上传中…' : '选择文件并上传'}
              </button>
              {uploadMsg && (
                <div className="file-meta" style={{ color: uploadMsg.startsWith('上传失败') ? 'var(--error, #d9534f)' : 'var(--text-muted)' }}>
                  {uploadMsg}
                </div>
              )}
              <div className="file-mcp-callout">
                <strong>推荐 MCP 本地包发布工作流</strong>
                <ol>
                  <li><code>ops_inspect_local_package</code>：本地包检查和 SHA256</li>
                  <li><code>ops_prepare_release_from_local_package</code>：上传、创建计划、预检、返回确认</li>
                  <li><code>ops_execute_deploy_plan</code>：用户明确确认后才执行</li>
                  <li><code>ops_get_deployment_report</code>：发布后查看报告</li>
                </ol>
                <div className="file-meta">该工作流不会自动执行发布，确认文本仍由后端生成。</div>
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* 清理预览 弹窗 */}
      {cleanupPreviewOpen && createPortal(
        <div className="cc-modal-overlay" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) setCleanupPreviewOpen(false) }}>
          <div className="cc-modal-shell" role="dialog" aria-modal="true" aria-label="清理预览" style={{ maxWidth: 820, width: 'calc(100% - 32px)' }}>
            <div className="cc-modal-header">
              <div>
                <h3>清理预览</h3>
                <p style={{ margin: 0, color: 'var(--text-muted)', fontSize: 12.5 }}>真实删除前请先确认候选和保护原因</p>
              </div>
              <button className="cc-icon-btn" onClick={() => setCleanupPreviewOpen(false)} aria-label="关闭">×</button>
            </div>
            <div className="cc-modal-body" style={{ display: 'grid', gap: 12 }}>
              {cleanupPreview ? (
                <>
                  <div className="file-cleanup-stat-grid">
                    <div className="file-stat-card file-stat-card--warn">
                      <strong>{cleanupPreview.summary?.cleanup_count || 0}</strong>
                      <span>可清理</span>
                      <small>{cleanupPreview.summary?.cleanup_size_mb || 0} MB</small>
                    </div>
                    <div className="file-stat-card file-stat-card--ok">
                      <strong>{cleanupPreview.summary?.protected_count || 0}</strong>
                      <span>受保护</span>
                      <small>不会删除</small>
                    </div>
                    <div className="file-stat-card file-stat-card--info">
                      <strong>{cleanupPreview.summary?.total_packages || 0}</strong>
                      <span>总包数</span>
                      <small>本地文件中心</small>
                    </div>
                  </div>

                  <h3 style={{ margin: '4px 0 4px', fontFamily: 'var(--font-display)', fontSize: 13, fontWeight: 600, color: 'var(--text-strong)' }}>候选包</h3>
                  <div className="file-table-scroll" style={{ maxHeight: '50vh' }}>
                    <table className="file-table">
                      <thead>
                        <tr>
                          <th style={{ minWidth: 220 }}>包名</th>
                          <th style={{ width: 90 }}>大小</th>
                          <th>清理原因</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(cleanupPreview.candidates || []).map((p: any) => (
                          <tr key={p.package_name}>
                            <td><span className="file-name" title={p.package_name}>{p.package_name}</span></td>
                            <td><span className="file-size">{p.size_mb} MB</span></td>
                            <td><span className="file-meta" style={{ wordBreak: 'break-word' }}>{p.reason}</span></td>
                          </tr>
                        ))}
                        {(!cleanupPreview.candidates || cleanupPreview.candidates.length === 0) && (
                          <tr><td colSpan={3} style={{ padding: 12, color: 'var(--text-muted)' }}>没有可清理包</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 }}>
                    <button className="cc-icon-btn" onClick={() => setCleanupPreviewOpen(false)}>关闭</button>
                    <button
                      className="cc-icon-btn cc-icon-btn--danger"
                      onClick={() => { setCleanupPreviewOpen(false); setCleanupConfirmOpen(true) }}
                      disabled={cleanupBusy || !cleanupPreview?.summary?.cleanup_count}
                    >
                      按预览清理
                    </button>
                  </div>
                </>
              ) : (
                <div style={{ color: 'var(--text-muted)' }}>暂无预览数据，请先点击"启动清理预览"。</div>
              )}
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  )
}
