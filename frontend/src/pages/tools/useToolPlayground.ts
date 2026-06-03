import { useState, useCallback } from 'react'
import { capabilityTools } from '../../api'

type PlaygroundState = {
  result: string
  error: string
  duration_ms: number | null
  executing: boolean
}

export function useToolPlayground() {
  const [state, setState] = useState<PlaygroundState>({
    result: '',
    error: '',
    duration_ms: null,
    executing: false,
  })

  const execute = useCallback(async (toolName: string, args: string) => {
    setState((prev) => ({ ...prev, executing: true, error: '', result: '' }))
    try {
      const parsed = JSON.parse(args)
      const res = await capabilityTools.call(toolName, parsed)
      const data = res.data
      setState({
        result: JSON.stringify(data?.result || data, null, 2),
        error: data?.error || '',
        duration_ms: data?.duration_ms || data?.elapsed_ms || null,
        executing: false,
      })
      return {
        result: JSON.stringify(data?.result || data, null, 2),
        error: data?.error || '',
        duration_ms: data?.duration_ms || data?.elapsed_ms || null,
      }
    } catch (err: any) {
      const message = err?.response?.data?.error || err?.message || 'Execution failed'
      setState({
        result: '',
        error: message,
        duration_ms: null,
        executing: false,
      })
      return { result: '', error: message, duration_ms: null }
    }
  }, [])

  const clear = useCallback(() => {
    setState({ result: '', error: '', duration_ms: null, executing: false })
  }, [])

  return { ...state, execute, clear }
}