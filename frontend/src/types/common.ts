export interface ApiEnvelope<T> {
  data?: T
  message?: string
  ok?: boolean
}

export interface Pagination {
  limit: number
  offset: number
  total: number
  has_more?: boolean
}

export type StatusTone = 'success' | 'warning' | 'danger' | 'neutral'
