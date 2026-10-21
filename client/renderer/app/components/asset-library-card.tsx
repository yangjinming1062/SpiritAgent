import type { ReactNode } from 'react'

import { cn } from '@/shared/lib/utils'
import { Pill, SettingCard } from '@/shared/panel'

// 预览比例由素材本身决定：竖版立绘按 4/5 展示，场景与背景按宽幅展示。
const PREVIEW_RATIO = {
  portrait: 'aspect-[4/5]',
  wide: 'aspect-video'
} as const

export function AssetLibraryGrid({ children }: { children: ReactNode }): React.JSX.Element {
  return (
    <div className="@container">
      <div className="grid grid-cols-1 items-start gap-3 @min-[460px]:grid-cols-2 @min-[700px]:grid-cols-3">
        {children}
      </div>
    </div>
  )
}

export function AssetLibraryCard({
  id,
  title,
  currentLabel,
  preview,
  previewRatio = 'wide',
  description,
  footer,
  onOpen,
  openLabel
}: {
  id: string | number
  title: string
  currentLabel?: string
  preview: ReactNode
  previewRatio?: keyof typeof PREVIEW_RATIO
  description: ReactNode
  footer: ReactNode
  onOpen: () => void
  openLabel: string
}): React.JSX.Element {
  return (
    <SettingCard className="min-w-0" divided={false}>
      <button
        aria-label={openLabel}
        className="block w-full min-w-0 text-left"
        data-asset-id={id}
        onClick={onOpen}
        type="button"
      >
        <div className={cn('relative w-full overflow-hidden bg-fill-trough', PREVIEW_RATIO[previewRatio])}>
          {preview}
        </div>
        <div className="space-y-1.5 p-3">
          <h3 className="flex min-w-0 items-center gap-2 text-xs font-medium text-strong">
            <span className="min-w-0 flex-1 truncate">{title}</span>
            {currentLabel ? <Pill tone="accent">{currentLabel}</Pill> : null}
          </h3>
          <div className="line-clamp-2 min-h-8 whitespace-pre-wrap break-words text-[10px] leading-relaxed text-faint">
            {description}
          </div>
        </div>
      </button>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line-hairline px-3 py-2">
        {footer}
      </div>
    </SettingCard>
  )
}
