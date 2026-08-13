import type { CSSProperties } from 'react'
import { StatusBadge } from '../../components/ui'

interface DeployServerStepProps {
  serverGroupList: any[]
  serverGroup: string
  servers: string
  serverAutoMode: boolean
  selectedServerNames: string[]
  candidateServerNames: string[]
  selectedServerSet: Set<string>
  recommendedServerSet: Set<string>
  selectedServerEnvironmentConflictSet: Set<string>
  serverMetaByName: Map<string, any>
  environmentServerErrorText: string
  system: string
  parallelism: number
  failFast: boolean
  selectStyle: CSSProperties
  setServerGroup: (value: string) => void
  setServers: (value: string) => void
  setServerAutoMode: (value: boolean) => void
  setParallelism: (value: number) => void
  setFailFast: (value: boolean) => void
  autoFillServers: (options?: any) => void
  selectRecommendedServers: () => void
  selectAllCandidateServers: () => void
  clearSelectedServers: () => void
  invertCandidateServers: () => void
  toggleServerSelection: (name: string, checked: boolean) => void
  serverLooksLikeTest: (name: string) => boolean
  isProdEnvironment: (value: any) => boolean
}

function compactServerMeta(meta: any, fallbackName: string) {
  const rawHost = meta?.host || meta?.ip || ''
  const rawEnv = meta?.environment || ''
  const rawGroup = meta?.group || ''
  const missing: string[] = []
  if (!rawHost) missing.push('主机/IP')
  if (!rawEnv && !rawGroup) missing.push('环境/组')
  return {
    displayName: meta?.displayName || String(meta?.name || fallbackName || '').trim() || '未命名服务器',
    host: rawHost || '-',
    environment: rawEnv || '-',
    group: rawGroup || '-',
    status: meta?.status || '',
    missing,
  }
}

export default function DeployServerStep(props: DeployServerStepProps) {
  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 16 }}>
        <div>
          <label style={{ display: 'block', marginBottom: 6, fontSize: 14, color: 'var(--text-secondary)' }}>服务器组</label>
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
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: 6, fontSize: 14, color: 'var(--text-secondary)' }}>发布服务器</label>
          <textarea
            value={props.servers}
            onChange={(e) => { props.setServers(e.target.value); props.setServerAutoMode(false) }}
            placeholder="选择服务器组后自动带出服务器列表；也可手动输入"
            rows={2}
            style={{ width: '100%', resize: 'vertical', fontFamily: 'monospace' }}
          />
          <div style={{ marginTop: 6, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: props.serverAutoMode ? 'var(--success)' : 'var(--warning)' }}>
              {props.serverAutoMode ? '按环境+服务推荐勾选' : '手动编辑服务器'}
            </span>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              已选 {props.selectedServerNames.length} 台
            </span>
            <button type="button" className="btn" onClick={props.selectRecommendedServers}
              style={{ padding: '2px 8px', fontSize: 12, background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
              按规则推荐
            </button>
            {props.candidateServerNames.length > 0 && (
              <>
                <button type="button" className="btn" onClick={props.selectAllCandidateServers}
                  style={{ padding: '2px 8px', fontSize: 12, background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
                  全选
                </button>
                <button type="button" className="btn" onClick={props.invertCandidateServers}
                  style={{ padding: '2px 8px', fontSize: 12, background: 'var(--border-strong)', color: 'var(--text-primary)' }}>
                  反选
                </button>
                <button type="button" className="btn" onClick={props.clearSelectedServers}
                  style={{ padding: '2px 8px', fontSize: 12, background: 'var(--danger-surface)', color: 'var(--danger)' }}>
                  清空
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {props.candidateServerNames.length > 0 && (
        <div style={{ border: '1px solid var(--border-strong)', borderRadius: 8, background: 'var(--bg-page)', padding: 10 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>服务器列表，勾选发布目标</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>推荐由环境和服务自动匹配</div>
          </div>
          <div style={{ display: 'grid', gap: 6, maxHeight: 260, overflow: 'auto', paddingRight: 4 }}>
            {props.candidateServerNames.map((name) => {
              const meta = compactServerMeta(props.serverMetaByName.get(name), name)
              const checked = props.selectedServerSet.has(name)
              const recommended = props.recommendedServerSet.has(name)
              const testLike = props.serverLooksLikeTest(name)
              const envConflict = checked && props.selectedServerEnvironmentConflictSet.has(name)
              return (
                <label key={name} style={{
                  display: 'grid',
                  gridTemplateColumns: '28px minmax(160px, 1.2fr) minmax(120px, .9fr) minmax(80px, .6fr) auto',
                  alignItems: 'center',
                  gap: 8,
                  padding: '8px 10px',
                  borderRadius: 8,
                  cursor: 'pointer',
                  border: envConflict ? '1px solid var(--danger)' : checked ? '1px solid var(--brand)' : '1px solid var(--border-strong)',
                  background: envConflict ? 'var(--danger-surface)' : checked ? 'var(--brand-surface)' : 'var(--bg-surface)',
                }}>
                  <input type="checkbox" checked={checked} onChange={(e) => props.toggleServerSelection(name, e.target.checked)} />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: 'monospace', fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>{meta.displayName}</span>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 12, color: 'var(--text-secondary)' }}>{meta.host}</span>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 12, color: 'var(--text-muted)' }}>{meta.environment !== '-' ? meta.environment : meta.group}</span>
                  <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                    {recommended && <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 999, background: 'var(--success-surface)', color: 'var(--success)' }}>推荐</span>}
                    {envConflict && <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 999, background: 'var(--danger)', color: 'white' }}>环境冲突</span>}
                    {meta.status && <StatusBadge value={meta.status} />}
                    <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 999, background: testLike ? 'var(--warning-surface)' : 'var(--bg-page)', color: testLike ? 'var(--warning)' : 'var(--text-muted)' }}>{testLike ? '测试' : '线上'}</span>
                  </span>
                </label>
              )
            })}
          </div>
        </div>
      )}

      {props.selectedServerNames.length > 1 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 16 }}>
          <div>
            <label style={{ display: 'block', marginBottom: 6, fontSize: 14, color: 'var(--text-secondary)' }}>并发度</label>
            <select value={props.parallelism} onChange={(e) => props.setParallelism(Number(e.target.value))} style={props.selectStyle}>
              {[1, 2, 4, 8, 16].map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </div>
          <div>
            <label style={{ display: 'block', marginBottom: 6, fontSize: 14, color: 'var(--text-secondary)' }}>失败策略</label>
            <select value={props.failFast ? 'fail_fast' : 'continue'} onChange={(e) => props.setFailFast(e.target.value === 'fail_fast')} style={props.selectStyle}>
              <option value="fail_fast">快速失败 — 任一台失败立即中止</option>
              <option value="continue">继续执行 — 跑完所有服务器后汇总</option>
            </select>
          </div>
        </div>
      )}
    </div>
  )
}