import type { ReactNode, Ref } from 'react'
import { useEffect, useState } from 'react'

import { AlertCircle, Clock, Info, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'

import { Spinner } from './components'
import styles from './creation.module.css'

// 页头和操作区不进入正文滚动容器，操作栏的位置由布局负责。
export function CreationWorkspace({
  header,
  children,
  footer,
  className,
  scrollRef
}: {
  header?: ReactNode
  children: ReactNode
  footer?: ReactNode
  className?: string
  scrollRef?: Ref<HTMLDivElement>
}): React.JSX.Element {
  return (
    <section className={cn(styles.workspace, className)}>
      {header && <div className={styles.workspaceHeader}>{header}</div>}
      <div className={styles.workspaceBody} ref={scrollRef}>
        {children}
      </div>
      {footer && <div className={styles.workspaceFooter}>{footer}</div>}
    </section>
  )
}

export type CreationFeedbackState = 'working' | 'waiting' | 'review' | 'error' | 'unknown' | 'paused' | 'cancelled'

interface CreationFeedbackProps {
  state: CreationFeedbackState
  title?: string
  description?: ReactNode
  actions?: ReactNode
  startedAt?: number
  className?: string
}

// 展示状态由业务提供；等待提示只解释耗时，不推测进度或改变任务生命周期。
export function CreationFeedback({
  state,
  title,
  description,
  actions,
  startedAt,
  className
}: CreationFeedbackProps): React.JSX.Element | null {
  const t = useStrings().creation
  const [slow, setSlow] = useState(false)

  useEffect(() => {
    setSlow(false)

    if (state !== 'working') {
      return
    }

    const remaining = startedAt === undefined ? 30_000 : Math.max(0, startedAt + 30_000 - Date.now())
    const timer = window.setTimeout(() => setSlow(true), remaining)

    return () => window.clearTimeout(timer)
  }, [startedAt, state])

  const showSlow = slow && state === 'working'
  const hasMessage = Boolean(title || description || showSlow)

  if (!hasMessage && !actions) {
    return null
  }

  const Icon =
    state === 'error' || state === 'unknown'
      ? AlertCircle
      : state === 'cancelled'
        ? X
        : state === 'paused' || state === 'waiting'
          ? Clock
          : Info

  return (
    <div className={cn(styles.feedback, className)} data-creation-feedback={state}>
      {hasMessage && (
        <div
          aria-atomic="true"
          aria-live={state === 'error' ? 'assertive' : 'polite'}
          className={styles.message}
          role={state === 'error' ? 'alert' : 'status'}
        >
          {title &&
            (state === 'working' ? (
              <Spinner className={styles.icon} size="sm" tone="accent" />
            ) : (
              <Icon aria-hidden="true" className={styles.icon} />
            ))}
          <div className={styles.copy}>
            {title && <span className={styles.title}>{title}</span>}
            {description && <div className={styles.description}>{description}</div>}
            {showSlow && <p className={styles.description}>{t.slow}</p>}
          </div>
        </div>
      )}
      {actions && <div className={styles.actions}>{actions}</div>}
    </div>
  )
}

export function CreationActionBar({
  children,
  description,
  className
}: {
  children: ReactNode
  description?: ReactNode
  className?: string
}): React.JSX.Element {
  return (
    <div className={cn(styles.actionBar, className)} data-creation-action-bar="">
      {description && <div className={styles.actionDescription}>{description}</div>}
      <div className={styles.actions}>{children}</div>
    </div>
  )
}

export interface LibraryBannerCandidate {
  state: CreationFeedbackState
  title?: string
  description?: ReactNode
  actions?: ReactNode
}

/**
 * 素材库状态条：候选按优先级排列（失败 > 待核对 > 进行中），同屏只渲染第一条；
 * 无候选时零占位。各页只负责按自己的业务排出候选，不复制选择逻辑。
 */
export function LibraryStatusBanner({
  candidates,
  className
}: {
  candidates: ReadonlyArray<LibraryBannerCandidate | null | undefined>
  className?: string
}): React.JSX.Element | null {
  const banner = candidates.find((candidate): candidate is LibraryBannerCandidate => Boolean(candidate))

  if (!banner) {
    return null
  }

  return (
    <CreationFeedback
      actions={banner.actions}
      className={className}
      description={banner.description}
      state={banner.state}
      title={banner.title}
    />
  )
}

/** 制作步骤条：描述想法 → 预览确认 → 完成制作，跨端同构；仅标注当前步骤，不拦截流程。 */
export function CreationSteps({
  step,
  labels
}: {
  step: number
  /** 有序步骤文案；条目数量即步骤数。 */
  labels: readonly string[]
}): React.JSX.Element {
  return (
    <ol aria-label={labels.join(' → ')} className="flex flex-wrap items-center gap-2">
      {labels.map((label, index) => {
        const current = index + 1 === step
        const done = index + 1 < step

        return (
          <li aria-current={current ? 'step' : undefined} className="flex items-center gap-1.5" key={label}>
            <span
              className={cn(
                'grid size-4 shrink-0 place-items-center rounded-full text-[10px] font-medium',
                current ? 'bg-accent text-on-accent' : done ? 'bg-accent-soft text-accent' : 'bg-fill-faint text-faint'
              )}
            >
              {index + 1}
            </span>
            <span className={cn('text-[11px]', current ? 'font-medium text-strong' : 'text-faint')}>{label}</span>
            {index < labels.length - 1 && (
              <span aria-hidden="true" className="px-1 text-faint">
                →
              </span>
            )}
          </li>
        )
      })}
    </ol>
  )
}
