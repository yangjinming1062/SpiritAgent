import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import { type RefObject, useEffect, useId, useLayoutEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

import { usePanelActivity } from '@/shared/context/panel-activity'
import { useEscapeKey } from '@/shared/hooks/use-escape-key'
import { X } from '@/shared/lib/icons'
import { probeInteractiveRegions, useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'
import { PortraitLightbox } from '@/ui/portrait-lightbox'
import type { ChatMediaItem } from '@protocol'

import { InlineMedia } from './inline-media'
import { useResolvedMediaSrc } from './media-src'

// 聊天媒体查看入口：图片复用灯箱，其余媒体保留播放控件。
const $mediaViewer = atom<{ item: ChatMediaItem; ownerViewId?: string } | null>(null)

// 换号或登出时关闭，旧账户媒体不留在界面上。
registerStorageClearHandler(() => $mediaViewer.set(null))

export function openMediaViewer(item: ChatMediaItem, ownerViewId?: string): void {
  $mediaViewer.set({ item, ownerViewId })
}

function closeMediaViewer(): void {
  $mediaViewer.set(null)
}

export function MediaViewerOverlay({
  windowId = 0,
  containerRef,
  viewId
}: {
  windowId?: number
  containerRef?: RefObject<HTMLElement | null>
  viewId?: string
}): React.JSX.Element | null {
  const viewer = useStore($mediaViewer)

  useEffect(
    () => () => {
      if (viewId !== undefined && $mediaViewer.get()?.ownerViewId === viewId) {
        closeMediaViewer()
      }
    },
    [viewId]
  )

  return viewer && (viewId === undefined || viewer.ownerViewId === viewId) ? (
    <MediaViewer
      containerRef={containerRef}
      item={viewer.item}
      key={`${viewer.item.type}:${viewer.item.url}`}
      windowId={windowId}
    />
  ) : null
}

function MediaViewer({
  containerRef,
  item,
  windowId
}: {
  containerRef?: RefObject<HTMLElement | null>
  item: ChatMediaItem
  windowId: number
}): React.JSX.Element | React.ReactPortal {
  const overlayRef = useRef<HTMLDivElement>(null)
  const active = usePanelActivity()
  const regionId = useId()
  const dict = useStrings()
  const image = useResolvedMediaSrc({ type: 'image', url: item.type === 'image' ? item.url : '' })
  const showLightbox = item.type === 'image' && image.status === 'ready'

  useInteractiveRegion(
    `media-viewer:${regionId}`,
    overlayRef,
    el => (active ? el.getBoundingClientRect() : null),
    undefined,
    windowId
  )

  useLayoutEffect(() => {
    probeInteractiveRegions(windowId)
  }, [active, showLightbox, windowId])

  useEffect(() => {
    if (!active) {
      overlayRef.current?.querySelectorAll<HTMLMediaElement>('audio, video').forEach(media => media.pause())
    }
  }, [active])

  useEscapeKey(closeMediaViewer, {
    capture: false,
    enabled: !showLightbox,
    preventDefault: false,
    stopPropagation: false
  })

  if (showLightbox) {
    return (
      <PortraitLightbox
        containerRef={containerRef}
        name=""
        onClose={closeMediaViewer}
        url={image.src}
        windowId={windowId}
      >
        {item.audio_url ? <InlineMedia alt="" audioUrl={null} mediaType="audio" url={item.audio_url} /> : null}
      </PortraitLightbox>
    )
  }

  return createPortal(
    <div
      className={cn(
        containerRef ? 'absolute' : 'fixed',
        'inset-0 z-[100] flex items-center justify-center bg-black/80 p-6 backdrop-blur-sm [-webkit-app-region:no-drag]'
      )}
      onClick={closeMediaViewer}
      ref={overlayRef}
      style={{ display: active ? undefined : 'none', pointerEvents: 'auto' }}
    >
      <button
        aria-label={dict.ui.lightbox.closePreview}
        className="absolute right-4 top-4 flex size-8 items-center justify-center rounded-full bg-white/12 text-white/85 transition hover:bg-white/20 hover:text-white"
        onClick={event => {
          event.stopPropagation()
          closeMediaViewer()
        }}
        title={dict.ui.lightbox.closePreview}
        type="button"
      >
        <X size={16} />
      </button>
      {item.type === 'image' ? (
        <p className="text-sm text-white/80" role="status">
          {image.status === 'failed' ? dict.chat.media.loadFailed : dict.chat.media.imageLoading}
        </p>
      ) : (
        <ViewerSurface item={item} />
      )}
    </div>,
    containerRef?.current ?? document.body
  )
}

/** 应用内全屏呈现：铺满所属窗口并自动播放，退出靠关闭按钮、Esc 或点击背景。 */
function ViewerSurface({ item }: { item: ChatMediaItem }): React.JSX.Element {
  return (
    <div
      className="flex max-h-full max-w-full items-center justify-center [&_video]:max-h-[86vh]"
      onClick={event => event.stopPropagation()}
    >
      <InlineMedia alt="" audioUrl={item.audio_url ?? null} autoPlay mediaType={item.type} url={item.url} />
    </div>
  )
}
