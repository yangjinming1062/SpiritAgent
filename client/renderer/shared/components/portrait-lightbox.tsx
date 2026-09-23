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

const MIN_SCALE = 1
const MAX_SCALE = 8
const WHEEL_ZOOM_STEP = 1.12
const DOUBLE_CLICK_SCALE = 2.5

type LightboxView = { scale: number; x: number; y: number }

function clampView(view: LightboxView, viewport: { height: number; width: number }): LightboxView {
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, view.scale))
  const maxX = Math.max(0, ((scale - 1) * viewport.width) / 2)
  const maxY = Math.max(0, ((scale - 1) * viewport.height) / 2)

  return {
    scale,
    x: Math.min(maxX, Math.max(-maxX, view.x)),
    y: Math.min(maxY, Math.max(-maxY, view.y))
  }
}

// 灯箱支持自由缩放：滚轮以指针为中心缩放，拖拽平移，双击在适应视口与放大之间切换。
// 图片本身即窗口；半透明背景只在点击背景（非图片/工具条）时关闭。
// 通过 createPortal 挂到 document.body，避免 onboarding 容器的 backdrop-filter
// 把 position: fixed 锁死在对话框里。
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
  const viewportRef = useRef<HTMLDivElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const viewRef = useRef<LightboxView>({ scale: 1, x: 0, y: 0 })

  const dragRef = useRef<null | {
    originX: number
    originY: number
    pointerId: number
    startX: number
    startY: number
  }>(null)

  const [view, setView] = useState<LightboxView>({ scale: 1, x: 0, y: 0 })
  const [actionError, setActionError] = useState<null | 'copy' | 'save'>(null)
  const [copied, setCopied] = useState(false)

  const commitView = (next: LightboxView): void => {
    const el = viewportRef.current

    const viewport = el
      ? { height: el.clientHeight, width: el.clientWidth }
      : { height: window.innerHeight * 0.8, width: window.innerWidth * 0.9 }

    const clamped = clampView(next, viewport)
    viewRef.current = clamped
    setView(clamped)
  }

  const zoomAt = (factor: number, clientX?: number, clientY?: number): void => {
    const el = viewportRef.current
    const current = viewRef.current
    const nextScale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, current.scale * factor))

    if (nextScale === current.scale) {
      return
    }

    if (!el || clientX === undefined || clientY === undefined) {
      commitView({ scale: nextScale, x: current.x, y: current.y })

      return
    }

    const rect = el.getBoundingClientRect()
    const cx = clientX - rect.left - rect.width / 2
    const cy = clientY - rect.top - rect.height / 2
    const ix = (cx - current.x) / current.scale
    const iy = (cy - current.y) / current.scale

    commitView({ scale: nextScale, x: cx - ix * nextScale, y: cy - iy * nextScale })
  }

  // 换图时回到适应视口，避免沿用上一张的缩放和平移；同时清理拖拽 ref，
  // 否则下次 pointerdown 拿到与上次同号的 pointerId 时会复用残留 drag 数据。
  useEffect(() => {
    viewRef.current = { scale: 1, x: 0, y: 0 }
    setView({ scale: 1, x: 0, y: 0 })
    dragRef.current = null
  }, [url])

  // 打开后把键盘焦点落到关闭钮：全局去掉了 focus ring，灯箱若不主动聚焦，
  // Tab 仍会先走到底层设置页，键盘用户看不到对话框已打开。
  // 卸载时把焦点还给打开前的元素，避免键盘用户掉回 body。
  // portal 异步挂载场景下 ref 可能仍为 null；聚焦失败不影响后续 Esc / 点击关闭。
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeButtonRef.current?.focus()

    return () => {
      previous?.focus()
    }
  }, [])

  const zoomAtRef = useRef(zoomAt)
  zoomAtRef.current = zoomAt

  // React 合成 wheel 在部分环境是 passive，无法 preventDefault。监听挂在整层遮罩上：
  // 灯箱打开时滚轮不得滚动底层页面；仅指针落在取景框内时才缩放。
  // overlay 在 portal 内部挂载，与 effect 同步执行；若未来改为延迟挂载需改用 ref callback。
  // 触控板两指捏合在不同浏览器派发 wheel+ctrlKey 或 GestureEvent；手机双指捏合不派发
  // wheel，pinch-to-zoom 暂未支持，见 docs/DESIGN.md §6.1。
  useEffect(() => {
    const el = overlayRef.current

    if (!el) {
      return
    }

    const onWheel = (event: WheelEvent): void => {
      event.preventDefault()

      const viewport = viewportRef.current

      if (!viewport) {
        return
      }

      const rect = viewport.getBoundingClientRect()

      const within =
        event.clientX >= rect.left &&
        event.clientX <= rect.right &&
        event.clientY >= rect.top &&
        event.clientY <= rect.bottom

      if (!within) {
        return
      }

      zoomAtRef.current(event.deltaY < 0 ? WHEEL_ZOOM_STEP : 1 / WHEEL_ZOOM_STEP, event.clientX, event.clientY)
    }

    el.addEventListener('wheel', onWheel, { passive: false })

    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const getLightboxRect = (): DOMRect => new DOMRect(0, 0, window.innerWidth, window.innerHeight)

  useInteractiveRegion('portrait-lightbox', overlayRef, getLightboxRect)

  // 灯箱挂在 bubble 阶段、不阻断冒泡——让外层的"返回上一层"也能响应 Esc。
  useEscapeKey(onClose, { capture: false, preventDefault: false, stopPropagation: false })

  if (typeof document === 'undefined') {
    return null
  }

  const zoomed = view.scale > MIN_SCALE + 0.001

  return createPortal(
    <div
      aria-label={t.ui.lightbox.backdropAria}
      aria-modal="true"
      className="fixed inset-0 z-[100] flex flex-col items-center justify-center gap-2 p-6"
      onClick={event => {
        if (event.target === event.currentTarget) {
          onClose()
        }
      }}
      ref={overlayRef}
      role="dialog"
      style={{ background: 'rgba(0,0,0,0.35)', pointerEvents: 'auto', touchAction: 'none' }}
    >
      <div
        aria-label={name}
        className={`relative max-h-[80vh] max-w-[90vw] touch-none overflow-hidden rounded-2xl ${
          zoomed ? 'cursor-grab active:cursor-grabbing' : 'cursor-zoom-in'
        }`}
        onDoubleClick={event => {
          event.stopPropagation()
          // viewRef 是事实源：state 在换图后尚未刷新时不应回退到旧 scale 计算因子。
          const currentScale = viewRef.current.scale
          const target = currentScale > MIN_SCALE + 0.001 ? MIN_SCALE : DOUBLE_CLICK_SCALE
          zoomAt(target / currentScale, event.clientX, event.clientY)
        }}
        onLostPointerCapture={() => {
          dragRef.current = null
        }}
        onPointerCancel={() => {
          dragRef.current = null
        }}
        onPointerDown={event => {
          if (event.button !== 0) {
            return
          }

          event.currentTarget.setPointerCapture(event.pointerId)
          dragRef.current = {
            originX: viewRef.current.x,
            originY: viewRef.current.y,
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY
          }
        }}
        onPointerMove={event => {
          const drag = dragRef.current

          if (!drag || drag.pointerId !== event.pointerId) {
            return
          }

          commitView({
            scale: viewRef.current.scale,
            x: drag.originX + (event.clientX - drag.startX),
            y: drag.originY + (event.clientY - drag.startY)
          })
        }}
        onPointerUp={event => {
          const drag = dragRef.current

          if (drag && drag.pointerId === event.pointerId) {
            dragRef.current = null

            if (event.currentTarget.hasPointerCapture(event.pointerId)) {
              event.currentTarget.releasePointerCapture(event.pointerId)
            }
          }
        }}
        ref={viewportRef}
      >
        <img
          alt={name}
          className="block max-h-[80vh] max-w-[90vw] rounded-2xl object-contain shadow-2xl select-none"
          draggable={false}
          src={url}
          style={{
            transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`,
            transformOrigin: 'center center'
          }}
        />
        {zoomed ? (
          <span className="pointer-events-none absolute right-2 bottom-2 rounded-md bg-black/60 px-1.5 py-0.5 text-[10px] text-white/90">
            {t.ui.lightbox.zoomPercent(Math.round(view.scale * 100))}
          </span>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center justify-center gap-2" onClick={event => event.stopPropagation()}>
        <button
          aria-label={t.ui.lightbox.closePreview}
          className="inline-flex h-7 items-center gap-1 rounded-lg bg-black/70 px-2 text-[11px] text-white/90 transition hover:bg-black/90 hover:text-white"
          onClick={onClose}
          ref={closeButtonRef}
          type="button"
        >
          {t.ui.lightbox.closePreview}
        </button>
        <button
          aria-label={t.ui.lightbox.zoomOut}
          className="inline-flex h-7 items-center gap-1 rounded-lg bg-black/70 px-2 text-[11px] text-white/90 transition hover:bg-black/90 hover:text-white disabled:opacity-40"
          disabled={view.scale <= MIN_SCALE + 0.001}
          onClick={() => zoomAt(1 / WHEEL_ZOOM_STEP)}
          type="button"
        >
          −
        </button>
        <span className="min-w-[3rem] text-center text-[11px] text-white/85 tabular-nums">
          {t.ui.lightbox.zoomPercent(Math.round(view.scale * 100))}
        </span>
        <button
          aria-label={t.ui.lightbox.zoomIn}
          className="inline-flex h-7 items-center gap-1 rounded-lg bg-black/70 px-2 text-[11px] text-white/90 transition hover:bg-black/90 hover:text-white disabled:opacity-40"
          disabled={view.scale >= MAX_SCALE - 0.001}
          onClick={() => zoomAt(WHEEL_ZOOM_STEP)}
          type="button"
        >
          +
        </button>
        <button
          aria-label={t.ui.lightbox.zoomReset}
          className="inline-flex h-7 items-center gap-1 rounded-lg bg-black/70 px-2 text-[11px] text-white/90 transition hover:bg-black/90 hover:text-white disabled:opacity-40"
          disabled={!zoomed}
          onClick={() => commitView({ scale: MIN_SCALE, x: 0, y: 0 })}
          type="button"
        >
          {t.ui.lightbox.zoomReset}
        </button>
        <button
          className="inline-flex h-7 items-center gap-1 rounded-lg bg-black/70 px-2 text-[11px] text-white/90 transition hover:bg-black/90 hover:text-white"
          onClick={e => {
            e.stopPropagation()

            setActionError(prev => (prev === 'copy' ? null : prev))

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
                setActionError('copy')
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
            setActionError(prev => (prev === 'save' ? null : prev))
            setCopied(false)

            void (async (): Promise<void> => {
              try {
                const saveImage = window.spiritagent?.saveImage

                if (!saveImage) {
                  throw new Error('saveImage IPC unavailable')
                }

                await saveImage({ defaultName: name || undefined, url })
              } catch {
                setActionError('save')
              }
            })()
          }}
          type="button"
        >
          <Download className="size-3.5" />
          {t.selfSource.saveRefImage}
        </button>
      </div>
      <p className="text-[10px] text-white/55">{t.ui.lightbox.zoomHint}</p>
      {copied && (
        <p className="text-xs text-white/80" role="status">
          {t.selfSource.copiedRefImage}
        </p>
      )}
      {actionError ? (
        <p className="text-xs text-rose-300" role="alert">
          {actionError === 'copy' ? t.selfSource.copyRefImageFailed : t.selfSource.saveRefImageFailed}
        </p>
      ) : null}
    </div>,
    document.body
  )
}
