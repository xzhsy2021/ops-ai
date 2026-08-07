import type { CSSProperties } from 'react'

export type FolderTone = 'green' | 'purple' | 'blue' | 'amber'

export type FolderItem = {
  id: string
  name: string
  meta?: string
  tone: FolderTone
  active?: boolean
  onClick?: () => void
}

const SLOT: Array<'is-back' | 'is-mid' | 'is-front'> = ['is-back', 'is-mid', 'is-front']

/** CSS-only 3D folder stack — video metaphor, no Three.js */
export function FolderStage({
  folders,
  caption,
}: {
  folders: FolderItem[]
  caption?: string
}) {
  const shown = folders.slice(0, 3)
  while (shown.length < 3) {
    shown.unshift({
      id: `pad-${shown.length}`,
      name: '—',
      meta: '',
      tone: 'blue',
    })
  }

  return (
    <div className="wx-stage" role="img" aria-label={caption || '任务分组舞台'}>
      <div className="wx-stage-grid" />
      <div className="wx-folder-stack">
        {shown.map((f, i) => (
          <button
            key={f.id}
            type="button"
            className={`wx-folder tone-${f.tone} ${SLOT[i]} ${f.active ? 'active' : ''}`}
            onClick={f.onClick}
            style={{ '--i': i } as CSSProperties}
            title={f.name}
          >
            <span className="name">{f.name}</span>
            {f.meta ? <span className="meta">{f.meta}</span> : null}
          </button>
        ))}
      </div>
    </div>
  )
}

export function toneFromStatus(status?: string): FolderTone {
  const s = String(status || '').toLowerCase()
  if (['running', 'executing'].includes(s)) return 'green'
  if (['failed', 'error'].includes(s)) return 'purple'
  if (['queued', 'pending', 'pending_approval', 'draft', 'submitted'].includes(s)) return 'amber'
  return 'blue'
}

export default FolderStage
