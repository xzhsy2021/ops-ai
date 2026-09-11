import { useEffect, useMemo, useState } from 'react'
import { ShieldCheck, ShieldAlert, RefreshCw, RotateCcw, Save, Eye } from 'lucide-react'

/**
 * EXEC_REMOTE（ad-hoc 远程命令执行审批）白名单配置面板。
 *
 * 背景：exec 白名单原先只存在于 capability_settings（API/DB），页面上没有任何
 * 入口——管理员开了 allowlist 模式后找不到「到底放行了哪些命令、在哪里加」。
 * 本面板展示**当前生效**的白名单（默认模板或管理员覆盖）并支持编辑。
 */
export type ExecPolicy = {
  mode?: string
  allow_exec_remote_tool?: boolean
  allow_prod?: boolean
  allow_multi_line?: boolean
  max_length?: number
  max_targets?: number
  max_timeout_seconds?: number
  max_per_hour?: number
  templates?: Array<Record<string, any>>
  templates_source?: string
  templates_overridden?: boolean
  default_templates?: Array<Record<string, any>>
  default_max_length?: number
}

type Draft = {
  mode: string
  allow_exec_remote_tool: boolean
  allow_prod: boolean
  allow_multi_line: boolean
  max_length: number
  max_timeout_seconds: number
  max_targets: number
  max_per_hour: number
}

const EMPTY_DRAFT: Draft = {
  mode: 'allowlist',
  allow_exec_remote_tool: false,
  allow_prod: true,
  allow_multi_line: false,
  max_length: 4096,
  max_timeout_seconds: 300,
  max_targets: 20,
  max_per_hour: 10,
}

function draftFrom(policy: ExecPolicy | null): Draft {
  if (!policy) return EMPTY_DRAFT
  return {
    mode: String(policy.mode || 'allowlist'),
    allow_exec_remote_tool: !!policy.allow_exec_remote_tool,
    allow_prod: policy.allow_prod !== false,
    allow_multi_line: !!policy.allow_multi_line,
    max_length: Number(policy.max_length || 4096),
    max_timeout_seconds: Number(policy.max_timeout_seconds || 300),
    max_targets: Number(policy.max_targets || 20),
    max_per_hour: Number(policy.max_per_hour ?? 10),
  }
}

export function ExecPolicyPanel({
  policy,
  loading,
  saving,
  isAdmin,
  onSave,
  onClearOverride,
  onReload,
  onPreview,
}: {
  policy: ExecPolicy | null
  loading: boolean
  saving: boolean
  isAdmin: boolean
  onSave: (payload: Record<string, any>) => Promise<void>
  onClearOverride: () => Promise<void>
  onReload: () => void
  onPreview: (payload: { command: string; environment: string }) => Promise<any>
}) {
  const [draft, setDraft] = useState<Draft>(draftFrom(policy))
  const [templateText, setTemplateText] = useState('[]')
  const [jsonError, setJsonError] = useState('')
  const [preview, setPreview] = useState('')
  // 试匹配走后端：模板是 Python re 语法，JS 的 RegExp 编译不了 (?P<name>...)，
  // 前端自己匹配会把含具名组的模板全部静默跳过（docker ps 就因此误报不命中）。
  const [previewEnv, setPreviewEnv] = useState('prod')
  const [previewResult, setPreviewResult] = useState<any>(null)
  const [previewError, setPreviewError] = useState('')
  const [previewBusy, setPreviewBusy] = useState(false)

  useEffect(() => {
    setDraft(draftFrom(policy))
    setTemplateText(JSON.stringify(policy?.templates || [], null, 2))
    setJsonError('')
  }, [policy])

  const overridden = !!policy?.templates_overridden
  const templates = policy?.templates || []
  const mode = draft.mode

  const statusColor = draft.allow_exec_remote_tool ? 'var(--success)' : 'var(--danger)'
  const statusBg = draft.allow_exec_remote_tool ? 'var(--success-surface)' : 'var(--danger-surface)'

  const buildPayload = (): Record<string, any> | null => {
    let parsed: any
    try {
      parsed = templateText.trim() ? JSON.parse(templateText) : []
    } catch (e: any) {
      setJsonError(`模板 JSON 解析失败：${e?.message || e}`)
      return null
    }
    if (!Array.isArray(parsed)) {
      setJsonError('模板必须是一个 JSON 数组（每项含 id / description / pattern / env）')
      return null
    }
    for (const item of parsed) {
      if (!item || typeof item !== 'object' || !item.id || !item.pattern) {
        setJsonError('每条模板必须含 id 与 pattern 字段')
        return null
      }
    }
    setJsonError('')
    return {
      exec_remote_mode: draft.mode,
      exec_remote_allow_prod: draft.allow_prod,
      exec_remote_allow_multiline: draft.allow_multi_line,
      exec_remote_max_length: Number(draft.max_length) || 4096,
      exec_remote_max_timeout_seconds: Number(draft.max_timeout_seconds) || 300,
      exec_remote_max_targets: Number(draft.max_targets) || 20,
      exec_remote_max_per_hour: Number(draft.max_per_hour) || 0,
      exec_remote_templates: parsed,
    }
  }

  const save = async () => {
    const payload = buildPayload()
    if (!payload) return
    await onSave(payload)
  }

  const previewMatch = async () => {
    const target = preview.trim()
    if (!target) return
    setPreviewBusy(true)
    setPreviewError('')
    try {
      const data = await onPreview({ command: target, environment: previewEnv })
      setPreviewResult(data || null)
    } catch (e: any) {
      setPreviewResult(null)
      setPreviewError(String(e?.message || e))
    } finally {
      setPreviewBusy(false)
    }
  }

  const switchRows = useMemo(() => ([
    {
      key: 'allow_exec_remote_tool' as const,
      label: '允许 AI 提交命令执行审批',
      desc: '总开关。关闭后 ops.approval.prepare_exec 直接 403，AI 无法发起任何 ad-hoc 命令审批。',
    },
    {
      key: 'allow_prod' as const,
      label: '允许生产环境执行',
      desc: '关闭后仅测试环境可提交；破坏性命令在生产环境始终恒拒。',
    },
    {
      key: 'allow_multi_line' as const,
      label: '允许多行命令',
      desc: '默认按单行校验，多行容易被用来夹带额外命令。',
    },
  ]), [])

  return (
    <div className="card" style={{ display: 'grid', gap: 14 }}>
      <div className="card-header">
        <div>
          <h2>命令执行白名单（EXEC_REMOTE）</h2>
          <span>
            allowlist 模式下命令必须完整匹配下面某条模板；模板为「可选覆盖」，不配置时用系统内置默认模板。
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span
            className="badge"
            style={{ background: statusBg, color: statusColor, padding: '2px 8px', borderRadius: 6, fontSize: 12 }}
          >
            {draft.allow_exec_remote_tool ? '已开启' : '已关闭'}
          </span>
          <button className="btn btn-subtle" onClick={onReload} disabled={loading || saving}>
            <RefreshCw size={14} /> 刷新
          </button>
        </div>
      </div>

      {!isAdmin && (
        <p className="muted">只读视图：仅管理员可修改白名单。</p>
      )}

      {/* 模式与开关 */}
      <div style={{ display: 'grid', gap: 10 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center' }}>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span>校验模式</span>
            <select
              value={mode}
              disabled={!isAdmin || saving}
              onChange={(e) => setDraft({ ...draft, mode: e.target.value })}
            >
              <option value="allowlist">allowlist（仅白名单模板，推荐）</option>
              <option value="free">free（任意命令，仅过黑名单/破坏性检查）</option>
            </select>
          </label>
          {mode === 'free' && (
            <span style={{ color: 'var(--warning)', display: 'flex', gap: 6, alignItems: 'center', fontSize: 13 }}>
              <ShieldAlert size={14} /> free 模式会放行任意命令，生产环境风险很高
            </span>
          )}
        </div>
        <div className="tool-switch-grid">
          {switchRows.map(({ key, label, desc }) => (
            <label key={key} className="tool-switch" title={desc}>
              <input
                type="checkbox"
                checked={!!draft[key]}
                disabled={!isAdmin || saving}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.checked })}
              />
              <span>{label}</span>
            </label>
          ))}
        </div>
      </div>

      {/* 限额 */}
      <div style={{ display: 'grid', gap: 8 }}>
        <strong>护栏限额</strong>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
          {([
            ['max_length', '命令长度上限', 1],
            ['max_timeout_seconds', '单命令超时（秒）', 1],
            ['max_targets', '单次最多目标数', 1],
            ['max_per_hour', '每小时最多审批数（0=不限）', 0],
          ] as const).map(([key, label, min]) => (
            <label key={key} style={{ display: 'grid', gap: 4, fontSize: 13 }}>
              <span style={{ color: 'var(--text-muted)' }}>{label}</span>
              <input
                type="number"
                min={min}
                style={{ width: 160 }}
                value={draft[key]}
                disabled={!isAdmin || saving}
                onChange={(e) => setDraft({ ...draft, [key]: Number(e.target.value) })}
              />
            </label>
          ))}
        </div>
      </div>

      {/* 当前生效模板 */}
      <div style={{ display: 'grid', gap: 8 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <strong>当前生效模板（{templates.length} 条）</strong>
          <span
            className="badge"
            style={{
              background: overridden ? 'var(--warning-surface)' : 'var(--success-surface)',
              color: overridden ? 'var(--warning)' : 'var(--success)',
              padding: '2px 8px', borderRadius: 6, fontSize: 12,
            }}
          >
            {overridden ? '管理员覆盖' : '系统默认'}
          </span>
          <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>
            <Eye size={13} /> 生产与测试环境均适用（模板 env 字段控制）
          </span>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="data-table data-table--compact">
            <thead>
              <tr>
                <th>模板 ID</th>
                <th>说明</th>
                <th>适用环境</th>
                <th>匹配表达式（完整匹配命令）</th>
              </tr>
            </thead>
            <tbody>
              {templates.length === 0 && (
                <tr><td colSpan={4} className="muted">暂无模板（allowlist 模式下将拒绝所有命令）</td></tr>
              )}
              {templates.map((t: any) => (
                <tr key={String(t.id)}>
                  <td><code>{String(t.id)}</code></td>
                  <td>{String(t.description || '-')}</td>
                  <td>{(t.env || ['test', 'prod']).join(' / ')}</td>
                  <td><code style={{ fontSize: 12, wordBreak: 'break-all' }}>{String(t.pattern)}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 命令试匹配 */}
      <div style={{ display: 'grid', gap: 6 }}>
        <strong>命令试匹配</strong>
        <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>
          由后端用同一份护栏判定（含环境过滤与参数校验），不是前端粗略正则。
        </span>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input
            style={{ flex: '1 1 320px', minWidth: 260 }}
            placeholder="例如 docker compose ps"
            value={preview}
            onChange={(e) => setPreview(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') previewMatch() }}
          />
          <select value={previewEnv} onChange={(e) => setPreviewEnv(e.target.value)}>
            <option value="prod">生产 prod</option>
            <option value="test">测试 test</option>
            <option value="">不限环境</option>
          </select>
          <button className="btn btn-subtle" onClick={previewMatch} disabled={!preview.trim() || previewBusy}>
            {previewBusy ? '判定中…' : '试匹配'}
          </button>
        </div>

        {previewError && (
          <div style={{ color: 'var(--danger)', fontSize: 13 }}>判定失败：{previewError}</div>
        )}

        {previewResult && (
          <div style={{ display: 'grid', gap: 6, marginTop: 2 }}>
            <div style={{
              fontSize: 13,
              color: previewResult.allowed ? 'var(--success)' : 'var(--danger)',
            }}>
              {previewResult.allowed
                ? `✅ 放行${previewResult.template_id ? `（命中模板 ${previewResult.template_id}）` : ''}`
                : `❌ 拒绝：${previewResult.reason || '未通过护栏校验'}`}
              {previewResult.environment ? `　环境=${previewResult.environment}` : '　环境=不限'}
              　模式={previewResult.mode}
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table className="data-table data-table--compact">
                <thead>
                  <tr>
                    <th>模板</th>
                    <th>适用环境</th>
                    <th>正则命中</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  {(previewResult.templates || []).map((row: any) => (
                    <tr key={String(row.id)}>
                      <td>{row.id}</td>
                      <td>{row.env_allowed ? '是' : '否'}</td>
                      <td>{row.error ? '正则错误' : (row.regex_matched ? '命中' : '—')}</td>
                      <td style={{ color: 'var(--text-muted)' }}>{row.error || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* 覆盖编辑 */}
      <div style={{ display: 'grid', gap: 8 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <strong>模板覆盖（JSON）</strong>
          <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>
            留空或点「恢复默认」即回到系统内置模板；一旦保存覆盖，后续升级新增的内置模板不会自动生效。
          </span>
        </div>
        <textarea
          className="code-block"
          style={{ width: '100%', minHeight: 200, fontFamily: 'monospace', fontSize: 12 }}
          value={templateText}
          disabled={!isAdmin || saving}
          onChange={(e) => setTemplateText(e.target.value)}
          spellCheck={false}
        />
        {jsonError && <p style={{ color: 'var(--danger)', margin: 0 }}>{jsonError}</p>}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button
            className="btn btn-primary"
            onClick={save}
            disabled={!isAdmin || saving}
          >
            <Save size={14} /> {saving ? '保存中…' : '保存白名单配置'}
          </button>
          <button
            className="btn btn-subtle"
            onClick={() => setTemplateText(JSON.stringify(policy?.default_templates || [], null, 2))}
            disabled={!isAdmin || saving}
          >
            <RotateCcw size={14} /> 载入默认模板到编辑框
          </button>
          <button
            className="btn btn-subtle"
            onClick={onClearOverride}
            disabled={!isAdmin || saving || !overridden}
            title={overridden ? '清除覆盖，回到系统内置模板' : '当前未覆盖'}
          >
            <ShieldCheck size={14} /> 清除覆盖（恢复默认）
          </button>
        </div>
      </div>
    </div>
  )
}
