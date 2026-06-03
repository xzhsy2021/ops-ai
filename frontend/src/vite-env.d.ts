/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare global {
  interface Window {
    __OPS_FRONTEND_READY__?: boolean
    __OPS_FRONTEND_BUILD__?: Record<string, unknown>
  }
}

export {}
