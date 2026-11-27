import { IconRefresh } from '@tabler/icons-react'
import type { ReactElement, ReactNode, Ref } from 'react'
import { useEffect, useRef, useState } from 'react'

import type { VideoQueueState } from '@protocol'

import { classifyError } from '../shared/error-classification'
import { uiCopyErrors } from '../shared/ui-copy'

export function videoQueueText(queue: VideoQueueState): string {
  return {
    waiting: '视频排队中',
    rate_limited: '服务繁忙，等待重试',
    blocked: '等待核对生成结果'
  }[queue.state]
}

export function focusAfterRender(selector: string): void {
  const content = document.querySelector('.content')
  requestAnimationFrame(() => {
    if (content?.isConnected) {
      content.querySelector<HTMLElement>(selector)?.focus({ preventScroll: true })
    }
  })
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

export function RefreshButton({
  label,
  busy,
  onClick
}: {
  label: string
  busy: boolean
  onClick: () => void
}): ReactElement {
  return (
    <button aria-label={label} className="icon-button" disabled={busy} onClick={onClick} type="button">
      <IconRefresh size={20} />
    </button>
  )
}

export function ListSkeleton({ rows = 3 }: { rows?: number }): ReactElement {
  return (
    <div aria-hidden="true" className="skeleton-list">
      {Array.from({ length: rows }, (_, index) => (
        <div className="skeleton-row" key={index}>
          <div className="skeleton sk-avatar" />
          <div className="sk-lines">
            <div className="skeleton sk-line" />
            <div className="skeleton sk-line short" />
          </div>
        </div>
      ))}
    </div>
  )
}

export function GridSkeleton({ cards = 6 }: { cards?: number }): ReactElement {
  return (
    <div aria-hidden="true" className="skeleton-grid">
      {Array.from({ length: cards }, (_, index) => (
        <div className="skeleton sk-card" key={index} />
      ))}
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
  /** 状态标记：没有需要用户知道的状态时省略，不占行。 */
  meta?: string
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
          {meta ? <small>{meta}</small> : null}
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

export function AssetSteps({
  step,
  upload = false,
  labels
}: {
  step: number
  upload?: boolean
  labels?: readonly [string, string, string]
}): ReactElement {
  const steps = labels ?? [upload ? '选择图片' : '描述想法', '预览确认', '完成制作']

  return (
    <ol aria-label="制作步骤" className="creation-steps">
      {steps.map((label, index) => (
        <li aria-current={step === index + 1 ? 'step' : undefined} key={label}>
          <span>{index + 1}</span>
          {label}
        </li>
      ))}
    </ol>
  )
}

export function SectionNav<Id extends string>({
  label,
  items,
  selected,
  onSelect,
  unread
}: {
  label: string
  items: readonly { id: Id; title: string }[]
  selected: Id
  onSelect: (id: Id) => void
  unread?: Partial<Record<Id, boolean>>
}): ReactElement {
  return (
    <nav aria-label={label} className="companion-nav">
      {items.map(item => (
        <button
          aria-current={selected === item.id ? 'page' : undefined}
          key={item.id}
          onClick={() => onSelect(item.id)}
          type="button"
        >
          {item.title}
          {unread?.[item.id] && <i aria-label="有未读内容" className="tab-unread" />}
        </button>
      ))}
    </nav>
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
