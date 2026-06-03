import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'


try {
  const savedTheme = localStorage.getItem('ops-theme')
  document.documentElement.dataset.theme = savedTheme === 'light' ? 'light' : 'dark'
} catch {
  document.documentElement.dataset.theme = 'dark'
}

if (import.meta.env.DEV) {
  const SES_PATTERNS = ['SES', 'lockdown', 'unpermitted', 'intrinsic', 'compartment']
  function isSesError(msg: any): boolean {
    const s = String(msg || '')
    return SES_PATTERNS.some(p => s.includes(p))
  }
  window.addEventListener('error', (event) => {
    if (isSesError(event.message) || isSesError(event.filename)) {
      event.preventDefault()
      event.stopPropagation()
      return false
    }
  }, true)
  const origOnError = window.onerror
  window.onerror = function (msg, source, lineno, colno, error) {
    if (isSesError(msg) || isSesError(source)) return true
    if (origOnError) return origOnError.call(window, msg, source, lineno, colno, error)
    return false
  }
  window.addEventListener('unhandledrejection', (event) => {
    if (isSesError(event.reason)) {
      event.preventDefault()
      return false
    }
  })
  const origConsoleError = console.error
  console.error = function (...args: any[]) {
    if (args.length > 0 && isSesError(args[0])) return
    origConsoleError.apply(console, args)
  }
  const origConsoleWarn = console.warn
  console.warn = function (...args: any[]) {
    if (args.length > 0 && isSesError(args[0])) return
    origConsoleWarn.apply(console, args)
  }
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
