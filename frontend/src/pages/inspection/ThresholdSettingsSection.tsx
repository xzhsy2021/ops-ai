import { useEffect, useState } from 'react'
import { inspection } from '../../api'

export type ThresholdSettingsSectionProps = {
  compact?: boolean
}

export function ThresholdSettingsSection({ compact = false }: ThresholdSettingsSectionProps = {}) {
  const [thresholds, setThresholds] = useState<Record<string, any> | null>(null)
  const [saving, setSaving] = useState<string | null>(null)
  const [message, setMessage] = useState<string>('')
  const [error, setError] = useState<string>('')

  useEffect(() => {
    (async () => {
      try {
        const res: any = await inspection.getThresholds()
        setThresholds(res.data || {})
      } catch (e: any) { setError(e?.message || String(e)) }
    })()
  }, [])

  function updateField(cat: string, key: string, value: any) {
    setThresholds((prev) => prev ? { ...prev, [cat]: { ...(prev[cat] || {}), [key]: value } } : prev)
  }

  function updateList(cat: string, key: string, text: string) {
    const arr = text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean)
    updateField(cat, key, arr)
  }

  async function saveCategory(cat: string) {
    if (!thresholds) return
    setSaving(cat); setMessage(''); setError('')
    try {
      await inspection.saveThresholds(cat, thresholds[cat] || {})
      setMessage(`${cat} 阈值已保存`)
    } catch (e: any) { setError(e?.message || String(e)) }
    finally { setSaving(null) }
  }

  if (!thresholds) {
    return compact
      ? <div className="muted" style={{ padding: 12 }}>阈值加载中…</div>
      : <section className="panel-card"><h2>巡检阈值配置</h2><p className="muted">加载中…</p></section>
  }

  const disk = thresholds.DISK || {}
  const backup = thresholds.BACKUP || {}
  const login = thresholds.LOGIN_SECURITY || {}
  const port = thresholds.PROCESS_PORT || {}
  const account = thresholds.ACCOUNT_SECURITY || {}
  const cmdHist = thresholds.COMMAND_HISTORY || {}
  const firewall = thresholds.FIREWALL || {}
  const service = thresholds.SERVICE_STATUS || {}
  const memoryThres = thresholds.MEMORY || {}

  if (compact) {
    return (
      <div>
        {message && <div className="alert alert-success" style={{ marginTop: 8 }}>{message}</div>}
        {error && <div className="alert alert-danger" style={{ marginTop: 8 }}>{error}</div>}
        <div className="threshold-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12, marginTop: 12 }}>
          {/* DISK */}
          <div className="mini-card">
            <strong>磁盘空间（DISK）</strong>
            <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>达「高阈值」判 HIGH；「中阈值」判 MEDIUM；系统分区额外严格 N%</p>
            <label style={{ display: 'block' }}>高阈值（%）<input type="number" min={50} max={100} value={disk.high_pct ?? 90} onChange={(e) => updateField('DISK', 'high_pct', Number(e.target.value))} style={{ width: 80, marginLeft: 6 }} /></label>
            <label style={{ display: 'block' }}>中阈值（%）<input type="number" min={30} max={100} value={disk.medium_pct ?? 75} onChange={(e) => updateField('DISK', 'medium_pct', Number(e.target.value))} style={{ width: 80, marginLeft: 6 }} /></label>
            <label style={{ display: 'block' }}>系统分区路径<input type="text" value={(disk.system_mounts || []).join(', ')} onChange={(e) => updateList('DISK', 'system_mounts', e.target.value)} style={{ width: '100%', marginLeft: 6 }} /></label>
            <label style={{ display: 'block' }}>系统分区严格偏移(%)<input type="number" min={0} max={30} value={disk.system_pct_offset ?? 5} onChange={(e) => updateField('DISK', 'system_pct_offset', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
            <label style={{ display: 'block' }}>inode高阈值(%)<input type="number" min={50} max={100} value={disk.inode_high_pct ?? 90} onChange={(e) => updateField('DISK', 'inode_high_pct', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
            <label style={{ display: 'block' }}>inode中阈值(%)<input type="number" min={30} max={100} value={disk.inode_medium_pct ?? 80} onChange={(e) => updateField('DISK', 'inode_medium_pct', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
            <button className="btn primary" disabled={saving === 'DISK'} onClick={() => saveCategory('DISK')}>{saving === 'DISK' ? '保存中…' : '保存'}</button>
          </div>

          {/* BACKUP */}
          <div className="mini-card">
            <strong>备份任务（BACKUP）</strong>
            <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>无备份判 HIGH；有cron无文件/0字节文件判 MEDIUM</p>
            <label style={{ display: 'block' }}>备份路径（逗号或换行分隔）<textarea rows={2} value={(Array.isArray(backup.backup_paths) ? backup.backup_paths : []).join('\n')} onChange={(e) => updateList('BACKUP', 'backup_paths', e.target.value)} style={{ width: '100%' }} /></label>
            <label style={{ display: 'block' }}>最近 N 天<input type="number" min={1} max={30} value={backup.min_age_days ?? 2} onChange={(e) => updateField('BACKUP', 'min_age_days', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
            <button className="btn primary" disabled={saving === 'BACKUP'} onClick={() => saveCategory('BACKUP')}>{saving === 'BACKUP' ? '保存中…' : '保存'}</button>
          </div>

          {/* LOGIN */}
          <div className="mini-card">
            <strong>登录安全（LOGIN_SECURITY）</strong>
            <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>失败登录达到高阈值判 HIGH；低阈值判 LOW；root远程登录判 MEDIUM</p>
            <label style={{ display: 'block' }}>失败登录高阈值<input type="number" min={1} max={1000} value={login.failed_high ?? 10} onChange={(e) => updateField('LOGIN_SECURITY', 'failed_high', Number(e.target.value))} style={{ width: 80, marginLeft: 6 }} /></label>
            <label style={{ display: 'block' }}>失败登录低阈值<input type="number" min={1} max={1000} value={login.failed_low ?? 3} onChange={(e) => updateField('LOGIN_SECURITY', 'failed_low', Number(e.target.value))} style={{ width: 80, marginLeft: 6 }} /></label>
            <label style={{ display: 'block' }}>统计时间窗口(小时)<input type="number" min={1} max={720} value={login.failed_window_hours ?? 24} onChange={(e) => updateField('LOGIN_SECURITY', 'failed_window_hours', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
            <label style={{ display: 'block' }}>root远程登录判 MEDIUM<input type="checkbox" checked={login.root_remote_medium !== false} onChange={(e) => updateField('LOGIN_SECURITY', 'root_remote_medium', e.target.checked)} style={{ marginLeft: 6 }} /></label>
            <button className="btn primary" disabled={saving === 'LOGIN_SECURITY'} onClick={() => saveCategory('LOGIN_SECURITY')}>{saving === 'LOGIN_SECURITY' ? '保存中…' : '保存'}</button>
          </div>

          {/* PROCESS_PORT */}
          <div className="mini-card">
            <strong>进程端口（PROCESS_PORT）</strong>
            <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>公网暴露高危端口判 MEDIUM；可疑进程判 HIGH；CPU超阈值判 MEDIUM</p>
            <label style={{ display: 'block' }}>高危端口（逗号或换行分隔）<textarea rows={2} value={(port.high_risk_ports || []).join(', ')} onChange={(e) => updateList('PROCESS_PORT', 'high_risk_ports', e.target.value)} style={{ width: '100%' }} /></label>
            <label style={{ display: 'block' }}>可疑进程关键字（逗号或换行分隔）<textarea rows={2} value={(port.suspicious_keywords || []).join(', ')} onChange={(e) => updateList('PROCESS_PORT', 'suspicious_keywords', e.target.value)} style={{ width: '100%' }} /></label>
            <label style={{ display: 'block' }}>CPU 阈值（%）<input type="number" min={10} max={100} value={port.cpu_threshold ?? 80} onChange={(e) => updateField('PROCESS_PORT', 'cpu_threshold', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
            <button className="btn primary" disabled={saving === 'PROCESS_PORT'} onClick={() => saveCategory('PROCESS_PORT')}>{saving === 'PROCESS_PORT' ? '保存中…' : '保存'}</button>
          </div>

          {/* ACCOUNT_SECURITY */}
          <div className="mini-card">
            <strong>账号安全（ACCOUNT_SECURITY）</strong>
            <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>UID=0 超阈值判 HIGH；可登录账号超阈值判 MEDIUM/LOW</p>
            <label style={{ display: 'block' }}>UID=0 阈值<input type="number" min={1} max={10} value={account.max_uid0 ?? 1} onChange={(e) => updateField('ACCOUNT_SECURITY', 'max_uid0', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
            <label style={{ display: 'block' }}>可登录账号 LOW 阈值<input type="number" min={1} max={100} value={account.max_login_users ?? 10} onChange={(e) => updateField('ACCOUNT_SECURITY', 'max_login_users', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
            <label style={{ display: 'block' }}>可登录账号 MEDIUM 阈值<input type="number" min={1} max={100} value={account.max_login_users_medium ?? 20} onChange={(e) => updateField('ACCOUNT_SECURITY', 'max_login_users_medium', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
            <button className="btn primary" disabled={saving === 'ACCOUNT_SECURITY'} onClick={() => saveCategory('ACCOUNT_SECURITY')}>{saving === 'ACCOUNT_SECURITY' ? '保存中…' : '保存'}</button>
          </div>

          {/* COMMAND_HISTORY */}
          <div className="mini-card">
            <strong>命令日志（COMMAND_HISTORY）</strong>
            <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>高危命令判 HIGH/MEDIUM；管道攻击判 HIGH；history清空判 HIGH</p>
            <label style={{ display: 'block' }}>高危关键字（逗号或换行分隔）<textarea rows={2} value={(cmdHist.high_keywords || []).join(', ')} onChange={(e) => updateList('COMMAND_HISTORY', 'high_keywords', e.target.value)} style={{ width: '100%' }} /></label>
            <label style={{ display: 'block' }}>管道攻击组合（每行: keyword1,keyword2）<textarea rows={2} value={(cmdHist.pipe_combos || []).map((p: string[]) => Array.isArray(p) ? p.join(', ') : p).join('\n')} onChange={(e) => { const arr = e.target.value.split('\n').filter(Boolean).map((s) => s.split(',').map((x) => x.trim()).filter(Boolean)); updateField('COMMAND_HISTORY', 'pipe_combos', arr) }} style={{ width: '100%' }} /></label>
            <button className="btn primary" disabled={saving === 'COMMAND_HISTORY'} onClick={() => saveCategory('COMMAND_HISTORY')}>{saving === 'COMMAND_HISTORY' ? '保存中…' : '保存'}</button>
          </div>

          {/* FIREWALL */}
          <div className="mini-card">
            <strong>防火墙（FIREWALL）</strong>
            <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>防火墙未启用时的风险等级（云环境可设为 LOW）</p>
            <label style={{ display: 'block' }}>未启用等级<select value={firewall.inactive_level ?? 'MEDIUM'} onChange={(e) => updateField('FIREWALL', 'inactive_level', e.target.value)} style={{ marginLeft: 6 }}><option value="LOW">LOW</option><option value="MEDIUM">MEDIUM</option><option value="HIGH">HIGH</option></select></label>
            <button className="btn primary" disabled={saving === 'FIREWALL'} onClick={() => saveCategory('FIREWALL')}>{saving === 'FIREWALL' ? '保存中…' : '保存'}</button>
          </div>

          {/* SERVICE_STATUS */}
          <div className="mini-card">
            <strong>服务状态（SERVICE_STATUS）</strong>
            <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>核心服务失败判 HIGH；其他失败单元判 MEDIUM</p>
            <label style={{ display: 'block' }}>核心服务（逗号或换行分隔）<textarea rows={2} value={(service.core_services || []).join(', ')} onChange={(e) => updateList('SERVICE_STATUS', 'core_services', e.target.value)} style={{ width: '100%' }} /></label>
            <button className="btn primary" disabled={saving === 'SERVICE_STATUS'} onClick={() => saveCategory('SERVICE_STATUS')}>{saving === 'SERVICE_STATUS' ? '保存中…' : '保存'}</button>
          </div>

          {/* MEMORY */}
          <div className="mini-card">
            <strong>内存状况（MEMORY）</strong>
            <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>内存使用率达高阈值判 HIGH，中阈值判 MEDIUM；Swap 达临界阈值判 HIGH</p>
            <label style={{ display: 'block' }}>内存高阈值(%)<input type="number" min={50} max={100} value={memoryThres.mem_high_pct ?? 95} onChange={(e) => updateField('MEMORY', 'mem_high_pct', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
            <label style={{ display: 'block' }}>内存中阈值(%)<input type="number" min={30} max={100} value={memoryThres.mem_medium_pct ?? 85} onChange={(e) => updateField('MEMORY', 'mem_medium_pct', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
            <label style={{ display: 'block' }}>Swap高阈值(%)<input type="number" min={10} max={100} value={memoryThres.swap_high_pct ?? 50} onChange={(e) => updateField('MEMORY', 'swap_high_pct', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
            <label style={{ display: 'block' }}>Swap临界阈值(%)<input type="number" min={10} max={100} value={memoryThres.swap_critical_pct ?? 80} onChange={(e) => updateField('MEMORY', 'swap_critical_pct', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
            <button className="btn primary" disabled={saving === 'MEMORY'} onClick={() => saveCategory('MEMORY')}>{saving === 'MEMORY' ? '保存中…' : '保存'}</button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <section className="panel-card">
      <h2>巡检阈值配置</h2>
      <p className="muted">为各巡检项设置判定阈值。修改后立即生效，后续巡检会按新阈值评分；报告会展示「评分依据」小节。</p>
      {message && <div className="alert alert-success" style={{ marginTop: 8 }}>{message}</div>}
      {error && <div className="alert alert-danger" style={{ marginTop: 8 }}>{error}</div>}

      <div className="threshold-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12, marginTop: 12 }}>
        {/* DISK */}
        <div className="mini-card">
          <strong>磁盘空间（DISK）</strong>
          <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>达「高阈值」判 HIGH；「中阈值」判 MEDIUM；系统分区额外严格 N%</p>
          <label style={{ display: 'block' }}>高阈值（%）<input type="number" min={50} max={100} value={disk.high_pct ?? 90} onChange={(e) => updateField('DISK', 'high_pct', Number(e.target.value))} style={{ width: 80, marginLeft: 6 }} /></label>
          <label style={{ display: 'block' }}>中阈值（%）<input type="number" min={30} max={100} value={disk.medium_pct ?? 75} onChange={(e) => updateField('DISK', 'medium_pct', Number(e.target.value))} style={{ width: 80, marginLeft: 6 }} /></label>
          <label style={{ display: 'block' }}>系统分区路径<input type="text" value={(disk.system_mounts || []).join(', ')} onChange={(e) => updateList('DISK', 'system_mounts', e.target.value)} style={{ width: '100%', marginLeft: 6 }} /></label>
          <label style={{ display: 'block' }}>系统分区严格偏移(%)<input type="number" min={0} max={30} value={disk.system_pct_offset ?? 5} onChange={(e) => updateField('DISK', 'system_pct_offset', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
          <label style={{ display: 'block' }}>inode高阈值(%)<input type="number" min={50} max={100} value={disk.inode_high_pct ?? 90} onChange={(e) => updateField('DISK', 'inode_high_pct', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
          <label style={{ display: 'block' }}>inode中阈值(%)<input type="number" min={30} max={100} value={disk.inode_medium_pct ?? 80} onChange={(e) => updateField('DISK', 'inode_medium_pct', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
          <button className="btn primary" disabled={saving === 'DISK'} onClick={() => saveCategory('DISK')}>{saving === 'DISK' ? '保存中…' : '保存'}</button>
        </div>

        {/* BACKUP */}
        <div className="mini-card">
          <strong>备份任务（BACKUP）</strong>
          <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>无备份判 HIGH；有cron无文件/0字节文件判 MEDIUM</p>
          <label style={{ display: 'block' }}>备份路径（逗号或换行分隔）<textarea rows={2} value={(Array.isArray(backup.backup_paths) ? backup.backup_paths : []).join('\n')} onChange={(e) => updateList('BACKUP', 'backup_paths', e.target.value)} style={{ width: '100%' }} /></label>
          <label style={{ display: 'block' }}>最近 N 天<input type="number" min={1} max={30} value={backup.min_age_days ?? 2} onChange={(e) => updateField('BACKUP', 'min_age_days', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
          <button className="btn primary" disabled={saving === 'BACKUP'} onClick={() => saveCategory('BACKUP')}>{saving === 'BACKUP' ? '保存中…' : '保存'}</button>
        </div>

        {/* LOGIN */}
        <div className="mini-card">
          <strong>登录安全（LOGIN_SECURITY）</strong>
          <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>失败登录达到高阈值判 HIGH；低阈值判 LOW；root远程登录判 MEDIUM</p>
          <label style={{ display: 'block' }}>失败登录高阈值<input type="number" min={1} max={1000} value={login.failed_high ?? 10} onChange={(e) => updateField('LOGIN_SECURITY', 'failed_high', Number(e.target.value))} style={{ width: 80, marginLeft: 6 }} /></label>
          <label style={{ display: 'block' }}>失败登录低阈值<input type="number" min={1} max={1000} value={login.failed_low ?? 3} onChange={(e) => updateField('LOGIN_SECURITY', 'failed_low', Number(e.target.value))} style={{ width: 80, marginLeft: 6 }} /></label>
          <label style={{ display: 'block' }}>统计时间窗口(小时)<input type="number" min={1} max={720} value={login.failed_window_hours ?? 24} onChange={(e) => updateField('LOGIN_SECURITY', 'failed_window_hours', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
          <label style={{ display: 'block' }}>root远程登录判 MEDIUM<input type="checkbox" checked={login.root_remote_medium !== false} onChange={(e) => updateField('LOGIN_SECURITY', 'root_remote_medium', e.target.checked)} style={{ marginLeft: 6 }} /></label>
          <button className="btn primary" disabled={saving === 'LOGIN_SECURITY'} onClick={() => saveCategory('LOGIN_SECURITY')}>{saving === 'LOGIN_SECURITY' ? '保存中…' : '保存'}</button>
        </div>

        {/* PROCESS_PORT */}
        <div className="mini-card">
          <strong>进程端口（PROCESS_PORT）</strong>
          <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>公网暴露高危端口判 MEDIUM；可疑进程判 HIGH；CPU超阈值判 MEDIUM</p>
          <label style={{ display: 'block' }}>高危端口（逗号或换行分隔）<textarea rows={2} value={(port.high_risk_ports || []).join(', ')} onChange={(e) => updateList('PROCESS_PORT', 'high_risk_ports', e.target.value)} style={{ width: '100%' }} /></label>
          <label style={{ display: 'block' }}>可疑进程关键字（逗号或换行分隔）<textarea rows={2} value={(port.suspicious_keywords || []).join(', ')} onChange={(e) => updateList('PROCESS_PORT', 'suspicious_keywords', e.target.value)} style={{ width: '100%' }} /></label>
          <label style={{ display: 'block' }}>CPU 阈值（%）<input type="number" min={10} max={100} value={port.cpu_threshold ?? 80} onChange={(e) => updateField('PROCESS_PORT', 'cpu_threshold', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
          <button className="btn primary" disabled={saving === 'PROCESS_PORT'} onClick={() => saveCategory('PROCESS_PORT')}>{saving === 'PROCESS_PORT' ? '保存中…' : '保存'}</button>
        </div>

        {/* ACCOUNT_SECURITY */}
        <div className="mini-card">
          <strong>账号安全（ACCOUNT_SECURITY）</strong>
          <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>UID=0 超阈值判 HIGH；可登录账号超阈值判 MEDIUM/LOW</p>
          <label style={{ display: 'block' }}>UID=0 阈值<input type="number" min={1} max={10} value={account.max_uid0 ?? 1} onChange={(e) => updateField('ACCOUNT_SECURITY', 'max_uid0', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
          <label style={{ display: 'block' }}>可登录账号 LOW 阈值<input type="number" min={1} max={100} value={account.max_login_users ?? 10} onChange={(e) => updateField('ACCOUNT_SECURITY', 'max_login_users', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
          <label style={{ display: 'block' }}>可登录账号 MEDIUM 阈值<input type="number" min={1} max={100} value={account.max_login_users_medium ?? 20} onChange={(e) => updateField('ACCOUNT_SECURITY', 'max_login_users_medium', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
          <button className="btn primary" disabled={saving === 'ACCOUNT_SECURITY'} onClick={() => saveCategory('ACCOUNT_SECURITY')}>{saving === 'ACCOUNT_SECURITY' ? '保存中…' : '保存'}</button>
        </div>

        {/* COMMAND_HISTORY */}
        <div className="mini-card">
          <strong>命令日志（COMMAND_HISTORY）</strong>
          <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>高危命令判 HIGH/MEDIUM；管道攻击判 HIGH；history清空判 HIGH</p>
          <label style={{ display: 'block' }}>高危关键字（逗号或换行分隔）<textarea rows={2} value={(cmdHist.high_keywords || []).join(', ')} onChange={(e) => updateList('COMMAND_HISTORY', 'high_keywords', e.target.value)} style={{ width: '100%' }} /></label>
          <label style={{ display: 'block' }}>管道攻击组合（每行: keyword1,keyword2）<textarea rows={2} value={(cmdHist.pipe_combos || []).map((p: string[]) => Array.isArray(p) ? p.join(', ') : p).join('\n')} onChange={(e) => { const arr = e.target.value.split('\n').filter(Boolean).map((s) => s.split(',').map((x) => x.trim()).filter(Boolean)); updateField('COMMAND_HISTORY', 'pipe_combos', arr) }} style={{ width: '100%' }} /></label>
          <button className="btn primary" disabled={saving === 'COMMAND_HISTORY'} onClick={() => saveCategory('COMMAND_HISTORY')}>{saving === 'COMMAND_HISTORY' ? '保存中…' : '保存'}</button>
        </div>

        {/* FIREWALL */}
        <div className="mini-card">
          <strong>防火墙（FIREWALL）</strong>
          <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>防火墙未启用时的风险等级（云环境可设为 LOW）</p>
          <label style={{ display: 'block' }}>未启用等级<select value={firewall.inactive_level ?? 'MEDIUM'} onChange={(e) => updateField('FIREWALL', 'inactive_level', e.target.value)} style={{ marginLeft: 6 }}><option value="LOW">LOW</option><option value="MEDIUM">MEDIUM</option><option value="HIGH">HIGH</option></select></label>
          <button className="btn primary" disabled={saving === 'FIREWALL'} onClick={() => saveCategory('FIREWALL')}>{saving === 'FIREWALL' ? '保存中…' : '保存'}</button>
        </div>

        {/* SERVICE_STATUS */}
        <div className="mini-card">
          <strong>服务状态（SERVICE_STATUS）</strong>
          <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>核心服务失败判 HIGH；其他失败单元判 MEDIUM</p>
          <label style={{ display: 'block' }}>核心服务（逗号或换行分隔）<textarea rows={2} value={(service.core_services || []).join(', ')} onChange={(e) => updateList('SERVICE_STATUS', 'core_services', e.target.value)} style={{ width: '100%' }} /></label>
          <button className="btn primary" disabled={saving === 'SERVICE_STATUS'} onClick={() => saveCategory('SERVICE_STATUS')}>{saving === 'SERVICE_STATUS' ? '保存中…' : '保存'}</button>
        </div>

        {/* MEMORY */}
        <div className="mini-card">
          <strong>内存状况（MEMORY）</strong>
          <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>内存使用率达高阈值判 HIGH，中阈值判 MEDIUM；Swap 达临界阈值判 HIGH</p>
          <label style={{ display: 'block' }}>内存高阈值(%)<input type="number" min={50} max={100} value={memoryThres.mem_high_pct ?? 95} onChange={(e) => updateField('MEMORY', 'mem_high_pct', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
          <label style={{ display: 'block' }}>内存中阈值(%)<input type="number" min={30} max={100} value={memoryThres.mem_medium_pct ?? 85} onChange={(e) => updateField('MEMORY', 'mem_medium_pct', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
          <label style={{ display: 'block' }}>Swap高阈值(%)<input type="number" min={10} max={100} value={memoryThres.swap_high_pct ?? 50} onChange={(e) => updateField('MEMORY', 'swap_high_pct', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
          <label style={{ display: 'block' }}>Swap临界阈值(%)<input type="number" min={10} max={100} value={memoryThres.swap_critical_pct ?? 80} onChange={(e) => updateField('MEMORY', 'swap_critical_pct', Number(e.target.value))} style={{ width: 60, marginLeft: 6 }} /></label>
          <button className="btn primary" disabled={saving === 'MEMORY'} onClick={() => saveCategory('MEMORY')}>{saving === 'MEMORY' ? '保存中…' : '保存'}</button>
        </div>
      </div>
    </section>
  )
}
