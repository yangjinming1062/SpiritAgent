import { useCallback, useMemo, useState } from 'react'

import type { SpiritAgentConfigResponse } from '@/shared/types/spiritagent'

interface FormSection<T> {
  isDirty: boolean
  reset: (config: SpiritAgentConfigResponse) => void
  set: (patch: Partial<T>) => void
  state: T
}

export function useFormSection<T>(empty: T, read: (config: SpiritAgentConfigResponse) => T): FormSection<T> {
  const [original, setOriginal] = useState<T>(empty)
  const [current, setCurrent] = useState<T>(empty)

  const isDirty = useMemo(
    () =>
      Object.entries(current as Record<string, unknown>).some(
        ([k, v]) => v !== (original as Record<string, unknown>)[k]
      ),
    [current, original]
  )

  const set = useCallback((patch: Partial<T>) => {
    setCurrent(prev => ({ ...prev, ...patch }))
  }, [])

  const reset = useCallback(
    (config: SpiritAgentConfigResponse) => {
      const next = read(config)
      setOriginal(next)
      setCurrent(next)
    },
    [read]
  )

  return { state: current, isDirty, reset, set }
}
