import { useState } from 'react'

import { HistoryGallery, type HistoryGalleryItem, PortraitLightbox, useNaturalAspectRatio } from '@/shared'

export { Chip } from '@/shared/panel'
export { HistoryGallery, type HistoryGalleryItem, PortraitLightbox, useNaturalAspectRatio }

// 从 onboarding-flow.tsx 中抽离出的四个小 JSX 组件。
// 所有输入都通过 props 传入——不依赖模块级共享状态——便于单独隔离测试。

export function PortraitPanel({
  avatarUrl,
  hint,
  history,
  introHint,
  name,
  onSelectEntry,
  selectedIdx
}: {
  avatarUrl: string | null
  hint: string | null
  history?: HistoryGalleryItem[]
  introHint?: string | null
  name: string
  onSelectEntry?: (idx: number) => void
  selectedIdx?: number
}): React.JSX.Element {
  const [zoomedUrl, setZoomedUrl] = useState<string | null>(null)

  const gallery =
    history && history.length > 1 && onSelectEntry ? (
      <HistoryGallery entries={history} onSelect={onSelectEntry} selectedIdx={selectedIdx ?? history.length - 1} />
    ) : null

  return (
    <div className="flex flex-col items-center gap-2">
      {introHint && <p className="text-center text-[10px] leading-relaxed text-muted">{introHint}</p>}
      <PortraitThumb
        label="头像"
        name={name}
        onZoom={avatarUrl ? () => setZoomedUrl(avatarUrl) : undefined}
        size="lg"
        url={avatarUrl}
      />
      {gallery}
      {hint && <p className="text-xs text-rose-300/90">{hint}</p>}
      {zoomedUrl && <PortraitLightbox name={name} onClose={() => setZoomedUrl(null)} url={zoomedUrl} />}
    </div>
  )
}

function PortraitThumb({
  label,
  name,
  onZoom,
  size = 'sm',
  url
}: {
  label: string
  name: string
  onZoom: (() => void) | undefined
  size?: 'lg' | 'md' | 'sm'
  url: string | null
}): React.JSX.Element {
  const sizeClass = size === 'lg' ? 'h-48 w-48' : size === 'sm' ? 'h-28 w-28' : 'h-36 w-36'

  return (
    <div className="flex flex-col items-center gap-1">
      {url ? (
        <div className="group relative">
          <button
            aria-label="放大查看"
            className="block cursor-zoom-in overflow-hidden rounded-xl border-0 bg-transparent p-0"
            onClick={onZoom}
            type="button"
          >
            <img alt={name} className={`${sizeClass} object-cover shadow-lg`} src={url} />
          </button>
        </div>
      ) : (
        <div
          className={`grid ${sizeClass} place-items-center rounded-xl bg-fill-faint text-center text-[10px] text-faint`}
        >
          —
        </div>
      )}
      <span className="text-[10px] text-muted">{label}</span>
    </div>
  )
}
