import { create } from 'zustand'

const STORAGE_KEY = 'ops_ai_favorites'

export type FavoriteAction = {
  id: string
  label: string
  description?: string
  url: string
  icon?: string
  category?: string
  pinned: boolean
  createdAt: number
}

function loadFavorites(): FavoriteAction[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    return JSON.parse(raw)
  } catch {
    return []
  }
}

function saveFavorites(items: FavoriteAction[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items))
  } catch {
    // localStorage may be full or unavailable
  }
}

type FavoriteStore = {
  favorites: FavoriteAction[]
  addFavorite: (item: Omit<FavoriteAction, 'id' | 'createdAt'>) => void
  removeFavorite: (id: string) => void
  togglePin: (id: string) => void
  isFavorite: (url: string) => boolean
  getFavoritesByCategory: (category: string) => FavoriteAction[]
  pinnedFavorites: () => FavoriteAction[]
}

export const useFavoriteStore = create<FavoriteStore>((set, get) => ({
  favorites: loadFavorites(),

  addFavorite: (item) => {
    const existing = get().favorites.find((f) => f.url === item.url)
    if (existing) return
    const newItem: FavoriteAction = {
      ...item,
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
      createdAt: Date.now(),
    }
    const updated = [...get().favorites, newItem]
    set({ favorites: updated })
    saveFavorites(updated)
  },

  removeFavorite: (id) => {
    const updated = get().favorites.filter((f) => f.id !== id)
    set({ favorites: updated })
    saveFavorites(updated)
  },

  togglePin: (id) => {
    const updated = get().favorites.map((f) =>
      f.id === id ? { ...f, pinned: !f.pinned } : f
    )
    set({ favorites: updated })
    saveFavorites(updated)
  },

  isFavorite: (url) => {
    return get().favorites.some((f) => f.url === url)
  },

  getFavoritesByCategory: (category) => {
    return get().favorites.filter((f) => f.category === category)
  },

  pinnedFavorites: () => {
    return get().favorites.filter((f) => f.pinned)
  },
}))

export const DEFAULT_FAVORITES: Omit<FavoriteAction, 'id' | 'createdAt'>[] = [
  {
    label: '新增发布',
    description: '创建新的应用发布',
    url: '/deploy?tab=new',
    category: 'deploy',
    pinned: true,
  },
  {
    label: '发布历史',
    description: '查看发布日志和历史记录',
    url: '/deploy?tab=history',
    category: 'deploy',
    pinned: false,
  },
  {
    label: '服务器管理',
    description: '管理所有 SSH 服务器',
    url: '/servers',
    category: 'servers',
    pinned: true,
  },
  {
    label: '流程管理',
    description: '管理发布和运维流程',
    url: '/pipelines',
    category: 'pipelines',
    pinned: false,
  },
  {
    label: '数据库工作台',
    description: '执行数据库 DML 操作',
    url: '/database',
    category: 'database',
    pinned: true,
  },
  {
    label: '工具中心',
    description: '浏览和调试 MCP 工具',
    url: '/tools',
    category: 'tools',
    pinned: false,
  },
]