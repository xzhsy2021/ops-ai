import type { CSSProperties } from 'react'
import { StatusBadge } from '../../components/ui'
import PreflightPanel from './PreflightPanel'
import ReleaseConfirmationPanel from './ReleaseConfirmationPanel'

interface DeployFormProps {
  systems: any[]
  services: any[]
  environments: any[]
  pipelines: any[]
  serverGroupList: any[]
  deployPackages: any[]
  system: string
  service: string
  environment: string
  fileName: string
  servers: string
  pipelineId: string
  pipelineSteps: any[]
  serverGroup: string
  serverAutoMode: boolean
  selectedService: any
  selectedServerNames: string[]
  candidateServerNames: string[]
  selectedServerSet: Set<string>
  recommendedServerSet: Set<string>
  selectedServerEnvironmentConflictSet: Set<string>
  serverMetaByName: Map<string, any>
  environmentServerErrorText: string
  status: string
  statusColor: string
  loading: boolean
  confirmingRelease: boolean
  prechecking: boolean
  resolving: boolean
  releaseConfirmation: any
  resolveResult: any
  precheckResult: any
  parallelism: number
  failFast: boolean
  selectStyle: CSSProperties
  navigate: (path: string) => void
  setSystem: (value: string) => void
  setService: (value: string) => void
  setEnvironment: (value: string) => void
  setPipelineId: (value: string) => void
  setFileName: (value: string) => void
  setServers: (value: string) => void
  setServerGroup: (value: string) => void
  setServerAutoMode: (value: boolean) => void
  setParallelism: (value: number) => void
  setFailFast: (value: boolean) => void
  applyServiceDefaults: (value: string, options?: any) => void
  autoFillServers: (options?: any) => void
  resolveServerNamesForTarget: (service: any) => string[]
  selectRecommendedServers: () => void
  selectAllCandidateServers: () => void
  clearSelectedServers: () => void
  invertCandidateServers: () => void
  toggleServerSelection: (name: string, checked: boolean) => void
  serverLooksLikeTest: (name: string) => boolean
  isProdEnvironment: (value: any) => boolean
  handlePrecheck: () => void
  handleResolve: () => void
  handleDeploy: () => void
}


function serverDisplayName(value: any): string {
  const text = String(value || '').trim()
  return text || '未命名服务器'
}

function compactServerMeta(meta: any, fallbackName: string) {
  const rawHost = meta?.host || meta?.ip || ''
  const rawEnv = meta?.environment || ''
  const rawGroup = meta?.group || ''
  const missing: string[] = []
  if (!rawHost) missing.push('主机/IP')
  if (!rawEnv && !rawGroup) missing.push('环境/组')
  return {
    displayName: meta?.displayName || serverDisplayName(fallbackName),
    host: rawHost || '-',
    environment: rawEnv || '-',
    group: rawGroup || '-',
    status: meta?.status || '',
    missing,
  }
}

function ResolvePreviewPanel({ result }: { result: any }) {
  if (!result) return null
  return (
    <div style={{ marginTop: '12px', background: 'var(--bg-page)', borderRadius: '8px', padding: '12px' }}>
      <div style={{ display: 'flex', gap: '16px', marginBottom: '8px', fontSize: '13px' }}>
        <span style={{ color: result.ready ? 'var(--success)' : 'var(--danger)', fontWeight: 'bold' }}>
          {result.ready ? '✅ 所有变量已解析' : '❌ 存在未解析变量'}
        </span>
        <span style={{ color: 'var(--text-muted)' }}>
          {result.trace?.length || 0} 条追踪记录
        </span>
      </div>
      {result.errors && result.errors.length > 0 && (
        <div style={{ marginBottom: '8px' }}>
          {result.errors.map((e: any, ei: number) => (
            <div key={ei} style={{ color: 'var(--danger)', fontSize: '12px', marginBottom: '2px' }}>
              ⚠ {e.message}
            </div>
          ))}
        </div>
      )}
      {result.trace && result.trace.length > 0 && (
        <div style={{ maxHeight: '200px', overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--bg-surface)', textAlign: 'left' }}>
                <th style={{ padding: '4px', color: 'var(--text-muted)' }}>步骤</th>
                <th style={{ padding: '4px', color: 'var(--text-muted)' }}>字段</th>
                <th style={{ padding: '4px', color: 'var(--text-muted)' }}>值</th>
                <th style={{ padding: '4px', color: 'var(--text-muted)' }}>来源</th>
              </tr>
            </thead>
            <tbody>
              {result.trace.map((t: any, ti: number) => (
                <tr key={ti} style={{ borderBottom: '1px solid var(--bg-page)' }}>
                  <td style={{ padding: '4px', color: 'var(--text-secondary)' }}>{t.step_name}</td>
                  <td style={{ padding: '4px', color: 'var(--brand-soft)', fontFamily: 'monospace' }}>{t.field}</td>
                  <td style={{ padding: '4px', color: 'var(--text-primary)', fontFamily: 'monospace', fontSize: '11px' }}>
                    {String(t.value).substring(0, 30)}{String(t.value).length > 30 ? '…' : ''}
                  </td>
                  <td style={{ padding: '4px' }}>
                    <span style={{
                      padding: '1px 6px', borderRadius: '4px', fontSize: '10px',
                      background: t.source_type === 'literal' ? 'var(--bg-surface)' : 'var(--purple-surface)',
                      color: t.source_type === 'literal' ? 'var(--text-muted)' : 'var(--purple-text)',
                    }}>
                      {t.source_type}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default function DeployForm(props: DeployFormProps) {
  const deployDisabledReasons = [
    props.loading ? '发布任务执行中' : '',
    props.confirmingRelease ? '正在准备发布确认' : '',
    props.resolving ? '变量解析中' : '',
    props.prechecking ? '发布前预检中' : '',
    !props.system ? '请选择系统' : '',
    !props.service ? '请选择服务' : '',
    !props.environment ? '请选择环境' : '',
    !props.fileName ? '请选择或输入版本/文件名' : '',
    props.selectedServerNames.length === 0 ? '请选择至少一台发布服务器' : '',
    props.environmentServerErrorText ? props.environmentServerErrorText : '',
    props.precheckResult?.status === 'blocked' || props.precheckResult?.can_continue === false ? '发布前预检存在阻断项' : '',
  ].filter(Boolean)
  const deployButtonDisabled = deployDisabledReasons.length > 0

  return (
    <div className="card">
      <h2 style={{ marginBottom: '16px' }}>发布</h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px' }}>
        <div>
          <label style={{ display: 'block', marginBottom: '6px', fontSize: '14px', color: 'var(--text-secondary)' }}>系统</label>
          <select value={props.system} onChange={(e) => { props.setSystem(e.target.value); props.setService(''); props.setEnvironment(''); props.setPipelineId('') }} style={props.selectStyle}>
            <option value="">-- 选择系统 --</option>
            {props.systems.map((s) => (
              <option key={s.name} value={s.name}>{s.display_name} ({s.name})</option>
            ))}
          </select>
        </div>
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
            <label style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>服务</label>
            {props.system && props.service && (
              <button
                type="button"
                className="btn"
                onClick={() => props.navigate(`/systems/${encodeURIComponent(props.system)}/services/${encodeURIComponent(props.service)}/edit`)}
                style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}
              >
                查看/编辑服务
              </button>
            )}
          </div>
          <select value={props.service} onChange={(e) => props.applyServiceDefaults(e.target.value, { forceAuto: props.serverAutoMode })} style={props.selectStyle}>
            <option value="">-- 选择服务 --</option>
            {props.services.map((s) => (
              <option key={s.id} value={s.name}>{s.display_name || s.name}</option>
            ))}
          </select>
          <div style={{ marginTop: '4px', fontSize: '12px', color: 'var(--text-muted)' }}>
            当前系统共 {props.services.length} 个服务
            {props.selectedService?.template ? ` · 流程: ${props.selectedService.template}` : ''}
            {props.resolveServerNamesForTarget(props.selectedService).length ? ` · 推荐目标: ${props.resolveServerNamesForTarget(props.selectedService).join(', ')}` : props.selectedService?.servers?.length ? ` · 默认服务器: ${props.selectedService.servers.join(', ')}` : ''}
          </div>
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: '6px', fontSize: '14px', color: 'var(--text-secondary)' }}>环境</label>
          <select value={props.environment} onChange={(e) => {
            const val = e.target.value
            props.setEnvironment(val)
            props.autoFillServers({ envName: val, force: props.serverAutoMode })
          }} style={props.selectStyle}>
            <option value="">-- 选择环境 --</option>
            {props.environments.map((e) => (
              <option key={e.name} value={e.name}>
                {e.display_name || e.name}{e.display_name && e.display_name !== e.name ? ` (${e.name})` : ''}{e.category ? ` · ${e.category}` : ''}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: '6px', fontSize: '14px', color: 'var(--text-secondary)' }}>Pipeline</label>
          <select value={props.pipelineId} onChange={(e) => props.setPipelineId(e.target.value)} style={props.selectStyle}>
            <option value="">-- 默认流程 --</option>
            {props.pipelines.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: '6px', fontSize: '14px', color: 'var(--text-secondary)' }}>版本/文件名</label>
          <input
            list="deploy-packages-list"
            value={props.fileName}
            onChange={(e) => props.setFileName(e.target.value)}
            placeholder={props.deployPackages.length > 0 ? '选择或输入包名...' : '输入包名...'}
            style={{ width: '100%' }}
          />
          <datalist id="deploy-packages-list">
            {props.deployPackages.map((p) => (
              <option key={p.name} value={p.name}>{p.name} ({p.size_mb} MB)</option>
            ))}
          </datalist>
          {props.system && props.deployPackages.length === 0 && (
            <div style={{ marginTop: '4px', fontSize: '12px', color: 'var(--warning)' }}>
              暂无匹配的发布包，可手动输入包名或先上传发布包
            </div>
          )}
          {props.system && props.deployPackages.length > 0 && (
            <div style={{ marginTop: '4px', fontSize: '12px', color: 'var(--text-muted)' }}>
              共 {props.deployPackages.length} 个可用发布包
            </div>
          )}
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: '6px', fontSize: '14px', color: 'var(--text-secondary)' }}>服务器组</label>
          <select value={props.serverGroup} onChange={(e) => {
            const val = e.target.value
            props.setServerGroup(val)
            props.autoFillServers({ groupKey: val, force: true })
          }} style={props.selectStyle}>
            <option value="">-- 手动指定 --</option>
            {props.serverGroupList.map((g) => (
              <option key={g.id} value={g.name}>{g.display_name || g.name} ({g.server_names?.length || 0}台)</option>
            ))}
          </select>
          {props.system && props.serverGroupList.length === 0 && (
            <div style={{ marginTop: '4px', fontSize: '12px', color: 'var(--text-muted)' }}>
              该系统未配置服务器组；可直接在下方"发布服务器"按服务/环境自动带出，或手动输入。
            </div>
          )}
        </div>
        <div style={{ gridColumn: '1 / -1' }}>
          <label style={{ display: 'block', marginBottom: '6px', fontSize: '14px', color: 'var(--text-secondary)' }}>发布服务器</label>
          <textarea
            value={props.servers}
            onChange={(e) => { props.setServers(e.target.value); props.setServerAutoMode(false) }}
            placeholder="选择服务器组后自动带出服务器列表；也可手动输入 prod-1, prod-2"
            rows={2}
            style={{ width: '100%', resize: 'vertical', fontFamily: 'monospace' }}
          />
          <div style={{ marginTop: '6px', display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '12px', color: props.serverAutoMode ? 'var(--success)' : 'var(--warning)' }}>
              {props.serverAutoMode ? '按环境+服务推荐勾选' : '手动编辑服务器'}
            </span>
            <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              已选 {props.selectedServerNames.length} 台{props.candidateServerNames.length ? ` / 可选 ${props.candidateServerNames.length} 台` : ''}
            </span>
            <button type="button" className="btn" onClick={props.selectRecommendedServers}
              style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
              按规则推荐
            </button>
            {props.candidateServerNames.length > 0 && (
              <>
                <button type="button" className="btn" onClick={props.selectAllCandidateServers}
                  style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
                  全选当前列表
                </button>
                <button type="button" className="btn" onClick={props.invertCandidateServers}
                  style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
                  反选当前列表
                </button>
                <button type="button" className="btn" onClick={props.clearSelectedServers}
                  style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--danger-surface)', color: 'var(--danger)' }}>
                  清空
                </button>
              </>
            )}
          </div>
          {props.serverGroup && props.candidateServerNames.length === 0 && (
            <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--warning)' }}>
              当前服务器组没有可用服务器，请到“服务器”页面维护分组，或在上方手动输入服务器名。
            </div>
          )}
          {props.candidateServerNames.length > 0 && (
            <div style={{ marginTop: '10px', border: '1px solid var(--border-strong)', borderRadius: '8px', background: 'var(--bg-page)', padding: '10px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'center', marginBottom: '8px', flexWrap: 'wrap' }}>
                <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                  服务器列表{props.serverGroup ? `：${props.serverGroup}` : ''}，可勾选本次要发布的目标机器
                </div>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                  “推荐”由当前环境和服务自动匹配，仅作为默认选择
                </div>
              </div>
              <div style={{ display: 'grid', gap: '6px', maxHeight: '260px', overflow: 'auto', paddingRight: '4px' }}>
                <div style={{ display: 'grid', gridTemplateColumns: '28px minmax(160px, 1.2fr) minmax(120px, .9fr) minmax(80px, .6fr) auto', gap: '8px', padding: '0 10px 4px', fontSize: '12px', color: 'var(--text-muted)' }}>
                  <span />
                  <span>名称</span>
                  <span>主机/IP</span>
                  <span>环境/组</span>
                  <span>标记</span>
                </div>
                {props.candidateServerNames.map((name) => {
                  const meta = compactServerMeta(props.serverMetaByName.get(name), name)
                  const checked = props.selectedServerSet.has(name)
                  const recommended = props.recommendedServerSet.has(name)
                  const testLike = props.serverLooksLikeTest(name)
                  const envConflict = checked && props.selectedServerEnvironmentConflictSet.has(name)
                  const label = meta.displayName
                  return (
                    <label key={name || label} style={{
                      display: 'grid',
                      gridTemplateColumns: '28px minmax(160px, 1.2fr) minmax(120px, .9fr) minmax(80px, .6fr) auto',
                      alignItems: 'center',
                      gap: '8px',
                      padding: '8px 10px',
                      borderRadius: '8px',
                      cursor: 'pointer',
                      border: envConflict ? '1px solid var(--danger)' : checked ? '1px solid var(--brand)' : '1px solid var(--border-strong)',
                      background: envConflict ? 'var(--danger-surface)' : checked ? 'var(--brand-surface)' : 'var(--bg-surface)',
                    }}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => props.toggleServerSelection(name, e.target.checked)}
                        aria-label={`选择服务器 ${label}`}
                      />
                      <span style={{ minWidth: 0 }}>
                        <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: 'monospace', fontSize: '12px', fontWeight: 700, color: envConflict ? 'var(--danger)' : 'var(--text-primary)' }} title={label}>{label}</span>
                        {label !== name && <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: 'monospace', fontSize: '11px', color: 'var(--text-muted)' }}>{name}</span>}
                      </span>
                      <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: 'monospace', fontSize: '12px', color: 'var(--text-secondary)' }} title={meta.host}>{meta.host}</span>
                      <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '12px', color: 'var(--text-muted)' }} title={`${meta.environment} / ${meta.group}`}>{meta.environment !== '-' ? meta.environment : meta.group}</span>
                      <span style={{ display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                        {recommended && <span style={{ fontSize: '11px', padding: '1px 6px', borderRadius: '999px', background: 'var(--success-surface)', color: 'var(--success)' }}>推荐</span>}
                        {envConflict && <span style={{ fontSize: '11px', padding: '1px 6px', borderRadius: '999px', background: 'var(--danger)', color: 'white' }}>环境冲突</span>}
                        {meta.status && <StatusBadge value={meta.status} />}
                        {meta.missing.length > 0 && <span title={`缺少 ${meta.missing.join('、')}`} style={{ fontSize: '11px', padding: '1px 6px', borderRadius: '999px', background: 'var(--warning-surface)', color: 'var(--warning)' }}>配置不完整</span>}
                        <span style={{ fontSize: '11px', padding: '1px 6px', borderRadius: '999px', background: testLike ? 'var(--warning-surface)' : 'var(--bg-page)', color: testLike ? 'var(--warning)' : 'var(--text-muted)' }}>{testLike ? '测试' : '线上'}</span>
                      </span>
                    </label>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      </div>

      {props.selectedServerNames.length > 1 && (
        <div style={{ marginTop: '16px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
          <div>
            <label style={{ display: 'block', marginBottom: '6px', fontSize: '14px', color: 'var(--text-secondary)' }}>并发度</label>
            <select value={props.parallelism} onChange={(e) => props.setParallelism(Number(e.target.value))} style={props.selectStyle}>
              {[1, 2, 4, 8, 16].map((v) => (
                <option key={v} value={v}>{v}{v > props.selectedServerNames.length ? ` (超过服务器数，自动调整为 ${props.selectedServerNames.length})` : ''}</option>
              ))}
            </select>
            <div style={{ marginTop: '4px', fontSize: '12px', color: 'var(--text-muted)' }}>
              同时发布的服务器数量，默认 1（串行）
            </div>
          </div>
          <div>
            <label style={{ display: 'block', marginBottom: '6px', fontSize: '14px', color: 'var(--text-secondary)' }}>失败策略</label>
            <select value={props.failFast ? 'fail_fast' : 'continue'} onChange={(e) => props.setFailFast(e.target.value === 'fail_fast')} style={props.selectStyle}>
              <option value="fail_fast">快速失败 — 任一台失败立即中止</option>
              <option value="continue">继续执行 — 跑完所有服务器后汇总</option>
            </select>
            <div style={{ marginTop: '4px', fontSize: '12px', color: 'var(--text-muted)' }}>
              {props.failFast ? '失败时立即停止剩余服务器' : '失败时继续执行，最终状态为 partial_failed'}
            </div>
          </div>
        </div>
      )}

      {props.parallelism > 1 && props.isProdEnvironment(props.environment) && (
        <div style={{ marginTop: '10px', background: 'var(--danger-surface)', border: '1px solid var(--danger-border)', borderRadius: '8px', padding: '10px 12px', color: 'var(--danger)', fontSize: '13px' }}>
          <strong>⚠ 并发发布生产环境</strong> — 已自动升级为高风险确认，需要输入 "CONFIRM PARALLEL DEPLOY" 确认短语
        </div>
      )}

      {(props.service || props.environment || props.serverGroup || props.servers) && (
        <div style={{ marginTop: '16px', background: 'var(--bg-page)', borderRadius: '8px', padding: '12px', fontSize: '13px', color: 'var(--text-secondary)' }}>
          <strong style={{ color: 'var(--text-primary)' }}>发布目标:</strong>
          {' '}环境 <span style={{ color: props.isProdEnvironment(props.environment) ? 'var(--danger-solid)' : 'var(--brand)' }}>{props.environment || '-'}</span>
          {' '}· 服务 <span style={{ color: 'var(--brand)' }}>{props.selectedService?.display_name || props.service || '-'}</span>
          {' '}· 服务器组 <span style={{ color: 'var(--brand)' }}>{props.serverGroup || '手动/服务默认'}</span>
          {' '}· 服务器 <span style={{ color: props.environmentServerErrorText ? 'var(--danger)' : 'var(--success)', fontFamily: 'monospace' }}>{props.servers || '-'}</span>
        </div>
      )}

      {props.environmentServerErrorText && (
        <div style={{ marginTop: '10px', background: 'var(--danger-surface)', border: '1px solid var(--danger-border)', borderRadius: '8px', padding: '10px 12px', color: 'var(--danger)', fontSize: '13px' }}>
          <strong>环境与服务器不一致，已阻断发布操作：</strong>{props.environmentServerErrorText}
        </div>
      )}

      {props.selectedService && (
        <div style={{ marginTop: '12px', background: 'var(--bg-page)', borderRadius: '8px', padding: '12px', fontSize: '13px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'center', marginBottom: '8px' }}>
            <strong style={{ color: 'var(--text-primary)' }}>当前服务配置</strong>
            {props.system && props.service && (
              <button type="button" className="btn" onClick={() => props.navigate(`/systems/${encodeURIComponent(props.system)}/services/${encodeURIComponent(props.service)}/edit`)}
                style={{ padding: '2px 8px', fontSize: '12px', background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
                编辑服务配置
              </button>
            )}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px', color: 'var(--text-secondary)' }}>
            <div>服务名：<span style={{ color: 'var(--brand)', fontFamily: 'monospace' }}>{props.selectedService.name}</span></div>
            <div>模板：<span style={{ color: 'var(--text-primary)', fontFamily: 'monospace' }}>{props.selectedService.template || '-'}</span></div>
            <div>目录：<span style={{ color: 'var(--text-primary)', fontFamily: 'monospace' }}>{props.selectedService.template_variables?.service_dir || props.selectedService.template_variables?.deploy_path || '-'}</span></div>
            <div>脚本：<span style={{ color: 'var(--text-primary)', fontFamily: 'monospace' }}>{props.selectedService.template_variables?.update_script || '-'}</span></div>
          </div>
        </div>
      )}

      {props.pipelineSteps.length > 0 && (
        <div style={{ marginTop: '16px', background: 'var(--bg-page)', borderRadius: '8px', padding: '12px' }}>
          <h3 style={{ marginBottom: '8px', color: 'var(--text-secondary)', fontSize: '14px' }}>Pipeline 步骤预览:</h3>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {props.pipelineSteps.map((s, i: number) => (
              <span key={i} style={{ background: 'var(--bg-surface)', padding: '4px 10px', borderRadius: '4px', fontSize: '13px', color: 'var(--brand)' }}>
                #{i + 1} {s.name || s.step_type} ({s.step_type || s.type})
              </span>
            ))}
          </div>
        </div>
      )}

      <div style={{ marginTop: '20px', display: 'flex', alignItems: 'center', gap: '12px' }}>
        <button className="btn" onClick={props.handlePrecheck} disabled={props.prechecking}
          style={{ background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
          {props.prechecking ? '预检中...' : '预检'}
        </button>
        <button className="btn" onClick={props.handleResolve} disabled={props.resolving}
          style={{ background: 'var(--action-bg)', color: 'var(--action-text)' }}>
          {props.resolving ? '解析中...' : '解析预览'}
        </button>
        <button
          className="btn btn-primary"
          onClick={props.handleDeploy}
          disabled={deployButtonDisabled}
          title={deployDisabledReasons.join('；') || '开始发布'}
        >
          {props.loading ? '发布中...' : props.confirmingRelease ? '确认中...' : '开始发布'}
        </button>
        {deployDisabledReasons.length > 0 && (
          <span style={{ fontSize: '12px', color: 'var(--text-muted)', maxWidth: '420px' }}>
            {deployDisabledReasons[0]}{deployDisabledReasons.length > 1 ? `，另有 ${deployDisabledReasons.length - 1} 项待处理` : ''}
          </span>
        )}
        {props.status && (
          <span style={{ fontSize: '14px', color: props.statusColor }}>
            状态: <StatusBadge value={props.status} />
          </span>
        )}
      </div>

      <ReleaseConfirmationPanel confirmation={props.releaseConfirmation} />
      <ResolvePreviewPanel result={props.resolveResult} />
      <PreflightPanel result={props.precheckResult} />
    </div>
  )
}
