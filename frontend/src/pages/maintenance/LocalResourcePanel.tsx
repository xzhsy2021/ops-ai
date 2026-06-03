import { useEffect, useMemo, useState } from 'react'
import { systemHealth } from '../../api'
import { EmptyState, LoadingState, RiskConfirmDialog, StatusBadge } from '../../components/ui'

function asData(res: any) {
  return res?.data ?? res
}

function numberValue(value: any, fallback: number) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback
}

function bucketLabel(key: string) {
  return ({
    app_data: 'APP_DATA_DIR',
    database: 'SQLite',
    uploads: '上传包',
    logs: '日志',
    backups: '备份',
    keys: '密钥',
    runtime: '临时运行',
    reports: '报告',
  } as Record<string, string>)[key] || key
}

export default function LocalResourcePanel({ showMsg, showErr }: { showMsg: (msg: string) => void; showErr: (msg: string) => void }) {
  const [storage, setStorage] = useState<any>(null)
  const [runtime, setRuntime] = useState<any>(null)
  const [retention, setRetention] = useState<any>(null)
  const [preview, setPreview] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [executing, setExecuting] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirmValue, setConfirmValue] = useState('')

  const policy = useMemo(() => ({
    log_file_keep_days: numberValue(retention?.log_file_keep_days, 30),
    backup_keep_days: numberValue(retention?.backup_keep_days, 90),
    backup_keep_max: numberValue(retention?.backup_keep_max, 7),
    runtime_tmp_keep_hours: numberValue(retention?.runtime_tmp_keep_hours, 24),
    dry_run: true,
  }), [retention])

  const load = async (force = false) => {
    setLoading(true)
    try {
      const [storageRes, runtimeRes, retentionRes] = await Promise.all([
        force ? systemHealth.storageScan() : systemHealth.storageUsage(),
        systemHealth.runtimeUsage(),
        systemHealth.runtimeRetention(),
      ])
      setStorage(asData(storageRes))
      setRuntime(asData(runtimeRes))
      setRetention(asData(retentionRes))
    } catch (e: any) {
      showErr(String(e?.message || e || '本地资源信息加载失败'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load(false) }, [])

  const updatePolicyField = (key: string, value: string) => {
    setRetention((prev: any) => ({ ...(prev || {}), [key]: numberValue(value, 0) }))
  }

  const savePolicy = async () => {
    try {
      const res = await systemHealth.updateRuntimeRetention(policy)
      setRetention(asData(res))
      showMsg('本地资源保留策略已保存')
    } catch (e: any) {
      showErr(String(e?.message || e || '保存保留策略失败'))
    }
  }

  const previewCleanup = async () => {
    setPreviewing(true)
    try {
      const res = await systemHealth.previewRuntimeCleanup({ dry_run: true, policy })
      setPreview(asData(res))
      showMsg('清理预览已生成')
    } catch (e: any) {
      showErr(String(e?.message || e || '清理预览失败'))
    } finally {
      setPreviewing(false)
    }
  }

  const executeCleanup = async () => {
    if (!preview?.confirm_text || confirmValue.trim() !== preview.confirm_text) return
    setExecuting(true)
    try {
      const res = await systemHealth.cleanupRuntime({ dry_run: false, confirm_text: preview.confirm_text, policy })
      setPreview(asData(res))
      setConfirmOpen(false)
      setConfirmValue('')
      await load(true)
      showMsg('本地资源清理已完成')
    } catch (e: any) {
      showErr(String(e?.message || e || '本地资源清理失败'))
    } finally {
      setExecuting(false)
    }
  }

  const buckets = storage?.buckets || {}
  const fileSummary = preview?.summary || {}
  const cleanupCount = ['logs', 'backups', 'runtime_tmp'].reduce((acc, key) => acc + Number(preview?.files?.[key]?.count || 0), 0)
  const canCleanup = Boolean(preview) && (cleanupCount > 0 || Number(fileSummary.package_cleanup_size_bytes || 0) > 0 || Object.values(fileSummary.database_candidate_counts || {}).some((v: any) => Number(v || 0) > 0))

  return (
    <section className="card local-resource-panel">
      <div className="panel-heading-row">
        <div>
          <h3>本地资源</h3>
          <p>查看 SQLite、日志、上传包、备份、报告和临时文件占用。清理流程固定为：扫描 → 预览 → 输入确认短语 → 执行。</p>
        </div>
        <div className="panel-actions-row">
          <button className="btn btn-subtle" onClick={() => void load(true)} disabled={loading}>{loading ? '扫描中...' : '重新扫描'}</button>
          <button className="btn btn-primary" onClick={previewCleanup} disabled={previewing}>{previewing ? '预览中...' : '清理预览'}</button>
        </div>
      </div>

      {loading && !storage ? <LoadingState message="加载本地资源..." /> : storage ? (
        <>
          <div className="resource-summary-strip">
            <div><span>托管总占用</span><strong>{storage.total_managed_size_human || '-'}</strong></div>
            <div><span>APP_DATA_DIR</span><code>{storage.app_data_dir || '-'}</code></div>
            <div><span>缓存</span><strong>{storage.cache?.hit ? '命中' : '刷新'}</strong></div>
            <div><span>进程</span><strong>PID {runtime?.process?.pid || '-'}</strong></div>
          </div>

          <div className="resource-bucket-grid">
            {Object.entries(buckets).map(([key, value]: [string, any]) => (
              <div className="resource-bucket-card" key={key}>
                <div className="resource-bucket-title"><strong>{bucketLabel(key)}</strong><StatusBadge value={value?.exists ? 'ok' : 'warning'} label={value?.exists ? '可用' : '缺失'} /></div>
                <div className="resource-bucket-size">{value?.size_human || '-'}</div>
                <div className="resource-bucket-meta">文件 {value?.file_count ?? (value?.exists ? 1 : 0)} · 跳过 {value?.skipped ?? 0}</div>
                <code title={value?.path}>{value?.path || '-'}</code>
              </div>
            ))}
          </div>

          <div className="resource-policy-grid">
            <label>日志保留天数<input type="number" min="0" value={policy.log_file_keep_days} onChange={(e) => updatePolicyField('log_file_keep_days', e.target.value)} /></label>
            <label>备份保留天数<input type="number" min="0" value={policy.backup_keep_days} onChange={(e) => updatePolicyField('backup_keep_days', e.target.value)} /></label>
            <label>最多保留备份<input type="number" min="0" value={policy.backup_keep_max} onChange={(e) => updatePolicyField('backup_keep_max', e.target.value)} /></label>
            <label>临时文件保留小时<input type="number" min="0" value={policy.runtime_tmp_keep_hours} onChange={(e) => updatePolicyField('runtime_tmp_keep_hours', e.target.value)} /></label>
            <button className="btn btn-subtle" onClick={savePolicy}>保存策略</button>
          </div>

          {preview ? (
            <div className="resource-preview-card">
              <div className="resource-preview-header">
                <div><strong>清理预览</strong><p>先预览候选项，不会直接删除。确认后仅清理日志、过期备份、runtime 临时文件、发布历史和过期包。</p></div>
                <button className="btn btn-danger" disabled={!canCleanup || executing} onClick={() => { setConfirmValue(''); setConfirmOpen(true) }}>{executing ? '清理中...' : '确认并清理'}</button>
              </div>
              <div className="resource-preview-grid">
                <div><span>日志候选</span><strong>{preview.files?.logs?.count || 0}</strong></div>
                <div><span>备份候选</span><strong>{preview.files?.backups?.count || 0}</strong></div>
                <div><span>临时文件候选</span><strong>{preview.files?.runtime_tmp?.count || 0}</strong></div>
                <div><span>预计释放</span><strong>{fileSummary.file_cleanup_size_human || '-'}</strong></div>
              </div>
              {!canCleanup && <EmptyState title="暂无可清理项" description="当前保留策略下没有发现明显可清理资源。" />}
            </div>
          ) : null}
        </>
      ) : <EmptyState title="暂无本地资源数据" description="点击重新扫描读取当前运行目录占用。" />}

      <RiskConfirmDialog
        open={confirmOpen}
        title="清理本地运行资源"
        description="将按当前保留策略清理候选文件和过期记录。该操作不可直接撤销，请确认已检查预览结果。"
        target={storage?.app_data_dir || 'APP_DATA_DIR'}
        confirmText={preview?.confirm_text || ''}
        value={confirmValue}
        onValueChange={setConfirmValue}
        onCancel={() => { setConfirmOpen(false); setConfirmValue('') }}
        onConfirm={executeCleanup}
        riskLevel="high"
        details={[
          { label: '日志候选', value: preview?.files?.logs?.count || 0 },
          { label: '备份候选', value: preview?.files?.backups?.count || 0 },
          { label: '临时文件候选', value: preview?.files?.runtime_tmp?.count || 0 },
          { label: '预计释放', value: fileSummary.file_cleanup_size_human || '-' },
        ]}
        confirmButtonLabel={executing ? '清理中...' : '确认并清理'}
        confirmDisabled={executing}
        confirmMode="type"
      />
    </section>
  )
}
