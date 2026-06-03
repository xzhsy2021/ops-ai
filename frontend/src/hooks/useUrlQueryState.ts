import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

export function useUrlQueryState<T extends Record<string, string | undefined>>(
  defaults: T
): [T, (patch: Partial<T>) => void] {
  const [searchParams, setSearchParams] = useSearchParams()

  const state = useMemo(() => {
    const obj = { ...defaults }
    for (const key of Object.keys(defaults)) {
      const val = searchParams.get(key)
      if (val !== null) {
        ;(obj as any)[key] = val
      }
    }
    return obj
  }, [searchParams, defaults])

  const setState = useCallback(
    (patch: Partial<T>) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        for (const key of Object.keys(patch)) {
          const val = (patch as any)[key]
          if (val === undefined || val === '' || val === null) {
            next.delete(key)
          } else {
            next.set(key, String(val))
          }
        }
        return next
      }, { replace: true })
    },
    [setSearchParams]
  )

  return [state, setState]
}