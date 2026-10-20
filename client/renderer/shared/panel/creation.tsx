import type { ReactNode, Ref } from 'react'
import { useEffect, useState } from 'react'

import { AlertCircle, Clock, Info, Loader2, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'

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
  title: string
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
}: CreationFeedbackProps): React.JSX.Element {
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

  const Icon =
    state === 'working'
      ? Loader2
      : state === 'error' || state === 'unknown'
        ? AlertCircle
        : state === 'cancelled'
          ? X
          : state === 'paused' || state === 'waiting'
            ? Clock
            : Info

  return (
    <div className={cn(styles.feedback, className)} data-creation-feedback={state}>
      <div
        aria-atomic="true"
        aria-live={state === 'error' ? 'assertive' : 'polite'}
        className={styles.message}
        role={state === 'error' ? 'alert' : 'status'}
      >
        <Icon aria-hidden="true" className={cn(styles.icon, state === 'working' && styles.spinning)} />
        <div className={styles.copy}>
          <span className={styles.title}>{title}</span>
          {description && <div className={styles.description}>{description}</div>}
          {slow && state === 'working' && <p className={styles.description}>{t.slow}</p>}
        </div>
      </div>
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
