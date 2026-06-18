export type ServerProfileStripProps = {
  profiles: any[]
  previewData: any
  running: boolean
  onPreviewIssueRetry: () => void
  onPreviewInspectionProfile: (profileId: string) => void
}

export function ServerProfileStrip({
  profiles,
  previewData,
  running,
  onPreviewIssueRetry,
  onPreviewInspectionProfile,
}: ServerProfileStripProps) {
  if (profiles.length === 0) return null

  return (
            <div className="cc-profile-strip">
              <div className="cc-profile-strip-head">
                <div>
                  <strong>常用巡检方案</strong>
                  <span className="muted">先预览目标和确认短语，再执行批量巡检。</span>
                </div>
                <button className="cc-icon-btn" type="button" disabled={running} onClick={() => onPreviewIssueRetry()}>复巡未关闭风险</button>
              </div>
              <div className="cc-profile-grid">
                {profiles.map((profile: any) => (
                  <button
                    key={profile.id}
                    type="button"
                    className="cc-profile-card"
                    disabled={running || profile.enabled === false}
                    onClick={() => onPreviewInspectionProfile(profile.id)}
                  >
                    <span className="cc-profile-title">{profile.name || profile.id}</span>
                    <span className="cc-profile-desc">{profile.description || profile.id}</span>
                    <span className="cc-profile-meta">
                      {(profile.categories || []).length} 项 · 并发 {profile.concurrency || '-'} · 批量 {profile.batch_size || '-'}
                    </span>
                  </button>
                ))}
              </div>
              {previewData && (
                <div className="cc-modal-callout">
                  <strong>{previewData.profile?.name || previewData.profile_id}</strong>
                  <div style={{ marginTop: 4, fontSize: 11 }}>目标 {previewData.eligible_count || 0} 台，跳过 {previewData.skipped_count || 0} 台，过滤 {previewData.filtered_count || 0} 台。</div>
                  <div style={{ marginTop: 4, fontSize: 10.5, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>一键确认已就绪，无需手动输入字符串。</div>
                </div>
              )}
            </div>
  )
}
