import { useEffect, useRef, useCallback, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { serverWorkbench, getApiBaseUrl } from '../../api'
import { ConfirmDialog, RiskConfirmDialog } from '../ui'
import '@xterm/xterm/css/xterm.css'

interface TerminalTabProps {
  name: string
  active: boolean
}

const DISCONNECTED = 0
const CONNECTING = 1
const CONNECTED = 2

interface PresetCommand {
  id: string
  label: string
  cmd: string
  confirm?: boolean
}

const STORAGE_KEY = 'ops_preset_commands'

const DEFAULT_PRESETS: PresetCommand[] = [
  { id: 'disk', label: '磁盘使用', cmd: 'df -h' },
  { id: 'mem', label: '内存', cmd: 'free -m || free -h' },
  { id: 'uptime', label: '运行时间', cmd: 'uptime' },
  { id: 'pm2list', label: 'PM2列表', cmd: 'pm2 list' },
  { id: 'pm2restart', label: '重启PM2', cmd: 'pm2 restart all', confirm: true },
  { id: 'ports', label: '端口监听', cmd: 'ss -tlnp 2>/dev/null || netstat -tlnp' },
  { id: 'osver', label: '系统版本', cmd: 'cat /etc/os-release 2>/dev/null | head -4 || uname -a' },
  { id: 'lastlogin', label: '最近登录', cmd: 'last -10' },
  { id: 'ls', label: '当前目录', cmd: 'ls -lah' },
  { id: 'pm2logs', label: '服务日志(50行)', cmd: 'pm2 logs --lines 50 --nostream 2>/dev/null || journalctl -u ops --no-pager -n 50' },
]

function loadPresets(): PresetCommand[] {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
      const parsed = JSON.parse(saved)
      if (Array.isArray(parsed) && parsed.length > 0) return parsed
    }
  } catch { }
  return DEFAULT_PRESETS
}

function savePresets(presets: PresetCommand[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(presets))
  } catch { }
}

const _BLOCKED_PATTERNS = [
  { re: /:\(\)\{\s*:\|:&\s*\};/i, label: 'fork bomb' },
  { re: /\brm\s+(-\w*f\w*\s+|--)\/\s/i, label: 'rm -rf /' },
  { re: /\bmkfs\b/i, label: 'mkfs' },
  { re: /\bdd\s+if=.*of=\/dev\//i, label: 'dd write to device' },
  { re: /\b(shutdown|reboot|poweroff|halt)\b/i, label: 'system shutdown/reboot' },
]

const _DANGEROUS_PATTERNS = [
  { re: /\brm\s+(-\w*r\w*\s+)/i, label: '递归删除' },
  { re: /\bkill\s+-9\s+1\b/i, label: 'kill init' },
  { re: /\biptables\s+-F\b/i, label: '清空防火墙规则' },
  { re: /\b(wget|curl)\s+.*\|\s*(ba)?sh\b/i, label: '管道远程内容到shell' },
  { re: /\bsystemctl\s+(stop|disable)\s+(ssh|sshd|nginx|apache2|httpd)\b/i, label: '停止关键服务' },
]

function validateCommand(cmd: string): string | null {
  if (!cmd.trim()) return '命令不能为空'
  if (cmd.length > 500) return '命令长度不能超过500字符'
  for (const p of _BLOCKED_PATTERNS) {
    if (p.re.test(cmd)) return `命令被拦截: ${p.label}`
  }
  return null
}

function checkDangerous(cmd: string): string | null {
  for (const p of _DANGEROUS_PATTERNS) {
    if (p.re.test(cmd)) return p.label
  }
  return null
}

let _presetSeq = 0

export default function TerminalTab({ name, active }: TerminalTabProps) {
  const [state, setState] = useState(DISCONNECTED)
  const [sessionId, setSessionId] = useState('')
  const [sessionCount, setSessionCount] = useState(0)
  const [idleWarning, setIdleWarning] = useState(false)
  const [copied, setCopied] = useState(false)
  const [quickRunning, setQuickRunning] = useState(false)
  const [errorMsg, setErrorMsg] = useState('')
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [presets, setPresets] = useState<PresetCommand[]>(loadPresets)
  const [editingPreset, setEditingPreset] = useState<PresetCommand | null>(null)
  const [showPresetEditor, setShowPresetEditor] = useState(false)
  const [presetFormLabel, setPresetFormLabel] = useState('')
  const [presetFormCmd, setPresetFormCmd] = useState('')
  const [presetFormConfirm, setPresetFormConfirm] = useState(false)
  const [presetFormError, setPresetFormError] = useState('')
  const [presetsCollapsed, setPresetsCollapsed] = useState(false)
  const [fontSize, setFontSize] = useState(14)
  const [pendingCommandRisk, setPendingCommandRisk] = useState<any | null>(null)
  const [commandRiskConfirmValue, setCommandRiskConfirmValue] = useState('')
  const [pendingConfirm, setPendingConfirm] = useState<any | null>(null)
  const fontSizeRef = useRef(14)

  const panelRef = useRef<HTMLDivElement>(null)
  const terminalRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitAddonRef = useRef<FitAddon | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const resizeObserverRef = useRef<ResizeObserver | null>(null)
  const idleTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const idleWarningRef = useRef(false)
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const stateRef = useRef(state)
  stateRef.current = state
  const lastResizeRef = useRef<{ cols: number; rows: number } | null>(null)
  const sessionIdRef = useRef('')
  const pendingLineRef = useRef('')
  const lastResizeTimeRef = useRef(0)

  const toggleFullscreen = useCallback(() => {
    if (!panelRef.current) return
    if (!document.fullscreenElement) {
      panelRef.current.requestFullscreen().catch(() => { })
    } else {
      document.exitFullscreen().catch(() => { })
    }
  }, [])

  useEffect(() => {
    const handleChange = () => setIsFullscreen(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', handleChange)
    return () => document.removeEventListener('fullscreenchange', handleChange)
  }, [])

  const resetIdleTimer = useCallback(() => {
    if (idleWarningRef.current) {
      setIdleWarning(false)
      idleWarningRef.current = false
    }
    if (idleTimerRef.current) clearInterval(idleTimerRef.current)
    idleTimerRef.current = setInterval(() => {
      setIdleWarning(true)
      idleWarningRef.current = true
    }, 5 * 60 * 1000)
  }, [])

  const fetchSessionCount = useCallback(async () => {
    try {
      const res: any = await serverWorkbench.terminalSessions(name)
      setSessionCount(res?.data?.sessions?.length || 0)
    } catch { }
  }, [name])

  function guardCommandBeforeSend(line: string): boolean {
    const command = line.trim()
    if (!command) return true
    const blocked = validateCommand(line)
    if (blocked) {
      termRef.current?.writeln(`\r\n\x1b[31m命令被拦截: ${blocked}\x1b[0m`)
      return false
    }
    const dangerLabel = checkDangerous(line)
    if (dangerLabel) {
      setCommandRiskConfirmValue('')
      setPendingCommandRisk({
        source: 'terminal',
        cmd: command,
        dangerLabel,
        title: '确认执行终端危险命令',
        description: '当前命令已输入到远端终端但尚未发送回车。确认后才会继续执行。',
        confirmText: `RUN ${command}`,
        confirmButtonLabel: '确认执行命令',
        riskLevel: dangerLabel.includes('删除') || dangerLabel.includes('关键服务') ? 'critical' : 'high',
      })
      termRef.current?.writeln(`\r\n\x1b[33m命令等待确认: ${dangerLabel}\x1b[0m`)
      return false
    }
    return true
  }

  function rememberTerminalInput(data: string) {
    if (data === '\u007f' || data === '\b') {
      pendingLineRef.current = pendingLineRef.current.slice(0, -1)
      return
    }
    if (data.includes('\x1b')) return
    if (data.includes('\r') || data.includes('\n')) {
      pendingLineRef.current = ''
      return
    }
    if (data.length === 1 && data >= ' ') {
      pendingLineRef.current += data
    }
  }

  const cleanup = useCallback(() => {
    if (idleTimerRef.current) { clearInterval(idleTimerRef.current); idleTimerRef.current = null }
    if (heartbeatRef.current) { clearInterval(heartbeatRef.current); heartbeatRef.current = null }
    if (resizeObserverRef.current) { resizeObserverRef.current.disconnect(); resizeObserverRef.current = null }
    if (wsRef.current) { try { wsRef.current.close() } catch { }; wsRef.current = null }
    if (termRef.current) { try { termRef.current.dispose() } catch { }; termRef.current = null }
    if (fitAddonRef.current) { fitAddonRef.current = null }
    setState(DISCONNECTED)
    setSessionId('')
    sessionIdRef.current = ''
    setSessionCount(0)
    setIdleWarning(false)
    setErrorMsg('')
    setFontSize(14)
    fontSizeRef.current = 14
  }, [])

  const connect = useCallback(() => {
    if (stateRef.current !== DISCONNECTED) return
    setState(CONNECTING)
    setErrorMsg('')

    if (wsRef.current) { try { wsRef.current.close() } catch { }; wsRef.current = null }
    if (termRef.current) { try { termRef.current.dispose() } catch { }; termRef.current = null }
    if (resizeObserverRef.current) { resizeObserverRef.current.disconnect(); resizeObserverRef.current = null }
    if (fitAddonRef.current) { fitAddonRef.current = null }
    lastResizeRef.current = null

    const term = new Terminal({
      cursorBlink: true,
      cursorStyle: 'bar',
      fontSize: fontSizeRef.current,
      fontFamily: 'Consolas, "Courier New", monospace',
      theme: {
        background: 'var(--bg-page)', foreground: 'var(--text-primary)', cursor: 'var(--success)',
        selectionBackground: 'var(--border-strong)',
        black: 'var(--bg-surface)', red: 'var(--danger)', green: 'var(--success)', yellow: 'var(--warning)',
        blue: 'var(--brand-soft)', magenta: 'var(--purple)', cyan: 'var(--cyan)', white: 'var(--text-primary)',
        brightBlack: 'var(--border-stronger)', brightRed: 'var(--danger-soft)', brightGreen: 'var(--success-soft)',
        brightYellow: 'var(--warning-soft)', brightBlue: 'var(--action-text)', brightMagenta: 'var(--purple-soft)',
        brightCyan: 'var(--cyan-soft)', brightWhite: 'var(--text-strong)',
      },
      scrollback: 5000,
      tabStopWidth: 8,
      convertEol: true,
    })

    if (!terminalRef.current) {
      setState(DISCONNECTED)
      setErrorMsg('终端容器未就绪')
      return
    }

    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    fitAddonRef.current = fitAddon

    term.open(terminalRef.current)
    termRef.current = term

    requestAnimationFrame(() => {
      try { fitAddon.fit() } catch { }
    })

    const ro = new ResizeObserver(() => {
      requestAnimationFrame(() => {
        try { fitAddonRef.current?.fit() } catch { }
      })
    })
    ro.observe(terminalRef.current)
    resizeObserverRef.current = ro

    term.onResize(({ cols, rows }) => {
      if (cols <= 0 || rows <= 0) return
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        const now = Date.now()
        if (now - lastResizeTimeRef.current < 1000) return
        const last = lastResizeRef.current
        if (!last || last.cols !== cols || last.rows !== rows) {
          lastResizeTimeRef.current = now
          lastResizeRef.current = { cols, rows }
          wsRef.current.send(JSON.stringify({ type: 'resize', cols, rows }))
        }
      }
    })

    term.attachCustomKeyEventHandler((e) => {
      if (e.key === 'F11') { e.preventDefault(); toggleFullscreen(); return false }
      if (e.ctrlKey && e.shiftKey && (e.key === 'C' || e.key === 'c')) {
        const sel = term.getSelection()
        if (sel) {
          navigator.clipboard.writeText(sel).then(() => {
            setCopied(true); setTimeout(() => setCopied(false), 2000)
          }).catch(() => { })
        }
        e.preventDefault(); return false
      }
      if (e.ctrlKey && e.shiftKey && (e.key === 'V' || e.key === 'v')) {
        e.preventDefault()
        navigator.clipboard.readText().then((text) => {
          if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({ type: 'input', data: text }))
          }
        }).catch(() => { })
        return false
      }
      return true
    })

    term.onData((data) => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        if ((data.includes('\r') || data.includes('\n')) && !guardCommandBeforeSend(pendingLineRef.current)) {
          pendingLineRef.current = ''
          return
        }
        resetIdleTimer()
        wsRef.current.send(JSON.stringify({ type: 'input', data }))
        rememberTerminalInput(data)
      }
    })

    serverWorkbench.terminalSessionCreate(name, term.cols, term.rows).then((res: any) => {
      const sid = res.data.session_id
      setSessionId(sid)
      sessionIdRef.current = sid

      const apiBase = getApiBaseUrl()
      let wsHost: string
      let wsProtocol: string
      if (apiBase.startsWith('http')) {
        const url = new URL(apiBase)
        wsHost = url.host
        wsProtocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
      } else {
        wsHost = window.location.host
        wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      }
      const wsUrl = `${wsProtocol}//${wsHost}/api/v2/servers/${encodeURIComponent(name)}/terminal/ws?session_id=${sid}`
      const sock = new WebSocket(wsUrl)
      sock.binaryType = 'arraybuffer'
      wsRef.current = sock

      sock.onopen = () => {
        setState(CONNECTED)
        fetchSessionCount()
        resetIdleTimer()
        term.focus()
        requestAnimationFrame(() => {
          try { fitAddon.fit() } catch { }
        })
        if (heartbeatRef.current) clearInterval(heartbeatRef.current)
        heartbeatRef.current = setInterval(() => {
          if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({ type: 'ping' }))
          }
        }, 30000)
      }

      sock.onmessage = (event) => {
        resetIdleTimer()
        if (event.data instanceof ArrayBuffer) { term.write(new Uint8Array(event.data)); return }
        if (typeof event.data === 'string') {
          try {
            const msg = JSON.parse(event.data)
            if (msg.type === 'connected') return
            if (msg.type === 'error') { term.writeln(`\r\n\x1b[31m错误: ${msg.message}\x1b[0m`); return }
          } catch { term.write(event.data) }
        }
        if (event.data instanceof Blob) {
          const reader = new FileReader()
          reader.onload = () => { if (reader.result) term.write(new Uint8Array(reader.result as ArrayBuffer)) }
          reader.readAsArrayBuffer(event.data)
        }
      }

      sock.onclose = (e) => {
        setState(DISCONNECTED)
        if (idleTimerRef.current) { clearInterval(idleTimerRef.current); idleTimerRef.current = null }
        if (heartbeatRef.current) { clearInterval(heartbeatRef.current); heartbeatRef.current = null }
        term.writeln(`\r\n\x1b[33m连接已断开${e.reason ? ': ' + e.reason : ''}\x1b[0m`)
      }

      sock.onerror = () => { term.writeln('\r\n\x1b[31m连接错误\x1b[0m') }
    }).catch((e: any) => {
      setState(DISCONNECTED)
      const msg = typeof e === 'string' ? e : e?.response?.data?.detail || e?.message || '未知错误'
      setErrorMsg(msg)
      term.writeln(`\r\n\x1b[31m连接失败: ${msg}\x1b[0m`)
    })
  }, [name, fetchSessionCount, resetIdleTimer, toggleFullscreen])

  const disconnect = useCallback(() => {
    setPendingConfirm({
      kind: 'disconnect',
      title: '断开终端会话',
      description: '将关闭当前 WebSocket 终端连接，不会自动停止已经在远端执行的后台进程。',
      confirmLabel: '断开',
      danger: true,
    })
  }, [])

  const confirmPendingConfirm = useCallback(() => {
    const action = pendingConfirm
    setPendingConfirm(null)
    if (!action) return
    if (action.kind === 'disconnect') {
      if (sessionId) serverWorkbench.terminalSessionClose(name, sessionId).catch(() => { })
      cleanup()
      return
    }
    if (action.kind === 'deletePreset') {
      setPresets((prev) => {
        const next = prev.filter((p) => p.id !== action.id)
        savePresets(next)
        return next
      })
      return
    }
    if (action.kind === 'resetPresets') {
      setPresets(DEFAULT_PRESETS)
      savePresets(DEFAULT_PRESETS)
    }
  }, [pendingConfirm, name, sessionId, cleanup])

  useEffect(() => {
    const timer = setTimeout(() => connect(), 100)
    const handleBeforeUnload = () => {
      if (sessionIdRef.current) {
        navigator.sendBeacon(`/api/v2/servers/${encodeURIComponent(name)}/terminal/sessions/${sessionIdRef.current}`)
      }
      if (wsRef.current) { try { wsRef.current.close() } catch { } }
      if (resizeObserverRef.current) { resizeObserverRef.current.disconnect(); resizeObserverRef.current = null }
      if (termRef.current) { try { termRef.current.dispose(); termRef.current = null } catch { } }
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => {
      clearTimeout(timer)
      window.removeEventListener('beforeunload', handleBeforeUnload)
      if (idleTimerRef.current) clearInterval(idleTimerRef.current)
      if (resizeObserverRef.current) resizeObserverRef.current.disconnect()
      if (wsRef.current) { try { wsRef.current.close() } catch { } }
      if (termRef.current) { try { termRef.current.dispose() } catch { } }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Tab 切换后重新 fit
  useEffect(() => {
    if (!active) return
    const timer = window.setTimeout(() => {
      try { fitAddonRef.current?.fit() } catch { }
    }, 50)
    return () => window.clearTimeout(timer)
  }, [active])

  // 窗口 resize 时重新 fit
  useEffect(() => {
    const handleResize = () => {
      requestAnimationFrame(() => {
        try { fitAddonRef.current?.fit() } catch { }
      })
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  const executeQuickCommand = useCallback(async (preset: PresetCommand) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      resetIdleTimer()
      wsRef.current.send(JSON.stringify({ type: 'input', data: preset.cmd + '\r' }))
      termRef.current?.focus()
      return
    }
    setQuickRunning(true)
    if (termRef.current) {
      termRef.current.writeln(`\r\n$ ${preset.cmd}`)
      termRef.current.write(' 执行中...')
    }
    try {
      const res: any = await serverWorkbench.exec(name, preset.cmd)
      const d = res.data
      const dur = d.duration_ms ? ` [${d.duration_ms}ms]` : ''
      const hopInfo = d.hop_context?.is_proxied ? ` [经由${d.hop_context.hop_count}跳]` : ''
      if (termRef.current) {
        termRef.current.writeln(`\r\n$ ${preset.cmd}${dur}${hopInfo}`)
        termRef.current.writeln(`[exit: ${d.exit_code}]`)
        if (d.stdout) termRef.current.writeln(d.stdout)
        if (d.stderr) termRef.current.writeln(`\r\n\x1b[33m[stderr]\x1b[0m\r\n${d.stderr}`)
      }
    } catch (e: any) {
      if (termRef.current) {
        termRef.current.writeln(`\r\n\x1b[31m❌ 错误: ${e?.response?.data?.detail || e?.message || '未知错误'}\x1b[0m`)
      }
    }
    setQuickRunning(false)
  }, [name, resetIdleTimer])

  const sendQuickCommand = useCallback((preset: PresetCommand) => {
    const dangerLabel = checkDangerous(preset.cmd)
    if (preset.confirm || dangerLabel) {
      setCommandRiskConfirmValue('')
      setPendingCommandRisk({
        source: 'quick',
        preset,
        cmd: preset.cmd,
        dangerLabel: dangerLabel || '预置命令要求确认',
        title: dangerLabel ? '确认执行高风险预置命令' : '确认执行预置命令',
        description: '该命令会通过服务器终端执行。请确认目标服务器和命令内容。',
        confirmText: `RUN ${preset.cmd}`,
        confirmButtonLabel: '确认执行命令',
        riskLevel: dangerLabel ? 'high' : 'medium',
      })
      return
    }
    void executeQuickCommand(preset)
  }, [executeQuickCommand])

  const confirmPendingCommandRisk = useCallback(() => {
    const action = pendingCommandRisk
    setPendingCommandRisk(null)
    setCommandRiskConfirmValue('')
    if (!action) return
    if (action.source === 'terminal') {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        resetIdleTimer()
        wsRef.current.send(JSON.stringify({ type: 'input', data: '\r' }))
        termRef.current?.focus()
      }
      return
    }
    if (action.preset) void executeQuickCommand(action.preset)
  }, [pendingCommandRisk, executeQuickCommand, resetIdleTimer])

  const copyAll = useCallback(() => {
    const sel = termRef.current?.getSelection()
    if (sel) {
      navigator.clipboard.writeText(sel).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000) }).catch(() => { })
      return
    }
    const buffer = termRef.current?.buffer.active
    if (buffer) {
      let all = ''
      for (let i = 0; i < buffer.length; i++) {
        const line = buffer.getLine(i)
        if (line) all += line.translateToString() + '\n'
      }
      navigator.clipboard.writeText(all).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000) }).catch(() => { })
    }
  }, [])

  const openPresetEditor = (preset?: PresetCommand) => {
    if (preset) {
      setEditingPreset(preset)
      setPresetFormLabel(preset.label)
      setPresetFormCmd(preset.cmd)
      setPresetFormConfirm(!!preset.confirm)
    } else {
      setEditingPreset(null)
      setPresetFormLabel('')
      setPresetFormCmd('')
      setPresetFormConfirm(false)
    }
    setPresetFormError('')
    setShowPresetEditor(true)
  }

  const savePreset = () => {
    const label = presetFormLabel.trim()
    const cmd = presetFormCmd.trim()
    if (!label) { setPresetFormError('标签名不能为空'); return }
    const cmdErr = validateCommand(cmd)
    if (cmdErr) { setPresetFormError(cmdErr); return }

    setPresets((prev) => {
      let next: PresetCommand[]
      if (editingPreset) {
        next = prev.map((p) => p.id === editingPreset.id ? { ...p, label, cmd, confirm: presetFormConfirm } : p)
      } else {
        next = [...prev, { id: `custom_${Date.now()}_${++_presetSeq}`, label, cmd, confirm: presetFormConfirm }]
      }
      savePresets(next)
      return next
    })
    setShowPresetEditor(false)
  }

  const deletePreset = (id: string) => {
    const item = presets.find((p) => p.id === id)
    setPendingConfirm({
      kind: 'deletePreset',
      id,
      title: '删除预置命令',
      description: `确认删除预置命令「${item?.label || id}」？`,
      confirmLabel: '删除',
      danger: true,
    })
  }

  const resetPresets = () => {
    setPendingConfirm({
      kind: 'resetPresets',
      title: '恢复默认预置命令',
      description: '将用系统默认预置命令覆盖当前自定义命令。',
      confirmLabel: '恢复默认',
      danger: true,
    })
  }

  const changeFontSize = useCallback((delta: number) => {
    setFontSize(prev => {
      const next = Math.max(10, Math.min(24, prev + delta))
      fontSizeRef.current = next
      if (termRef.current) {
        termRef.current.options.fontSize = next
        setTimeout(() => fitAddonRef.current?.fit(), 0)
      }
      return next
    })
  }, [])

  const statusLabel = state === CONNECTING ? '连接中...' : state === CONNECTED ? '已连接' : '已断开'
  const statusColor = state === CONNECTED ? 'var(--success)' : state === CONNECTING ? 'var(--warning)' : 'var(--danger)'

  return (
    <div ref={panelRef} className="server-terminal-panel">
      {/* 工具栏 */}
      <div className="server-terminal-toolbar">
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', flexWrap: 'wrap', minWidth: 0 }}>
          <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: statusColor, display: 'inline-block', flexShrink: 0 }} />
          <span style={{ color: 'var(--text-secondary)' }}>{statusLabel}</span>
          {errorMsg && <span style={{ color: 'var(--danger)', fontSize: '11px', background: 'var(--danger-surface)', padding: '1px 6px', borderRadius: '4px', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{errorMsg}</span>}
          {sessionId && <span className="mobile-hide" style={{ color: 'var(--text-muted)', fontSize: '10px' }}>ID: {sessionId.substring(0, 8)}</span>}
          {sessionCount > 0 && <span style={{ color: 'var(--success)', fontSize: '10px', background: 'var(--success-surface)', padding: '1px 6px', borderRadius: '4px' }}>sessions: {sessionCount}</span>}
          {idleWarning && <span style={{ color: 'var(--warning)', fontSize: '10px', background: 'var(--warning-surface)', padding: '1px 6px', borderRadius: '4px' }}>空闲超5分钟</span>}
          <button onClick={copyAll}
            style={{ fontSize: '10px', padding: '1px 6px', background: 'var(--bg-surface)', color: copied ? 'var(--success)' : 'var(--text-secondary)', border: '1px solid var(--border-strong)', borderRadius: '4px', cursor: 'pointer' }}
            title="Ctrl+Shift+C 复制选区，无选区则复制全部">
            {copied ? '已复制' : '复制'}
          </button>
        </div>
        <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
          <button onClick={() => changeFontSize(-1)}
            style={{ padding: '2px 6px', fontSize: '11px', background: 'var(--border-strong)', color: 'var(--text-secondary)', border: '1px solid var(--border-stronger)', borderRadius: '4px', cursor: 'pointer' }}
            title="缩小字体">
            A-
          </button>
          <span style={{ fontSize: '10px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center' }}>{fontSize}px</span>
          <button onClick={() => changeFontSize(1)}
            style={{ padding: '2px 6px', fontSize: '11px', background: 'var(--border-strong)', color: 'var(--text-secondary)', border: '1px solid var(--border-stronger)', borderRadius: '4px', cursor: 'pointer' }}
            title="放大字体">
            A+
          </button>
          <button onClick={toggleFullscreen}
            style={{ padding: '2px 8px', fontSize: '11px', background: 'var(--border-strong)', color: 'var(--text-secondary)', border: '1px solid var(--border-stronger)', borderRadius: '4px', cursor: 'pointer' }}
            title="F11 全屏">
            {isFullscreen ? '退出全屏' : '⬛'}
          </button>
          {state === CONNECTED && (
            <button onClick={disconnect}
              style={{ padding: '2px 8px', fontSize: '11px', background: 'var(--danger-surface)', color: 'var(--danger)', border: '1px solid var(--danger-border)', borderRadius: '4px', cursor: 'pointer' }}>
              断开
            </button>
          )}
          <button onClick={connect} disabled={state !== DISCONNECTED}
            style={{ padding: '2px 8px', fontSize: '11px', background: 'var(--action-bg)', color: 'var(--action-text)', border: '1px solid var(--brand-hover)', borderRadius: '4px', cursor: state !== DISCONNECTED ? 'not-allowed' : 'pointer', opacity: state !== DISCONNECTED ? 0.5 : 1 }}>
            重连
          </button>
        </div>
      </div>

      {/* 终端区域 - flex: 1 填满剩余空间 */}
      <div className="server-terminal-body">
        <div ref={terminalRef} className="server-terminal-xterm"
          onClick={() => termRef.current?.focus()}
        />
      </div>

      {/* 预置命令栏 */}
      <div className="server-terminal-preset-bar">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <button onClick={() => setPresetsCollapsed(!presetsCollapsed)}
            style={{ fontSize: '11px', color: 'var(--border-stronger)', background: 'none', border: 'none', cursor: 'pointer', padding: 0, display: 'flex', alignItems: 'center', gap: '4px' }}>
            <span style={{ transition: 'transform 0.2s', display: 'inline-block', transform: presetsCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)' }}>▾</span>
            预置命令 ({presets.length})
          </button>
          <div style={{ display: 'flex', gap: '3px' }}>
            <button onClick={() => openPresetEditor()}
              style={{ fontSize: '10px', padding: '1px 6px', background: 'var(--success-border)', color: 'var(--success)', border: 'none', borderRadius: '3px', cursor: 'pointer' }}>
              + 新增
            </button>
            <button onClick={resetPresets}
              style={{ fontSize: '10px', padding: '1px 6px', background: 'var(--border-strong)', color: 'var(--text-secondary)', border: 'none', borderRadius: '3px', cursor: 'pointer' }}>
              重置
            </button>
          </div>
        </div>
        {!presetsCollapsed && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '4px' }}>
            {presets.map((p) => (
              <div key={p.id} style={{ display: 'inline-flex', alignItems: 'center', gap: '0', borderRadius: '3px', overflow: 'hidden', border: '1px solid var(--action-bg)' }}>
                <button
                  onClick={() => sendQuickCommand(p)}
                  disabled={quickRunning}
                  style={{
                    padding: '2px 8px', fontSize: '11px', background: 'var(--action-bg)', color: 'var(--action-text)',
                    border: 'none', cursor: quickRunning ? 'not-allowed' : 'pointer', opacity: quickRunning ? 0.6 : 1,
                  }}
                  title={p.cmd}
                >
                  {p.label}{p.confirm ? ' ⚠' : ''}
                </button>
                <button onClick={() => openPresetEditor(p)}
                  style={{ padding: '2px 4px', fontSize: '10px', background: 'var(--bg-page)', color: 'var(--text-muted)', border: 'none', borderLeft: '1px solid var(--action-bg)', cursor: 'pointer' }}
                  title="编辑">✎</button>
                <button onClick={() => deletePreset(p.id)}
                  style={{ padding: '2px 4px', fontSize: '10px', background: 'var(--bg-page)', color: 'var(--danger)', border: 'none', borderLeft: '1px solid var(--action-bg)', cursor: 'pointer' }}
                  title="删除">✕</button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 预设命令编辑器弹窗 */}
      {showPresetEditor && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }} onClick={(e) => { if (e.target === e.currentTarget) setShowPresetEditor(false) }}>
          <div className="card" style={{ width: '440px', maxWidth: '90vw' }}>
            <h3 style={{ margin: '0 0 16px 0' }}>{editingPreset ? '编辑预置命令' : '新增预置命令'}</h3>

            {presetFormError && (
              <div style={{ background: 'var(--danger-surface)', color: 'var(--danger)', padding: '8px 12px', borderRadius: '6px', marginBottom: '12px', fontSize: '13px' }}>
                {presetFormError}
              </div>
            )}

            <div style={{ display: 'grid', gap: '12px' }}>
              <div>
                <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px' }}>标签名 *</label>
                <input value={presetFormLabel} onChange={(e) => setPresetFormLabel(e.target.value)}
                  placeholder="如: 查看日志" style={{ width: '100%' }} maxLength={20} />
              </div>
              <div>
                <label style={{ display: 'block', color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px' }}>命令 *</label>
                <textarea value={presetFormCmd} onChange={(e) => setPresetFormCmd(e.target.value)}
                  placeholder="如: pm2 logs --lines 100 --nostream"
                  style={{ width: '100%', minHeight: '60px', fontFamily: 'monospace', fontSize: '13px', resize: 'vertical' }}
                  maxLength={500} />
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>
                  {presetFormCmd.length}/500
                </div>
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                <input type="checkbox" checked={presetFormConfirm} onChange={(e) => setPresetFormConfirm(e.target.checked)} />
                执行前需要确认（适用于危险命令如重启操作）
              </label>

              <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', marginTop: '4px' }}>
                <button className="btn" onClick={() => setShowPresetEditor(false)}
                  style={{ padding: '6px 16px', background: 'var(--border-strong)', color: 'var(--text-primary)', fontSize: '13px' }}>
                  取消
                </button>
                <button className="btn" onClick={savePreset}
                  style={{ padding: '6px 16px', background: 'var(--success-border)', color: 'var(--success)', fontSize: '13px', fontWeight: 'bold' }}>
                  {editingPreset ? '保存修改' : '添加命令'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <RiskConfirmDialog
        open={Boolean(pendingCommandRisk)}
        title={pendingCommandRisk?.title || '确认执行命令'}
        description={pendingCommandRisk?.description}
        target={`${name}: ${pendingCommandRisk?.cmd || '-'}`}
        confirmText={pendingCommandRisk?.confirmText || ''}
        value={commandRiskConfirmValue}
        onValueChange={setCommandRiskConfirmValue}
        onCancel={() => { setPendingCommandRisk(null); setCommandRiskConfirmValue(''); termRef.current?.writeln('\r\n\x1b[33m命令已取消\x1b[0m') }}
        onConfirm={confirmPendingCommandRisk}
        riskLevel={pendingCommandRisk?.riskLevel || 'high'}
        details={[
          { label: '服务器', value: name },
          { label: '风险原因', value: pendingCommandRisk?.dangerLabel || '-' },
          { label: '命令', value: <code>{pendingCommandRisk?.cmd || '-'}</code> },
        ]}
        confirmButtonLabel={pendingCommandRisk?.confirmButtonLabel || '确认执行'}
        confirmMode="one-click"
      />

      <ConfirmDialog
        open={Boolean(pendingConfirm)}
        title={pendingConfirm?.title || '确认操作'}
        description={pendingConfirm?.description}
        confirmLabel={pendingConfirm?.confirmLabel || '确认'}
        danger={Boolean(pendingConfirm?.danger)}
        onCancel={() => setPendingConfirm(null)}
        onConfirm={confirmPendingConfirm}
      />
    </div>
  )
}
