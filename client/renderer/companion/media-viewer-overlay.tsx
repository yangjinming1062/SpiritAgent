import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

import { InlineMedia } from '@/chat'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import type { ChatMediaItem } from '@/shared/types/spiritagent'

// 富媒体查看器：聊天窗媒体卡点击后全屏放大；图片查看与视频播放共用一个遮罩。
const $mediaViewer = atom<ChatMediaItem | null>(null)

export function openMediaViewer(item: ChatMediaItem): void {
  $mediaViewer.set(item)
}

export function MediaViewerOverlay({ windowId = 0 }: { windowId?: number }): React.ReactPortal | null {
  const item = useStore($mediaViewer)
  const overlayRef = useRef<HTMLDivElement>(null)

  // 打开时把整个视口注册为可交互区，避免点击穿透到下层窗口；函数引用稳定，effect 不会每次渲染重挂。
  const getViewportRect = (): DOMRect => new DOMRect(0, 0, window.innerWidth, window.innerHeight)

  useInteractiveRegion('media-viewer', overlayRef, getViewportRect, undefined, windowId)

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
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 p-6 backdrop-blur-sm"
      onClick={() => $mediaViewer.set(null)}
      ref={overlayRef}
      style={{ pointerEvents: 'auto' }}
    >
      <ViewerSurface item={item} />
    </div>,
    document.body
  )
}

function ViewerSurface({ item }: { item: ChatMediaItem }): React.JSX.Element {
  return (
    <div className="max-h-full max-w-full" onClick={event => event.stopPropagation()}>
      <InlineMedia alt="" audioUrl={item.audio_url ?? null} mediaType={item.type} url={item.url} />
    </div>
  )
}
