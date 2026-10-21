import type { ReactNode, Ref } from 'react'

import { cn } from '@/shared/lib/utils'

import { PAGE_INSET_X } from '../layout/page-inset'

import { CHIP, CHIP_FILTER_ACTIVE } from './palette'

// 设置页基元：视觉词汇取自 panel palette；容器背景由所在窗口提供，此处只约束正文宽度并提供滚动 + 内边距。

export function SettingsContent({
  children,
  contentClassName,
  scrollRef
}: {
  children: ReactNode
  contentClassName?: string
  scrollRef?: Ref<HTMLDivElement>
}): React.JSX.Element {
  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className={cn('h-full min-h-0 overflow-y-auto pb-20 pt-6', PAGE_INSET_X)} ref={scrollRef}>
        <div className={cn('mx-auto w-full max-w-4xl text-strong', contentClassName)}>{children}</div>
      </div>
    </section>
  )
}

export type PillTone = 'muted' | 'primary' | 'accent' | 'success' | 'warning'

// 状态胶囊的视觉词汇：中性与激活沿用设置页基色，强调/成功/警告是实底低透明。
const PILL_TONE: Record<PillTone, string> = {
  muted: CHIP,
  primary: CHIP_FILTER_ACTIVE,
  accent: 'inline-flex shrink-0 items-center rounded-full bg-accent/15 px-2.5 py-1 text-[10px] text-accent',
  success: 'inline-flex shrink-0 items-center rounded-full bg-success/15 px-2.5 py-1 text-[10px] text-success',
  warning: 'inline-flex shrink-0 items-center rounded-full bg-warning/15 px-2.5 py-1 text-[10px] text-warning'
}

export function Pill({
  tone = 'muted',
  children,
  className,
  title
}: {
  tone?: PillTone
  children: ReactNode
  className?: string
  title?: string
}): React.JSX.Element {
  return (
    <span className={cn(PILL_TONE[tone], className)} title={title}>
      {children}
    </span>
  )
}

export function SettingsSubsection({
  children,
  intro,
  title
}: {
  children: ReactNode
  intro?: string
  title: string
}): React.JSX.Element {
  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-strong">
        <span>{title}</span>
      </div>
      {intro ? <p className="text-[10px] text-faint">{intro}</p> : null}
      <div className="space-y-2">{children}</div>
    </div>
  )
}

export function ListRow({
  title,
  description,
  action
}: {
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
}): React.JSX.Element {
  return (
    <div className="group relative grid gap-3 rounded-xl border border-line-hairline bg-fill-faint p-3 transition-all duration-150 hover:border-line-strong hover:bg-fill-hover sm:grid-cols-[minmax(0,1fr)_minmax(15rem,22rem)] sm:items-center">
      <div className="min-w-0">
        <div className="text-xs font-medium text-strong">{title}</div>
        {description && <div className="mt-1 text-[11px] leading-relaxed text-muted">{description}</div>}
      </div>
      {action && <div className="min-w-0 sm:justify-self-end">{action}</div>}
    </div>
  )
}
