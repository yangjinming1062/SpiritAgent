import { clamp } from '@runtime'
import { type ReactNode, type RefObject, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { Copy, Download } from '@/shared/lib/icons'
import { useStrings } from '@/shared/strings'

import { usePanelActivity } from '../context/panel-activity'
import { useEscapeKey } from '../hooks/use-escape-key'
import { useImageActions } from '../hooks/use-image-actions'
import { useLatestRef } from '../hooks/use-latest-ref'
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
  const scale = clamp(view.scale, MIN_SCALE, MAX_SCALE)
  const maxX = Math.max(0, ((scale - 1) * viewport.width) / 2)
  const maxY = Math.max(0, ((scale - 1) * viewport.height) / 2)

  return {
    scale,
    x: clamp(view.x, -maxX, maxX),
    y: clamp(view.y, -maxY, maxY)
  }
}

// 未指定容器时经 Portal 挂到 body，避免 onboarding 的 backdrop-filter 限制 fixed 定位；遮罩设 no-drag，保证标题栏上方也能点击关闭。
export function PortraitLightbox({
  children,
  containerRef,
  name,
  onClose,
  url,
  windowId
}: {
  children?: ReactNode
  containerRef?: RefObject<HTMLElement | null>
  name: string
  onClose: () => void
  url: string
  windowId?: number
}): React.ReactPortal | null {
  const t = useStrings()
  const panelActive = usePanelActivity()
  const panelActiveRef = useRef(panelActive)
  const regionId = useId()
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
  const { copied, copy, error, save } = useImageActions()

  useLayoutEffect(() => {
    panelActiveRef.current = panelActive
  }, [panelActive])

  useEffect(() => {
    if (panelActive) {
      return
    }

    const pointer = dragRef.current
    dragRef.current = null

    if (pointer && viewportRef.current?.hasPointerCapture(pointer.pointerId)) {
      viewportRef.current.releasePointerCapture(pointer.pointerId)
    }

    overlayRef.current?.querySelectorAll<HTMLMediaElement>('audio, video').forEach(media => media.pause())
  }, [panelActive])

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
    const nextScale = clamp(current.scale * factor, MIN_SCALE, MAX_SCALE)

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

  // 换图时回到适应视口并清理拖拽 ref，否则下次 pointerdown 拿到同号 pointerId 时会复用残留 drag 数据。
  useEffect(() => {
    viewRef.current = { scale: 1, x: 0, y: 0 }
    setView({ scale: 1, x: 0, y: 0 })
    dragRef.current = null
  }, [url])

  // 打开后把键盘焦点落到关闭钮（全局去掉了 focus ring，否则 Tab 会先走到底层设置页）；卸载时把焦点还给打开前的元素。portal 异步挂载时 ref 可能仍为 null，聚焦失败不影响后续 Esc/点击关闭。
  useEffect(() => {
    if (!panelActive) {
      return
    }

    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeButtonRef.current?.focus()

    return () => {
      if (panelActiveRef.current && previous?.isConnected) {
        previous.focus()
      }
    }
  }, [panelActive])

  const zoomAtRef = useLatestRef(zoomAt)

  // 用原生非 passive 监听：React 合成 wheel 可能是 passive，无法 preventDefault。监听挂在整层遮罩上，灯箱打开时滚轮不滚动底层页面，仅指针在取景框内才缩放；遮罩随组件同步挂载，effect 内可直接取到。
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
  }, [zoomAtRef])

  const getLightboxRect = (el: HTMLElement): DOMRect | null => (panelActive ? el.getBoundingClientRect() : null)

  useInteractiveRegion(`portrait-lightbox:${regionId}`, overlayRef, getLightboxRect, undefined, windowId)

  // 灯箱挂在 bubble 阶段、不阻断冒泡——让外层的"返回上一层"也能响应 Esc。
  useEscapeKey(onClose, { capture: false, preventDefault: false, stopPropagation: false })

  if (typeof document === 'undefined') {
    return null
  }

  const zoomed = view.scale > MIN_SCALE + 0.001

  // 取景框尽量吃满整窗暗底，工具条与提示叠在下方，不再把预览收成小卡片。
  const imageLimits = containerRef
    ? children
      ? 'max-h-[calc(100cqh-9rem)] max-w-[96cqw]'
      : 'max-h-[calc(100cqh-5.5rem)] max-w-[96cqw]'
    : children
      ? 'max-h-[calc(100vh-9rem)] max-w-[96vw]'
      : 'max-h-[calc(100vh-6.5rem)] max-w-[96vw]'

  return createPortal(
    <div
      aria-label={t.ui.lightbox.backdropAria}
      aria-modal="true"
      className={`${containerRef ? 'absolute' : 'fixed'} inset-0 z-[100] flex flex-col items-center [-webkit-app-region:no-drag]`}
      onClick={event => {
        if (event.target === event.currentTarget) {
          onClose()
        }
      }}
      ref={overlayRef}
      role="dialog"
      style={{
        display: panelActive ? undefined : 'none',
        background: 'rgba(0,0,0,0.88)',
        containerType: containerRef ? 'size' : undefined,
        pointerEvents: 'auto',
        touchAction: 'none'
      }}
    >
      <div
        className="flex min-h-0 w-full flex-1 items-center justify-center p-3"
        onClick={event => {
          if (event.target === event.currentTarget) {
            onClose()
          }
        }}
      >
        <div
          aria-label={name}
          className={`relative ${imageLimits} touch-none overflow-hidden ${
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
            className={`block ${imageLimits} object-contain select-none`}
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
      </div>

      {children ? (
        <div className="w-full max-w-md px-3 pb-1" onClick={event => event.stopPropagation()}>
          {children}
        </div>
      ) : null}

      <div
        className="mb-1 flex flex-wrap items-center justify-center gap-1 rounded-full bg-white/15 px-2 py-1.5 backdrop-blur-md"
        onClick={event => event.stopPropagation()}
      >
        <button
          aria-label={t.ui.lightbox.closePreview}
          className="inline-flex h-7 items-center gap-1 rounded-full px-2.5 text-[11px] text-white/90 transition hover:bg-white/25 hover:text-white"
          onClick={onClose}
          ref={closeButtonRef}
          type="button"
        >
          {t.ui.lightbox.closePreview}
        </button>
        <button
          aria-label={t.ui.lightbox.zoomOut}
          className="inline-flex h-7 items-center gap-1 rounded-full px-2.5 text-[11px] text-white/90 transition hover:bg-white/25 hover:text-white disabled:opacity-40"
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
          className="inline-flex h-7 items-center gap-1 rounded-full px-2.5 text-[11px] text-white/90 transition hover:bg-white/25 hover:text-white disabled:opacity-40"
          disabled={view.scale >= MAX_SCALE - 0.001}
          onClick={() => zoomAt(WHEEL_ZOOM_STEP)}
          type="button"
        >
          +
        </button>
        <button
          aria-label={t.ui.lightbox.zoomReset}
          className="inline-flex h-7 items-center gap-1 rounded-full px-2.5 text-[11px] text-white/90 transition hover:bg-white/25 hover:text-white disabled:opacity-40"
          disabled={!zoomed}
          onClick={() => commitView({ scale: MIN_SCALE, x: 0, y: 0 })}
          type="button"
        >
          {t.ui.lightbox.zoomReset}
        </button>
        <button
          className="inline-flex h-7 items-center gap-1 rounded-full px-2.5 text-[11px] text-white/90 transition hover:bg-white/25 hover:text-white"
          onClick={e => {
            e.stopPropagation()
            void copy(url)
          }}
          type="button"
        >
          <Copy className="size-3.5" />
          {t.selfSource.copyRefImage}
        </button>
        <button
          className="inline-flex h-7 items-center gap-1 rounded-full px-2.5 text-[11px] text-white/90 transition hover:bg-white/25 hover:text-white"
          onClick={e => {
            e.stopPropagation()
            void save(url, name)
          }}
          type="button"
        >
          <Download className="size-3.5" />
          {t.selfSource.saveRefImage}
        </button>
      </div>
      <p className="mb-2 text-[10px] text-white/50">{t.ui.lightbox.zoomHint}</p>
      {copied && (
        <p className="mb-2 text-xs text-white/80" role="status">
          {t.selfSource.copiedRefImage}
        </p>
      )}
      {error ? (
        <p className="mb-2 text-xs text-rose-300" role="alert">
          {error === 'copy' ? t.selfSource.copyRefImageFailed : t.selfSource.saveRefImageFailed}
        </p>
      ) : null}
    </div>,
    containerRef?.current ?? document.body
  )
}
