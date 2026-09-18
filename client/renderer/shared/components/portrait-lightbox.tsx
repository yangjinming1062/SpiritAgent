import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { Copy, Download } from '@/shared/lib/icons'
import { imageUrlForNativeClipboard } from '@/shared/lib/image-clipboard'
import { useStrings } from '@/shared/strings'

import { useEscapeKey } from '../hooks/use-escape-key'
import { useInteractiveRegion } from '../lib/interactive-regions'

export interface HistoryGalleryItem {
  url: string | null
}

export function HistoryGallery({
  entries,
  onSelect,
  selectedIdx
}: {
  entries: HistoryGalleryItem[]
  onSelect: (idx: number) => void
  selectedIdx: number
}): React.JSX.Element {
  return (
    <div className="mt-1 flex justify-center gap-1.5">
      {entries.map((entry, idx) => {
        return (
          <button
            className={`overflow-hidden rounded-md border transition ${
              idx === selectedIdx ? 'border-accent' : 'border-line-hairline opacity-60 hover:opacity-90'
            }`}
            key={idx}
            onClick={() => onSelect(idx)}
            type="button"
          >
            {entry.url ? (
              <img alt="" className="h-10 w-10 object-cover" src={entry.url} />
            ) : (
              <div className="grid h-10 w-10 place-items-center text-[10px] text-faint">—</div>
            )}
          </button>
        )
      })}
    </div>
  )
}

// 立绘画幅随物种骨骼分桶（竖/方/横并存），取景框比例须跟随图片本身——
// 这里探测 naturalWidth/Height 供容器设 aspectRatio；加载完成前返回 null（调用方回退双足竖版占位）。
export function useNaturalAspectRatio(src: string | null | undefined): number | null {
  const [ratio, setRatio] = useState<number | null>(null)

  useEffect(() => {
    setRatio(null)

    if (!src) {
      return
    }

    let alive = true
    const img = new Image()

    img.onload = () => {
      if (alive && img.naturalWidth > 0 && img.naturalHeight > 0) {
        setRatio(img.naturalWidth / img.naturalHeight)
      }
    }

    img.src = src

    return () => {
      alive = false
    }
  }, [src])

  return ratio
}

// 灯箱按立绘本身尺寸渲染——不铺满整屏深色遮罩。图片本身即窗口，关闭按钮叠在其右上角；
// 半透明背景负责捕获外部点击。通过 createPortal 挂到 document.body，
// 避免 onboarding 容器的 `backdrop-filter`（依 CSS Containing Block 规则）
// 把我们的 `position: fixed` 锁死在对话框里。
export function PortraitLightbox({
  name,
  onClose,
  url
}: {
  name: string
  onClose: () => void
  url: string
}): React.ReactPortal | null {
  const t = useStrings()
  const overlayRef = useRef<HTMLDivElement>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  // 灯箱打开时把整个视口注册为可交互区，避免点击图片或背景时事件穿透到下层窗口。
  // hook 内部经 getRectRef 镜像读取，函数身份无需稳定。
  const getLightboxRect = (): DOMRect => new DOMRect(0, 0, window.innerWidth, window.innerHeight)

  useInteractiveRegion('portrait-lightbox', overlayRef, getLightboxRect)

  // 灯箱挂在 bubble 阶段、不阻断冒泡——让外层的"返回上一层"也能响应 Esc。
  useEscapeKey(onClose, { capture: false, preventDefault: false, stopPropagation: false })

  if (typeof document === 'undefined') {
    return null
  }

  return createPortal(
    <div
      aria-label={t.ui.lightbox.backdropAria}
      className="fixed inset-0 z-[100] flex items-center justify-center p-6"
      onClick={onClose}
      ref={overlayRef}
      role="dialog"
      style={{ background: 'rgba(0,0,0,0.35)', pointerEvents: 'auto' }}
    >
      <div className="flex max-h-[90vh] max-w-[90vw] flex-col items-center gap-2">
        <button
          aria-label={t.ui.lightbox.closePreview}
          className="block cursor-zoom-out rounded-2xl border-0 bg-transparent p-0"
          onClick={onClose}
          type="button"
        >
          <img alt={name} className="block max-h-[80vh] max-w-[90vw] rounded-2xl object-contain shadow-2xl" src={url} />
        </button>
        <div className="flex items-center gap-2" onClick={e => e.stopPropagation()}>
          <button
            className="inline-flex h-7 items-center gap-1 rounded-lg bg-black/70 px-2 text-[11px] text-white/90 transition hover:bg-black/90 hover:text-white"
            onClick={e => {
              e.stopPropagation()

              setActionError(null)

              void (async (): Promise<void> => {
                try {
                  setCopied(false)

                  const copyImage = window.spiritagent?.copyImage

                  if (!copyImage) {
                    throw new Error('copyImage IPC unavailable')
                  }

                  await copyImage({ url: await imageUrlForNativeClipboard(url) })
                  setCopied(true)
                } catch {
                  setActionError(t.selfSource.copyRefImageFailed)
                }
              })()
            }}
            type="button"
          >
            <Copy className="size-3.5" />
            {t.selfSource.copyRefImage}
          </button>
          <button
            className="inline-flex h-7 items-center gap-1 rounded-lg bg-black/70 px-2 text-[11px] text-white/90 transition hover:bg-black/90 hover:text-white"
            onClick={e => {
              e.stopPropagation()
              setActionError(null)
              setCopied(false)

              void (async (): Promise<void> => {
                try {
                  const saveImage = window.spiritagent?.saveImage

                  if (!saveImage) {
                    throw new Error('saveImage IPC unavailable')
                  }

                  await saveImage({ defaultName: name || undefined, url })
                } catch {
                  setActionError(t.selfSource.saveRefImageFailed)
                }
              })()
            }}
            type="button"
          >
            <Download className="size-3.5" />
            {t.selfSource.saveRefImage}
          </button>
        </div>
        {copied && (
          <p className="text-xs text-white/80" role="status">
            {t.selfSource.copiedRefImage}
          </p>
        )}
        {actionError && (
          <p className="text-xs text-rose-300" role="alert">
            {actionError}
          </p>
        )}
      </div>
    </div>,
    document.body
  )
}
