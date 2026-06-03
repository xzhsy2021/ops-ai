import { useEffect, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { adminMaintenance } from '../api'
import { useAuthStore } from '../store'
import BackupPanel from './maintenance/BackupPanel'
import MaintenanceHealthPanel from './maintenance/MaintenanceHealthPanel'
import XshellImportPanel from './maintenance/XshellImportPanel'
import LocalResourcePanel from './maintenance/LocalResourcePanel'
import { useMaintenanceBackupActions } from './maintenance/useMaintenanceBackupActions'
import { ConfirmDialog, RiskConfirmDialog } from '../components/ui'
import { ROUTES } from '../routes'

export default function AdminMaintenancePage() {
  const { user } = useAuthStore()
  const isAdmin = Boolean(user?.is_admin)
  const location = useLocation()
  const legacyTab = new URLSearchParams(location.search).get('tab')
  const [health, setHealth] = useState<any>(null)
  const [backups, setBackups] = useState<any[]>([])
  const [message, setMessage] = useState('')
  const [errorMsg, setErrorMsg] = useState('')
  const [xshellResult, setXshellResult] = useState<any>(null)
  const [xshellLoading, setXshellLoading] = useState(false)
  const [xshellConfirmOpen, setXshellConfirmOpen] = useState(false)

  const showMsg = (msg: string) => { setMessage(msg); window.setTimeout(() => setMessage(''), 3000) }
  const showErr = (msg: string) => { setErrorMsg(msg); window.setTimeout(() => setErrorMsg(''), 5000) }

  const refreshHealth = () => {
    adminMaintenance.health().then((res: any) => setHealth(res.data)).catch(() => {})
  }
  const refreshBackups = () => {
    adminMaintenance.backups({ verify_latest: true }).then((res: any) => setBackups(res.data || [])).catch(() => {})
  }

  useEffect(() => {
    if (!isAdmin) return
    refreshHealth()
    refreshBackups()
  }, [isAdmin])

  const {
    handleBackup,
    handleExport,
    handleRestore,
    handleVerify,
    handleDelete,
    handleImportClick,
    handleFileImport,
    riskDialog,
    riskConfirmValue,
    setRiskConfirmValue,
    riskReason,
    setRiskReason,
    riskSubmitting,
    cancelRiskDialog,
    confirmRiskAction,
  } = useMaintenanceBackupActions({ refreshBackups, showMsg, showErr })

  const handleXshellImport = async () => {
    setXshellConfirmOpen(false)
    setXshellLoading(true)
    setXshellResult(null)
    try {
      const res: any = await adminMaintenance.importXshell()
      setXshellResult(res.data)
      showMsg(`导入完成: ${res.data.imported} 台服务器`)
    } catch (e: any) {
      showErr(typeof e === 'string' ? e : e?.response?.data?.detail || e?.message || 'Xshell 导入失败')
    }
    setXshellLoading(false)
  }

  if (!isAdmin) return <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '40px' }}>仅限管理员访问</div>

  if (legacyTab === 'sql') return <Navigate to={`${ROUTES.database}?tab=query`} replace />
  if (legacyTab === 'cleanup' || legacyTab === 'jobs') return <Navigate to={`${ROUTES.database}?tab=cleanup`} replace />
  if (legacyTab === 'connections') return <Navigate to={`${ROUTES.database}?tab=connections`} replace />

  return (
    <div className="page-stack page-enter maintenance-system-page">
      <section className="hero-panel maintenance-system-hero">
        <div>
          <p className="eyebrow">System Maintenance</p>
          <h1>系统维护</h1>
          <p className="hero-subtitle">维护页只保留系统健康、SQLite 备份恢复、配置导入导出和 Xshell 导入。数据库连接、SQL 查询、SQL 执行和数据清理已统一迁移到“数据库工作台”。</p>
        </div>
        <div className="hero-actions">
          <button className="btn btn-subtle" onClick={refreshHealth}>刷新健康</button>
          <button className="btn btn-primary" onClick={refreshBackups}>刷新备份</button>
        </div>
      </section>

      {message && <div className="card" style={{ background: 'var(--success-surface)', color: 'var(--success)', fontSize: '14px' }}>{message}</div>}
      {errorMsg && <div className="card" style={{ background: 'var(--danger-surface)', color: 'var(--danger)', fontSize: '14px' }}>{errorMsg}</div>}

      <div className="maintenance-system-grid">
        <MaintenanceHealthPanel health={health} onRefresh={refreshHealth} />
        <BackupPanel
          backups={backups}
          backupDownloadUrl={adminMaintenance.backupDownloadUrl}
          onBackup={handleBackup}
          onRefreshBackups={refreshBackups}
          onExport={handleExport}
          onImportClick={handleImportClick}
          onFileImport={handleFileImport}
          onRestore={handleRestore}
          onVerify={handleVerify}
          onDelete={handleDelete}
        />
      </div>

      <LocalResourcePanel showMsg={showMsg} showErr={showErr} />

      <XshellImportPanel
        xshellLoading={xshellLoading}
        xshellResult={xshellResult}
        onImport={() => setXshellConfirmOpen(true)}
      />

      <RiskConfirmDialog
        open={Boolean(riskDialog)}
        title={riskDialog?.title || '高风险操作确认'}
        description={riskDialog?.description}
        target={riskDialog?.target || '-'}
        confirmText={riskDialog?.confirmText || ''}
        value={riskConfirmValue}
        onValueChange={setRiskConfirmValue}
        onCancel={cancelRiskDialog}
        onConfirm={confirmRiskAction}
        riskLevel={riskDialog?.riskLevel || 'high'}
        details={riskDialog?.details}
        reason={riskReason}
        onReasonChange={setRiskReason}
        reasonRequired={false}
        reasonLabel="操作原因"
        confirmButtonLabel={riskSubmitting ? '执行中...' : '一键确认执行'}
        confirmDisabled={riskSubmitting}
        confirmMode="one-click"
      />

      <ConfirmDialog
        open={xshellConfirmOpen}
        title="导入 Xshell Sessions"
        description="将从本地 Xshell Sessions 目录读取服务器配置并写入资产库。请确认当前机器目录可信。"
        confirmLabel={xshellLoading ? '导入中...' : '确认导入'}
        onCancel={() => setXshellConfirmOpen(false)}
        onConfirm={handleXshellImport}
      />
    </div>
  )
}
