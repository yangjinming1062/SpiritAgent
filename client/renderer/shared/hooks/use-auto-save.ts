import { useCallback, useEffect, useRef, useState } from 'react'

export type AutoSaveStatus = 'idle' | 'saving' | 'saved' | 'error'

interface UseAutoSaveOptions<T> {
  dirty: boolean
  delayMs?: number
  onError?: (error: unknown) => void
  onSave: (value: T) => Promise<void>
  value: T
}

interface AutoSaveState {
  status: AutoSaveStatus
}

const DEFAULT_DELAY_MS = 350

/** 防抖、串行提交设置草稿；组件卸载时会把尚未开始的最后一版立即提交。 */
export function useAutoSave<T>({
  dirty,
  delayMs = DEFAULT_DELAY_MS,
  onError,
  onSave,
  value
}: UseAutoSaveOptions<T>): AutoSaveState {
  const mountedRef = useRef(true)
  const latestValueRef = useRef(value)
  const onErrorRef = useRef(onError)
  const onSaveRef = useRef(onSave)
  const pendingRef = useRef<{ revision: number; value: T } | null>(null)
  const revisionRef = useRef(0)
  const savingRef = useRef(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const flushRef = useRef<() => void>(() => undefined)
  const [status, setStatus] = useState<AutoSaveStatus>('idle')

  latestValueRef.current = value
  onErrorRef.current = onError
  onSaveRef.current = onSave

  const clearTimer = useCallback((): void => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const flush = useCallback((): void => {
    if (savingRef.current || pendingRef.current === null) {
      return
    }

    const pending = pendingRef.current
    pendingRef.current = null
    savingRef.current = true
    let succeeded = false

    if (mountedRef.current) {
      setStatus('saving')
    }

    void onSaveRef
      .current(pending.value)
      .then(() => {
        succeeded = true

        if (mountedRef.current && revisionRef.current === pending.revision) {
          setStatus('saved')
        }
      })
      .catch(error => {
        if (revisionRef.current === pending.revision && pendingRef.current === null) {
          pendingRef.current = pending
        }

        if (mountedRef.current && revisionRef.current === pending.revision) {
          setStatus('error')
          onErrorRef.current?.(error)
        }
      })
      .finally(() => {
        savingRef.current = false

        if (pendingRef.current !== null && (succeeded || pendingRef.current.revision !== pending.revision)) {
          queueMicrotask(() => flushRef.current())
        }
      })
  }, [])

  flushRef.current = flush

  useEffect(() => {
    if (!dirty) {
      clearTimer()
      pendingRef.current = null

      return
    }

    revisionRef.current += 1
    pendingRef.current = { revision: revisionRef.current, value: latestValueRef.current }

    if (mountedRef.current) {
      setStatus('saving')
    }

    clearTimer()
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      flushRef.current()
    }, delayMs)
  }, [clearTimer, delayMs, dirty, value])

  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
      clearTimer()
      flushRef.current()
    }
  }, [clearTimer])

  return { status }
}
