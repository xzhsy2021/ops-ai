import { create } from 'zustand'

interface AuthState {
  user: {
    id: string
    username: string
    role: string
    is_admin: boolean
    can_deploy: boolean
  } | null
  setUser: (user: AuthState['user']) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  setUser: (user) => set({ user }),
  logout: () => set({ user: null }),
}))

let _notifySeq = 0

interface NotificationState {
  messages: Array<{ id: string; text: string; type: 'success' | 'error' | 'info' }>
  addMessage: (text: string, type?: 'success' | 'error' | 'info') => void
  removeMessage: (id: string) => void
}

export const useNotificationStore = create<NotificationState>((set) => ({
  messages: [],
  addMessage: (text, type = 'info') => {
    const id = `${Date.now()}-${++_notifySeq}`
    set((state) => ({ messages: [...state.messages, { id, text, type }] }))
    setTimeout(() => {
      set((state) => ({ messages: state.messages.filter((m) => m.id !== id) }))
    }, 5000)
  },
  removeMessage: (id) =>
    set((state) => ({ messages: state.messages.filter((m) => m.id !== id) })),
}))

interface BackendState {
  online: boolean
  lastChecked: number | null
  setOnline: (online: boolean) => void
}

type CacheRecord<T> = {
  data: T
  ts: number
}

export const useBackendStore = create<BackendState>((set) => ({
  online: true,
  lastChecked: null,
  setOnline: (online) => set({ online, lastChecked: Date.now() }),
}))

const DB_NAME = 'ops_cache'
const DB_VERSION = 1
const STORE_NAME = 'cache'

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME)
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

export const localCache = {
  async get<T>(key: string): Promise<CacheRecord<T> | null> {
    try {
      const db = await openDB()
      return new Promise((resolve) => {
        const tx = db.transaction(STORE_NAME, 'readonly')
        const store = tx.objectStore(STORE_NAME)
        const req = store.get(key)
        req.onsuccess = () => {
          const record = req.result as CacheRecord<T> | undefined
          resolve(record || null)
        }
        req.onerror = () => resolve(null)
      })
    } catch {
      return null
    }
  },

  async set<T>(key: string, data: T): Promise<void> {
    try {
      const db = await openDB()
      const tx = db.transaction(STORE_NAME, 'readwrite')
      const store = tx.objectStore(STORE_NAME)
      store.put({ data, ts: Date.now() } satisfies CacheRecord<T>, key)
    } catch {
    }
  },

  async getWithFallback<T>(key: string, fetcher: () => Promise<T>, maxAge: number = 5 * 60 * 1000): Promise<T | null> {
    try {
      const cached = await localCache.get<T>(key)
      if (cached && Date.now() - cached.ts < maxAge) {
        return cached.data
      }
    } catch {
    }
    try {
      const data = await fetcher()
      await localCache.set(key, data)
      return data
    } catch {
      try {
        const cached = await localCache.get<T>(key)
        if (cached) return cached.data
      } catch {
      }
      return null
    }
  },
}
