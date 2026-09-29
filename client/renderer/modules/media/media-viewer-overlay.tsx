import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import { type RefObject, useEffect, useLayoutEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

import { probeInteractiveRegions, useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import type { ChatMediaItem } from '@/shared/types/spiritagent'

import { InlineMedia } from './inline-media'

// 富媒体查看器：聊天窗媒体卡点击后全屏放大；图片查看与视频播放共用一个遮罩。
const $mediaViewer = atom<ChatMediaItem | null>(null)

// 换号或登出时关闭，旧账户媒体不留在界面上。
registerStorageClearHandler(() => $mediaViewer.set(null))

export function openMediaViewer(item: ChatMediaItem): void {
  $mediaViewer.set(item)
}

export function MediaViewerOverlay({
  windowId = 0,
  containerRef
}: {
  windowId?: number
  containerRef?: RefObject<HTMLElement | null>
}): React.ReactPortal | null {
  const item = useStore($mediaViewer)
  const overlayRef = useRef<HTMLDivElement>(null)

  useInteractiveRegion('media-viewer', overlayRef, undefined, undefined, windowId)

  useLayoutEffect(() => {
    probeInteractiveRegions(windowId)
  }, [item, windowId])

  useEffect(() => {
    if (!item) {
      return
    }

    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        $mediaViewer.set(null)
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [item])

  if (!item || typeof document === 'undefined') {
    return null
  }

  return createPortal(
    <div
      className={`${containerRef ? 'absolute' : 'fixed'} inset-0 z-[100] flex items-center justify-center bg-black/80 p-6 backdrop-blur-sm`}
      onClick={() => $mediaViewer.set(null)}
      ref={overlayRef}
      style={{ pointerEvents: 'auto' }}
    >
      <ViewerSurface item={item} />
    </div>,
    containerRef?.current ?? document.body
  )
}

function ViewerSurface({ item }: { item: ChatMediaItem }): React.JSX.Element {
  return (
    <div className="max-h-full max-w-full" onClick={event => event.stopPropagation()}>
      <InlineMedia alt="" audioUrl={item.audio_url ?? null} mediaType={item.type} url={item.url} />
    </div>
  )
}
