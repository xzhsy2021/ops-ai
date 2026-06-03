import { X } from 'lucide-react'

type ShortcutItem = {
  keys: string
  description: string
  category: string
}

type ShortcutGroup = {
  title: string
  items: ShortcutItem[]
}

export function KeyboardShortcutsPanel({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  if (!open) return null

  const groups: ShortcutGroup[] = [
    {
      title: '导航',
      items: [
        { keys: 'Ctrl + K', description: '打开命令面板', category: '导航' },
        { keys: 'Ctrl + D', description: '返回仪表盘', category: '导航' },
        { keys: 'Ctrl + P', description: '切换性能模式', category: '导航' },
        { keys: 'Esc', description: '关闭弹窗/抽屉', category: '导航' },
      ],
    },
    {
      title: '操作',
      items: [
        { keys: 'Ctrl + Enter', description: '提交当前表单', category: '操作' },
        { keys: 'Ctrl + S', description: '保存', category: '操作' },
        { keys: 'Ctrl + R', description: '刷新当前页面', category: '操作' },
        { keys: 'Ctrl + Z', description: '撤销', category: '操作' },
      ],
    },
    {
      title: '其他',
      items: [
        { keys: '?', description: '显示/隐藏快捷键面板', category: '其他' },
        { keys: 'Ctrl + Shift + L', description: '切换主题', category: '其他' },
      ],
    },
  ]

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="drawer-panel" style={{ width: 480 }} onClick={(e) => e.stopPropagation()}>
        <div className="drawer-header">
          <strong>键盘快捷键</strong>
          <button className="drawer-close-btn" onClick={onClose} aria-label="关闭">
            <X size={18} />
          </button>
        </div>
        <div className="drawer-body" style={{ display: 'grid', gap: 20 }}>
          {groups.map((group) => (
            <div key={group.title}>
              <h3 style={{ color: 'var(--text-primary)', fontSize: 14, fontWeight: 700, margin: '0 0 8px' }}>
                {group.title}
              </h3>
              <div style={{ display: 'grid', gap: 6 }}>
                {group.items.map((item) => (
                  <div
                    key={item.keys}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '6px 10px',
                      borderRadius: 8,
                      background: 'var(--bg-page)',
                    }}
                  >
                    <span style={{ color: 'var(--text-primary)', fontSize: 13 }}>{item.description}</span>
                    <kbd
                      style={{
                        padding: '3px 8px',
                        borderRadius: 6,
                        background: 'var(--bg-surface-2)',
                        border: '1px solid var(--border)',
                        fontSize: 12,
                        fontWeight: 700,
                        color: 'var(--text-secondary)',
                        fontFamily: "'Consolas', 'Monaco', ui-monospace, monospace",
                      }}
                    >
                      {item.keys}
                    </kbd>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}