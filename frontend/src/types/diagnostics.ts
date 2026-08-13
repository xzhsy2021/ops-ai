export interface DiagnosticCheck {
  status?: string
  message?: string
  path?: string
  items?: Record<string, DiagnosticCheck>
  [key: string]: any
}

export interface Recommendation {
  key: string
  severity: string
  title: string
  reason: string
  actions: string[]
  source?: string
}

export interface RecentErrorItem {
  time?: string
  level?: string
  source?: string
  path?: string
  line_no?: number
  module?: string
  summary?: string
  raw?: string
}

export interface RecentErrorsPayload {
  status?: string
  message?: string
  generated_at?: string
  limit?: number
  error_count?: number
  warning_count?: number
  scanned_files?: any[]
  items?: RecentErrorItem[]
}

export interface DiagnosticsPayload {
  status?: string
  generated_at?: string
  startup_mode?: string
  platform?: Record<string, any>
  python?: Record<string, any>
  summary?: Record<string, any>
  recommendations?: Recommendation[]
  sections?: Record<string, any>
}
