import { create } from 'zustand'

const STORAGE_KEY = 'ops_ai_user_preferences'

export type ThemeMode = 'light' | 'dark' | 'system'
export type Language = 'zh' | 'en'
export type PerformanceMode = 'auto' | 'high' | 'low'

export type UserPreferences = {
  theme: ThemeMode
  language: Language
  performanceMode: PerformanceMode
  lowResourceMode: boolean
  autoRefreshInterval: number
  defaultDeploySystem: string
  defaultDeployService: string
  defaultDeployEnvironment: string
  collapsedSidebar: boolean
  recentSearches: string[]
}

const STORAGE_DEFAULTS: UserPreferences = {
  theme: 'system',
  language: 'zh',
  performanceMode: 'auto',
  lowResourceMode: false,
  autoRefreshInterval: 30,
  defaultDeploySystem: '',
  defaultDeployService: '',
  defaultDeployEnvironment: '',
  collapsedSidebar: false,
  recentSearches: [],
}

function loadPreferences(): UserPreferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...STORAGE_DEFAULTS }
    return { ...STORAGE_DEFAULTS, ...JSON.parse(raw) }
  } catch {
    return { ...STORAGE_DEFAULTS }
  }
}

function savePreferences(prefs: UserPreferences) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs))
  } catch {
    // localStorage may be full or unavailable
  }
}

const initialState = loadPreferences()

type PreferenceStore = UserPreferences & {
  setTheme: (theme: ThemeMode) => void
  setLanguage: (language: Language) => void
  setPerformanceMode: (mode: PerformanceMode) => void
  setLowResourceMode: (enabled: boolean) => void
  setAutoRefreshInterval: (seconds: number) => void
  setDefaultDeployTarget: (system: string, service: string, env: string) => void
  setCollapsedSidebar: (collapsed: boolean) => void
  addRecentSearch: (query: string) => void
  resetPreferences: () => void
}

export const usePreferenceStore = create<PreferenceStore>((set, get) => ({
  ...initialState,

  setTheme: (theme) => {
    set({ theme })
    savePreferences({ ...get(), theme })
  },

  setLanguage: (language) => {
    set({ language })
    savePreferences({ ...get(), language })
  },

  setPerformanceMode: (performanceMode) => {
    set({ performanceMode })
    savePreferences({ ...get(), performanceMode })
  },

  setLowResourceMode: (lowResourceMode) => {
    set({ lowResourceMode })
    savePreferences({ ...get(), lowResourceMode })
  },

  setAutoRefreshInterval: (autoRefreshInterval) => {
    set({ autoRefreshInterval })
    savePreferences({ ...get(), autoRefreshInterval })
  },

  setDefaultDeployTarget: (system, service, env) => {
    set({ defaultDeploySystem: system, defaultDeployService: service, defaultDeployEnvironment: env })
    savePreferences({ ...get(), defaultDeploySystem: system, defaultDeployService: service, defaultDeployEnvironment: env })
  },

  setCollapsedSidebar: (collapsedSidebar) => {
    set({ collapsedSidebar })
    savePreferences({ ...get(), collapsedSidebar })
  },

  addRecentSearch: (query) => {
    const trimmed = query.trim()
    if (!trimmed) return
    const recent = get().recentSearches.filter((q) => q !== trimmed)
    recent.unshift(trimmed)
    const limited = recent.slice(0, 10)
    set({ recentSearches: limited })
    savePreferences({ ...get(), recentSearches: limited })
  },

  resetPreferences: () => {
    set({ ...STORAGE_DEFAULTS })
    localStorage.removeItem(STORAGE_KEY)
  },
}))