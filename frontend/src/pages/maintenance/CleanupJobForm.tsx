import type { CSSProperties, Dispatch, SetStateAction } from 'react'

interface CleanupJobFormProps {
  jobForm: any
  setJobForm: Dispatch<SetStateAction<any>>
  connections: any[]
  tableOptions: string[]
  columnOptions: string[]
  tablesLoading: boolean
  columnsLoading: boolean
  jobSubmitting: boolean
  inputStyle: CSSProperties
  selectStyle: CSSProperties
  onConnectionChange: (connectionId: string) => void
  onTableChange: (tableName: string) => void
  onSubmit: () => void
  onCancel: () => void
}

export default function CleanupJobForm({
  jobForm,
  setJobForm,
  connections,
  tableOptions,
  columnOptions,
  tablesLoading,
  columnsLoading,
  jobSubmitting,
  inputStyle,
  selectStyle,
  onConnectionChange,
  onTableChange,
  onSubmit,
  onCancel,
}: CleanupJobFormProps) {
  return (
    <section className="glass-card maintenance-form-card">
      <div className="section-title-row">
        <div>
          <h2>创建数据清理任务</h2>
          <p>通过连接自动拉取表和字段。执行前先 Dry Run 和风险评估，复核后按批次 DELETE，过程写入批次日志和审计。</p>
        </div>
        <button className="btn btn-subtle" onClick={onCancel}>收起表单</button>
      </div>
      <div className="maintenance-form-section">
        <div className="maintenance-form-title"><strong>目标范围</strong><span>选择连接、数据库、表和日期字段</span></div>
        <div className="maintenance-form-grid maintenance-form-grid--wide">
          <label className="field-label">任务名称 *
            <input style={inputStyle} value={jobForm.name} onChange={e => setJobForm((f: any) => ({ ...f, name: e.target.value }))} placeholder="如 cleanup-order-logs-before-2024" />
          </label>
          <label className="field-label">数据库连接 *
            <select style={selectStyle} value={jobForm.connection_id} onChange={e => onConnectionChange(e.target.value)}>
              <option value="">请选择连接</option>
              {connections.map((c: any) => <option key={c.id} value={c.id}>{c.name} · {c.environment} · {c.database_name}</option>)}
            </select>
          </label>
          <label className="field-label">环境
            <input style={inputStyle} value={jobForm.environment} onChange={e => setJobForm((f: any) => ({ ...f, environment: e.target.value }))} />
          </label>
          <label className="field-label">数据库名
            <input style={inputStyle} value={jobForm.database_name} onChange={e => setJobForm((f: any) => ({ ...f, database_name: e.target.value }))} />
          </label>
          <label className="field-label">表名 *
            <select style={selectStyle} value={jobForm.table_name} onChange={e => onTableChange(e.target.value)} disabled={!jobForm.connection_id || tablesLoading}>
              <option value="">{tablesLoading ? '加载表中...' : '请选择表'}</option>
              {tableOptions.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label className="field-label">日期列 *
            <select style={selectStyle} value={jobForm.date_column} onChange={e => setJobForm((f: any) => ({ ...f, date_column: e.target.value }))} disabled={!jobForm.table_name || columnsLoading}>
              <option value="">{columnsLoading ? '加载字段中...' : '请选择字段'}</option>
              {columnOptions.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
        </div>
      </div>

      <div className="maintenance-form-section">
        <div className="maintenance-form-title"><strong>执行策略</strong><span>配置截止时间、批次大小、间隔和最大处理行数</span></div>
        <div className="maintenance-form-grid maintenance-form-grid--wide">
          <label className="field-label">截止时间 *
            <input style={inputStyle} value={jobForm.cutoff_time} onChange={e => setJobForm((f: any) => ({ ...f, cutoff_time: e.target.value }))} placeholder="2025-01-01 00:00:00" />
          </label>
          <label className="field-label">批次大小
            <input style={inputStyle} type="number" value={jobForm.batch_size} onChange={e => setJobForm((f: any) => ({ ...f, batch_size: Number(e.target.value) }))} />
          </label>
          <label className="field-label">批次间隔秒
            <input style={inputStyle} type="number" value={jobForm.batch_interval_seconds} onChange={e => setJobForm((f: any) => ({ ...f, batch_interval_seconds: Number(e.target.value) }))} />
          </label>
          <label className="field-label">最大处理行数
            <input style={inputStyle} value={jobForm.max_delete_rows} onChange={e => setJobForm((f: any) => ({ ...f, max_delete_rows: e.target.value }))} placeholder="超过该值自动暂停" />
          </label>
          <label className="switch-line maintenance-span-2">
            <input type="checkbox" checked={jobForm.approval_required} onChange={e => setJobForm((f: any) => ({ ...f, approval_required: e.target.checked }))} />
            需要人工复核后执行
          </label>
        </div>
      </div>

      <div className="maintenance-form-actions">
        <button className="btn" onClick={onSubmit} disabled={jobSubmitting} style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>
          {jobSubmitting ? '创建中...' : '创建清理任务'}
        </button>
        <button className="btn" onClick={onCancel} style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
          取消
        </button>
      </div>
    </section>
  )
}
