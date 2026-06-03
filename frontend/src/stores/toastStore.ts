import { create } from 'zustand'

export type ToastItem = {
  id: string
  type: 'success' | 'error' | 'warning' | 'info'
  title: string
  message?: string
  duration?: number
}

type ToastStore = {
  toasts: ToastItem[]
  addToast: (toast: Omit<ToastItem, 'id'>) => void
  removeToast: (id: string) => void
}

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  addToast: (toast) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
    set((s) => ({ toasts: [...s.toasts, { ...toast, id }] }))
    const duration = toast.duration ?? 4000
    if (duration > 0) {
      setTimeout(() => {
        set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
      }, duration)
    }
  },
  removeToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))

export function toastSuccess(title: string, message?: string, duration?: number) {
  useToastStore.getState().addToast({ type: 'success', title, message, duration })
}

export function toastError(title: string, message?: string, duration?: number) {
  useToastStore.getState().addToast({ type: 'error', title, message, duration })
}

export function toastWarning(title: string, message?: string, duration?: number) {
  useToastStore.getState().addToast({ type: 'warning', title, message, duration })
}

export function toastInfo(title: string, message?: string, duration?: number) {
  useToastStore.getState().addToast({ type: 'info', title, message, duration })
}