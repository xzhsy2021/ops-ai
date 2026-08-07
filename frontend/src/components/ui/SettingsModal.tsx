import type { ReactNode } from 'react'

export type ThemeMode = 'crystal' | 'jade' | 'light'

const THEMES: Array<{ id: ThemeMode; name: string; desc: string; icon: string }> = [
  { id: 'crystal', name: '水晶文件', desc: '深空黑 + 荧光绿', icon: '🌑' },
  { id: 'jade', name: '翡翠苔藓', desc: '墨绿玻璃层次', icon: '🌿' },
  { id: 'light', name: '浅色办公', desc: '明亮工作台', icon: '☀️' },
]

/** Video-like settings panel: theme picker + FX note */
export function SettingsModal({
  open,
  theme,
  onTheme,
  onClose,
  extra,
}: {
  open: boolean
  theme: ThemeMode
  onTheme: (t: ThemeMode) => void
  onClose: () => void
  extra?: ReactNode
}) {
  if (!open) return null
  return (
    <div
      className="wx-settings-overlay"
      role="presentation"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="wx-settings-modal" role="dialog" aria-modal="true" aria-label="显示设置">
        <header className="wx-settings-head">
          <div>
            <span className="wx-settings-eyebrow">DISPLAY</span>
            <h2>背景主题</h2>
          </div>
          <button type="button" className="btn small" onClick={onClose}>关闭</button>
        </header>
        <p className="wx-settings-desc">切换工作台皮肤。物理发光与玻璃层次随主题变化；不引入额外 3D 引擎。</p>
        <div className="wx-settings-themes">
          {THEMES.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`wx-settings-theme ${theme === t.id ? 'active' : ''}`}
              onClick={() => onTheme(t.id)}
            >
              <span className="wx-settings-theme-icon">{t.icon}</span>
              <strong>{t.name}</strong>
              <span>{t.desc}</span>
            </button>
          ))}
        </div>
        {extra}
      </div>
    </div>
  )
}

export default SettingsModal
