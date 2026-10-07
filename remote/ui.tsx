import type { ReactElement, ReactNode } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { errorText } from './api'

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

export function pauseMedia(except?: HTMLMediaElement): void {
  document.querySelectorAll<HTMLMediaElement>('audio,video').forEach(media => {
    if (media !== except) {
      media.pause()
    }
  })
}

export function Empty({ title, children }: { title: string; children?: ReactNode }): ReactElement {
  return (
    <div className="empty">
      <span className="empty-mark">✦</span>
      <h3>{title}</h3>
      {children ? <p>{children}</p> : null}
    </div>
  )
}

export function Notice({ children }: { children: ReactNode }): ReactElement {
  return (
    <div className="notice" role="alert">
      {children}
    </div>
  )
}

export function PageHeader({
  title,
  subtitle,
  action
}: {
  title: string
  subtitle?: string
  action?: ReactNode
}): ReactElement {
  return (
    <header className="page-header">
      <div>
        <p className="eyebrow">SPIRITAGENT</p>
        <h1>{title}</h1>
        {subtitle ? <p className="subtitle">{subtitle}</p> : null}
      </div>
      {action}
    </header>
  )
}

export function Media({
  type,
  url,
  refresh
}: {
  type: 'image' | 'video' | 'audio'
  url: string
  refresh?: () => void
}): ReactElement {
  const failed = useRef(false)
  useEffect(() => {
    failed.current = false
  }, [url])

  const onError = () => {
    if (!failed.current) {
      failed.current = true
      refresh?.()
    }
  }

  if (type === 'video') {
    return (
      <video
        className="media"
        controls
        onError={onError}
        onPlay={event => pauseMedia(event.currentTarget)}
        playsInline
        preload="metadata"
        src={url}
      />
    )
  }

  if (type === 'audio') {
    return (
      <audio
        className="voice"
        controls
        onError={onError}
        onPlay={event => pauseMedia(event.currentTarget)}
        preload="none"
        src={url}
      />
    )
  }

  return (
    <a href={url} rel="noreferrer" target="_blank">
      <img alt="图片" className="media" loading="lazy" onError={onError} src={url} />
    </a>
  )
}

export function timestamp(value: string | number): string {
  const date = new Date(typeof value === 'number' ? value * 1000 : value)

  return Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleString('zh-CN', {
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      })
}
