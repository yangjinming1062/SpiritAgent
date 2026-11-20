import { useCallback, useEffect, useRef, useState } from 'react'

import { errorText } from './api'

export function requireCurrentRead(version: number, latest: number): void {
  if (version !== latest) {
    throw new DOMException('列表已开始新的读取，请重新核对结果', 'AbortError')
  }
}

export function useAlive(): () => boolean {
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true

    return () => {
      alive.current = false
    }
  }, [])

  return useCallback(() => alive.current, [])
}

export function useTask(): {
  busy: boolean
  error: string
  run: (task: () => Promise<void>) => Promise<void>
  runLatest: (task: () => Promise<void>) => Promise<void>
} {
  const current = useAlive()
  const lock = useRef(false)
  const queuedRead = useRef<(() => Promise<void>) | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const run = useCallback(
    async (task: () => Promise<void>) => {
      if (lock.current || !current()) {
        return
      }

      lock.current = true
      setBusy(true)
      setError('')

      try {
        let next: (() => Promise<void>) | null = task

        while (next && current()) {
          queuedRead.current = null

          try {
            await next()
          } catch (failure) {
            if (current() && !(failure instanceof DOMException && failure.name === 'AbortError')) {
              setError(errorText(failure))
            }
          }

          next = queuedRead.current
        }
      } finally {
        lock.current = false
        queuedRead.current = null

        if (current()) {
          setBusy(false)
        }
      }
    },
    [current]
  )

  const runLatest = useCallback(
    async (task: () => Promise<void>) => {
      if (lock.current) {
        queuedRead.current = task

        return
      }

      await run(task)
    },
    [run]
  )

  return { busy, error, run, runLatest }
}

export function useVisiblePolling(
  enabled: boolean,
  load: () => Promise<void>,
  runLatest: (task: () => Promise<void>) => Promise<void>
): void {
  useEffect(() => {
    if (!enabled) {
      return
    }
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') {
        void runLatest(load)
      }
    }, 5000)

    return () => clearInterval(timer)
  }, [enabled, load, runLatest])
}
