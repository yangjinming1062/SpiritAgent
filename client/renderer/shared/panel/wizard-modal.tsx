import type { ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'

import { useEscapeKey } from '@/shared/hooks/use-escape-key'
import { usePointerDrag } from '@/shared/hooks/use-pointer-drag'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'

import { PanelHeader } from './components'
import { SURFACE_OVERLAY } from './palette'

interface WizardModalProps {
  regionId: string
  title: ReactNode
  onClose: () => void
  children: ReactNode
  // 固定在底部的操作条（上一步/下一步等），不随正文滚动。
  footer?: ReactNode
  // 内部还有自己的 Esc 语义（如灯箱打开时不关向导）时关闭默认 Esc 处理。
  escClose?: boolean
  // 可选标题栏拖拽；默认关闭以保持现有向导的居中行为。
  draggable?: boolean
  // 可选点击卡片外关闭；默认关闭，避免误触关闭已有向导。
  backdropClose?: boolean
  // 提交或上传期间禁用标题栏关闭按钮。
  closeDisabled?: boolean
  widthClass?: string
}

// 桌面内部窗口会限制 fixed 浮层的范围；隐藏时退出命中，避免捕获窗口外的点击。
const overlayRect = (element: HTMLElement): DOMRect | null =>
  element.getClientRects().length ? element.getBoundingClientRect() : null

// 线性向导的模态外壳：居中浮层卡（不铺全屏暗化遮罩）。Esc 在捕获阶段拦截并阻断传播，所在页面的 Esc 处理不会同时触发。窗口拖拽区不受 DOM 叠放遮挡，外层须设 no-drag。
export function WizardModal({
  regionId,
  title,
  onClose,
  children,
  footer,
  escClose = true,
  draggable = false,
  backdropClose = false,
  closeDisabled = false,
  widthClass = 'max-w-md'
}: WizardModalProps): React.JSX.Element {
  const overlayRef = useRef<HTMLDivElement>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const [dragOffset, setDragOffset] = useState({ dx: 0, dy: 0 })
  const dragOffsetRef = useRef(dragOffset)
  dragOffsetRef.current = dragOffset

  const clampOffset = (
    offset: { dx: number; dy: number },
    renderedOffset: { dx: number; dy: number } = dragOffsetRef.current
  ): { dx: number; dy: number } => {
    const card = cardRef.current
    const overlay = overlayRef.current
    const bounds = overlay ? overlayRect(overlay) : null

    if (!card || !bounds) {
      return offset
    }

    const rect = card.getBoundingClientRect()
    const baseLeft = rect.left - renderedOffset.dx
    const baseTop = rect.top - renderedOffset.dy
    const margin = 12

    return {
      dx: Math.min(Math.max(offset.dx, bounds.left + margin - baseLeft), bounds.right - margin - rect.width - baseLeft),
      dy: Math.min(Math.max(offset.dy, bounds.top + margin - baseTop), bounds.bottom - margin - rect.height - baseTop)
    }
  }

  const { delta: dragDelta, onPointerDown: onHeaderPointerDown } = usePointerDrag({
    threshold: 4,
    onCommit: delta => {
      setDragOffset(previous =>
        clampOffset(
          { dx: previous.dx + delta.dx, dy: previous.dy + delta.dy },
          { dx: previous.dx + dragDelta.dx, dy: previous.dy + dragDelta.dy }
        )
      )
    }
  })

  useEffect(() => {
    if (!draggable) {
      return
    }

    const constrain = (): void => {
      setDragOffset(previous => {
        const next = clampOffset(previous)

        return next.dx === previous.dx && next.dy === previous.dy ? previous : next
      })
    }

    const observer = new ResizeObserver(constrain)

    if (overlayRef.current) {
      observer.observe(overlayRef.current)
    }

    return () => observer.disconnect()
  }, [draggable])

  useInteractiveRegion(regionId, overlayRef, overlayRect)

  useEscapeKey(onClose, { enabled: escClose })

  return (
    <div
      aria-label={typeof title === 'string' ? title : undefined}
      aria-modal="true"
      className="pointer-events-auto fixed inset-0 z-[60] flex items-center justify-center px-6 py-6 [-webkit-app-region:no-drag]"
      onMouseDown={event => {
        if (backdropClose && event.target === event.currentTarget) {
          onClose()
        }
      }}
      ref={overlayRef}
      role="dialog"
    >
      <div
        className={`flex max-h-[min(85vh,100%)] w-full ${widthClass} flex-col overflow-hidden rounded-2xl text-strong ${SURFACE_OVERLAY}`}
        ref={cardRef}
        style={
          draggable
            ? {
                transform: `translate3d(${dragOffset.dx + dragDelta.dx}px, ${dragOffset.dy + dragDelta.dy}px, 0)`
              }
            : undefined
        }
      >
        <PanelHeader
          closeDisabled={closeDisabled}
          draggable={draggable}
          onClose={onClose}
          onPointerDown={draggable ? onHeaderPointerDown : undefined}
          title={title}
        />
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">{children}</div>
        {footer && <div className="flex items-center gap-2 border-t border-line-hairline px-4 py-3">{footer}</div>}
      </div>
    </div>
  )
}
