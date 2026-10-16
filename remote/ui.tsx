import type { Dispatch, ReactElement, ReactNode, SetStateAction } from 'react'
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

import { errorText } from './api'

const PageState = createContext<{ prefix: string; values: Map<string, unknown> } | null>(null)

export function PageStateProvider({ children, scope }: { children: ReactNode; scope: string }): ReactElement {
  const [state] = useState(() => ({ prefix: `remote:${scope}:ui:`, values: new Map<string, unknown>() }))

  return <PageState.Provider value={state}>{children}</PageState.Provider>
}

export function usePageState<T>(key: string, initial: T, persist = true): [T, Dispatch<SetStateAction<T>>] {
  const state = useContext(PageState)
  const [value, setValue] = useState<T>(() => {
    if (state?.values.has(key)) {
      return state.values.get(key) as T
    }

    try {
      const saved = persist && state ? sessionStorage.getItem(state.prefix + key) : null

      return saved ? (JSON.parse(saved) as T) : initial
    } catch {
      return initial
    }
  })

  const valueRef = useRef(value)
  valueRef.current = value
  const update = useCallback<Dispatch<SetStateAction<T>>>(
    action => {
      const next = typeof action === 'function' ? (action as (previous: T) => T)(valueRef.current) : action
      valueRef.current = next
      state?.values.set(key, next)

      if (persist && state) {
        try {
          sessionStorage.setItem(state.prefix + key, JSON.stringify(next))
        } catch {
          // 浏览器存储不可用时仍保留当前登录期间的草稿。
        }
      }
      setValue(next)
    },
    [key, persist, state]
  )

  return [value, update]
}

export function usePageScroll(key: string, ready: boolean): void {
  const state = useContext(PageState)

  useEffect(() => {
    const content = document.querySelector<HTMLElement>('.content')
    if (!state || !content || !ready) {
      return
    }
    const storageKey = state.prefix + `scroll:${key}`
    let position = state.values.get(storageKey)
    if (position === undefined) {
      try {
        position = Number(sessionStorage.getItem(storageKey) ?? 0)
      } catch {
        position = 0
      }
    }
    const save = () => {
      state.values.set(storageKey, content.scrollTop)
      try {
        sessionStorage.setItem(storageKey, String(content.scrollTop))
      } catch {
        // 保留内存中的阅读位置。
      }
    }
    const frame = requestAnimationFrame(() => {
      content.scrollTop = typeof position === 'number' && Number.isFinite(position) ? position : 0
      content.addEventListener('scroll', save, { passive: true })
    })

    return () => {
      cancelAnimationFrame(frame)
      content.removeEventListener('scroll', save)
    }
  }, [key, ready, state])
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

export function focusAfterRender(selector: string): void {
  const content = document.querySelector('.content')
  requestAnimationFrame(() => {
    if (content?.isConnected) {
      content.querySelector<HTMLElement>(selector)?.focus({ preventScroll: true })
    }
  })
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

export function Notice({ children, error }: { children?: ReactNode; error?: string }): ReactElement {
  const raw = error ?? (typeof children === 'string' ? children : '')
  const message = /failed to fetch|networkerror|load failed|fetch failed/i.test(raw)
    ? '暂时连接不上。请检查网络后再试，已经提交的制作可以返回列表查看。'
    : /429|quota|rate.?limit|额度|配额/i.test(raw)
      ? '请求暂时受限，请稍后再试。已有内容仍然可以使用。'
      : /provider|supplier|api.?key|not configured|未配置|供应商|模型卡片/i.test(raw)
        ? 'AI 暂时不可用，请稍后重试。已保存的内容仍然保留。'
        : /traceback|internal server|exception|请求失败（5\d\d）/i.test(raw)
          ? '这次操作没有完成。请稍后重试；制作中的内容可以返回列表查看。'
          : raw

  return (
    <div className="notice" role="alert">
      {raw ? message : children}
      {error ? children : null}
      {raw && message !== raw ? (
        <details className="diagnostics">
          <summary>查看问题详情</summary>
          <p>{raw}</p>
        </details>
      ) : null}
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
  refresh,
  onReady
}: {
  type: 'image' | 'video' | 'audio'
  url: string
  refresh?: () => void
  onReady?: () => void
}): ReactElement {
  const failed = useRef(false)
  const [error, setError] = useState(false)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    failed.current = false
    setError(false)
  }, [url])

  const onError = () => {
    setError(true)
    if (!failed.current) {
      failed.current = true
      refresh?.()
    }
  }

  if (error) {
    return (
      <div className="media-retry" role="status">
        <p>{type === 'image' ? '图片' : type === 'video' ? '视频' : '语音'}暂时没有加载出来</p>
        <button
          onClick={() => {
            setError(false)
            setRevision(value => value + 1)
          }}
          type="button"
        >
          重新加载
        </button>
      </div>
    )
  }

  if (type === 'video') {
    return (
      <video
        className="media"
        controls
        key={`${url}:${revision}`}
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
        key={`${url}:${revision}`}
        onError={onError}
        onPlay={event => pauseMedia(event.currentTarget)}
        preload="none"
        src={url}
      />
    )
  }

  return (
    <a href={url} rel="noreferrer" target="_blank">
      <img
        alt="图片"
        className="media"
        key={`${url}:${revision}`}
        loading="lazy"
        onError={onError}
        onLoad={onReady}
        src={url}
      />
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
