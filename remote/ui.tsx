import type { Dispatch, ReactElement, ReactNode, Ref, SetStateAction } from 'react'
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

import type { VideoQueueState } from '@protocol'

import { classifyError } from '../shared/error-classification'
import { uiCopyErrors } from '../shared/ui-copy'

import { ApiError, errorText } from './api'

export function videoQueueText(queue: VideoQueueState): string {
  return {
    waiting: '视频排队中',
    rate_limited: '服务繁忙，等待重试',
    blocked: '等待核对生成结果'
  }[queue.state]
}

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

export function useAssetSubmission(
  key: string,
  onCheck: () => Promise<void>
): {
  check: () => Promise<void>
  clearSubmission: () => void
  reconciled: boolean
  submit: <Result>(task: () => Promise<Result>) => Promise<Result>
  submitted: boolean
} {
  const alive = useAlive()
  const [submitted, setSubmitted] = usePageState(`${key}.submitted`, false)
  const [reconciled, setReconciled] = useState(false)

  const submit = async <Result,>(task: () => Promise<Result>): Promise<Result> => {
    setSubmitted(true)
    setReconciled(false)
    try {
      return await task()
    } catch (failure) {
      if (alive() && failure instanceof ApiError && failure.status !== null && failure.status < 500) {
        setSubmitted(false)
      }
      throw failure
    }
  }
  const check = async () => {
    await onCheck()
    if (alive()) {
      setReconciled(true)
    }
  }

  return { check, clearSubmission: () => setSubmitted(false), reconciled, submit, submitted }
}

export function AssetResultRecovery({
  onCheck,
  onContinue,
  reconciled
}: {
  onCheck: () => Promise<void>
  onContinue: () => void
  reconciled: boolean
}): ReactElement {
  return (
    <div className="creation-status">
      <p>上次制作的结果尚未确认。请先刷新，核对原来的结果。</p>
      <div className="button-row">
        <button onClick={onCheck} type="button">
          刷新结果
        </button>
        <button disabled={!reconciled} onClick={onContinue} type="button">
          已核对，继续调整
        </button>
      </div>
    </div>
  )
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

export function Empty({ title, children }: { title: string; children?: ReactNode }): ReactElement {
  return (
    <div className="empty">
      <span className="empty-mark">✦</span>
      <h3>{title}</h3>
      {children ? <p>{children}</p> : null}
    </div>
  )
}

/** 素材与影像列表共用的卡片；整卡可点进详情，不承载独立的卡片级动作。 */
export function LibraryCard({
  active = false,
  cardKey,
  imageAlt,
  imageUrl,
  meta,
  onSelect,
  submeta,
  title
}: {
  active?: boolean
  cardKey: string
  imageAlt: string
  imageUrl: string
  meta: string
  onSelect: () => void
  submeta?: string
  title: string
}): ReactElement {
  return (
    <article className={`asset-card ${active ? 'active' : ''}`}>
      <button className="asset-card-preview" data-card-key={cardKey} onClick={onSelect} type="button">
        {imageUrl ? <img alt={imageAlt} loading="lazy" src={imageUrl} /> : <div className="asset-placeholder">✦</div>}
        <div>
          <strong>{title}</strong>
          <small>{meta}</small>
          {submeta ? <small>{submeta}</small> : null}
        </div>
      </button>
    </article>
  )
}

export function Notice({ children, error }: { children?: ReactNode; error?: string }): ReactElement {
  const raw = error ?? (typeof children === 'string' ? children : '')
  const kind = classifyError(raw)
  // 归类得到的失败用跨端统一措辞，其余保留原文。
  const message = kind === 'unknown' ? raw : uiCopyErrors[kind]

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

export function FileInput({
  ref,
  accept,
  multiple = false,
  onFiles
}: {
  ref: Ref<HTMLInputElement>
  /** 允许的文件类型；缺省为图片格式。 */
  accept?: string
  multiple?: boolean
  onFiles: (files: File[]) => void
}): ReactElement {
  return (
    <input
      accept={accept ?? 'image/png,image/jpeg,image/webp,image/gif'}
      hidden
      multiple={multiple}
      onChange={event => {
        const files = [...(event.target.files ?? [])]
        event.target.value = ''
        if (files.length) {
          onFiles(multiple ? files : files.slice(0, 1))
        }
      }}
      ref={ref}
      type="file"
    />
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

  if (type !== 'image') {
    const Tag = type === 'video' ? 'video' : 'audio'

    return (
      <Tag
        className={type === 'video' ? 'media' : 'voice'}
        controls
        key={`${url}:${revision}`}
        onError={onError}
        onPlay={event => pauseMedia(event.currentTarget)}
        playsInline={type === 'video' || undefined}
        preload={type === 'video' ? 'metadata' : 'none'}
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
