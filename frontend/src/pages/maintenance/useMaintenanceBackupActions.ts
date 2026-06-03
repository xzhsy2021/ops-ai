import { useState, type ChangeEvent } from 'react'
import { adminMaintenance } from '../../api'

interface UseMaintenanceBackupActionsOptions {
  refreshBackups: () => void | Promise<void>
  showMsg: (message: string) => void
  showErr: (message: string) => void
}

type PendingRiskAction = {
  kind: 'restore' | 'delete'
  file: string
  title: string
  description: string
  target: string
  confirmText: string
  riskLevel: 'high' | 'critical'
  details: Array<{ label: string; value: string }>
}

function errText(e: any, fallback: string): string {
  if (typeof e === 'string') return e
  if (e?.message) return e.message
  return fallback
}

export function useMaintenanceBackupActions({ refreshBackups, showMsg, showErr }: UseMaintenanceBackupActionsOptions) {
  const [pendingRiskAction, setPendingRiskAction] = useState<PendingRiskAction | null>(null)
  const [riskConfirmValue, setRiskConfirmValue] = useState('')
  const [riskReason, setRiskReason] = useState('')
  const [riskSubmitting, setRiskSubmitting] = useState(false)

  const resetRiskDialog = () => {
    setPendingRiskAction(null)
    setRiskConfirmValue('')
    setRiskReason('')
    setRiskSubmitting(false)
  }

  const handleBackup = async () => {
    try {
      const res: any = await adminMaintenance.backupDb()
      const file = res?.data?.file
      showMsg(file ? `备份已创建并校验：${file}` : '备份已创建并校验')
      refreshBackups()
    } catch (e) {
      showErr(errText(e, '备份失败'))
    }
  }

  const handleExport = async () => {
    try {
      const res: any = await adminMaintenance.exportConfig()
      const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'ops_config_export.json'
      a.click()
      URL.revokeObjectURL(url)
      showMsg('配置已导出')
    } catch (e) {
      showErr(errText(e, '导出失败'))
    }
  }

  const handleVerify = async (file: string) => {
    try {
      const res: any = await adminMaintenance.verifyBackup(file)
      const data = res?.data || {}
      if (data.valid) {
        showMsg(`备份校验通过：${file}${data.sha256 ? `，SHA256=${String(data.sha256).slice(0, 12)}...` : ''}`)
      } else {
        showErr(`备份校验失败：${(data.errors || []).join('；') || file}`)
      }
      refreshBackups()
    } catch (e) {
      showErr(errText(e, '备份校验失败'))
    }
  }

  const handleRestore = async (file: string) => {
    setPendingRiskAction({
      kind: 'restore',
      file,
      title: '恢复数据库备份',
      description: '该操作会用选中的备份替换当前 SQLite 数据库。系统会先创建安全备份，恢复完成后必须重启后端进程。',
      target: file,
      confirmText: `RESTORE ${file}`,
      riskLevel: 'critical',
      details: [
        { label: '影响范围', value: '当前 OPS 数据库' },
        { label: '安全措施', value: '恢复前自动创建安全备份' },
        { label: '恢复后动作', value: '重启后端应用以释放旧连接' },
      ],
    })
    setRiskConfirmValue('')
    setRiskReason('')
  }

  const handleDelete = async (file: string) => {
    setPendingRiskAction({
      kind: 'delete',
      file,
      title: '删除数据库备份',
      description: '删除备份不可撤销。请确认该备份已不再用于回滚或审计留存。',
      target: file,
      confirmText: `DELETE ${file}`,
      riskLevel: 'high',
      details: [
        { label: '影响范围', value: '备份文件永久删除' },
        { label: '可恢复性', value: '不可恢复' },
      ],
    })
    setRiskConfirmValue('')
    setRiskReason('')
  }

  const confirmRiskAction = async () => {
    if (!pendingRiskAction) return
    const effectiveConfirmText = (riskConfirmValue.trim() || pendingRiskAction.confirmText).trim()
    setRiskSubmitting(true)
    try {
      if (pendingRiskAction.kind === 'restore') {
        const res: any = await adminMaintenance.restoreDb(pendingRiskAction.file, effectiveConfirmText, true, riskReason.trim())
        const safety = res?.data?.safety_backup?.file
        showMsg(safety ? `已恢复，请重启应用。安全备份：${safety}` : '已恢复，请重启应用')
      } else {
        await adminMaintenance.deleteBackup(pendingRiskAction.file, effectiveConfirmText, riskReason.trim())
        showMsg(`备份已删除：${pendingRiskAction.file}`)
      }
      resetRiskDialog()
      refreshBackups()
    } catch (e) {
      setRiskSubmitting(false)
      showErr(errText(e, pendingRiskAction.kind === 'restore' ? '恢复失败' : '删除失败'))
    }
  }

  const handleImportClick = () => document.getElementById('importFile')?.click()

  const handleFileImport = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      const text = await file.text()
      const data = JSON.parse(text)
      await adminMaintenance.importConfig(data)
      showMsg('配置已导入')
    } catch (err) {
      showErr(errText(err, '导入失败，请检查文件格式'))
    }
    e.target.value = ''
  }

  return {
    handleBackup,
    handleExport,
    handleRestore,
    handleVerify,
    handleDelete,
    handleImportClick,
    handleFileImport,
    riskDialog: pendingRiskAction,
    riskConfirmValue,
    setRiskConfirmValue,
    riskReason,
    setRiskReason,
    riskSubmitting,
    cancelRiskDialog: resetRiskDialog,
    confirmRiskAction,
  }
}
