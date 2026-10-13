import { type PointerEvent as ReactPointerEvent, useContext, useEffect, useRef, useState } from 'react'

import { usePanelActivity } from '@/shared/context/panel-activity'
import { CaptureWindowIdContext, holdWindowMouseCapture } from '@/shared/lib/interactive-regions'

import { useLatestRef } from './use-latest-ref'

export interface PointerDragDelta {
  dx: number
  dy: number
}

export interface UsePointerDragOptions {
  // 松手时回调：相对 pointerdown 起点的最终累计位移。
  onCommit?: (delta: PointerDragDelta) => void
  // 越过该像素阈值才认为开始拖拽；避免点击误触发。0 表示立即激活。
  threshold?: number
}

// 通用指针拖拽 hook：返回 { delta, onPointerDown }。delta 为越过阈值后的实时累计位移（松手归 0），onCommit 松手时拿最终累计位移，便于「视觉 base+live delta、提交 base+=final」。不适用：拖拽过程需要额外领域逻辑（locomotion、缩放、屏幕边缘吸附等）——那种情况直接挂 onPointerDown + 内部管理 listeners，参见 sprite-stage.tsx。
export function usePointerDrag(opts: UsePointerDragOptions = {}): {
  delta: PointerDragDelta
  onPointerDown: (e: ReactPointerEvent<HTMLElement>) => void
} {
  const { onCommit, threshold = 0 } = opts
  const windowId = useContext(CaptureWindowIdContext)
  const panelActive = usePanelActivity()
  const [delta, setDelta] = useState<PointerDragDelta>({ dx: 0, dy: 0 })

  // listener 创建于 pointerdown，经 ref 在松手时调用最新的 onCommit。
  const onCommitRef = useLatestRef(onCommit)
  const detachRef = useRef<null | (() => void)>(null)

  useEffect(() => {
    if (!panelActive) {
      detachRef.current?.()
    }
  }, [panelActive])

  // 卸载时释放 window 级监听，避免拖拽中途 unmount 泄漏。
  useEffect(() => {
    return () => {
      detachRef.current?.()
      detachRef.current = null
    }
  }, [])

  const onPointerDown = (e: ReactPointerEvent<HTMLElement>): void => {
    // 仅响应左键，其他按钮直接吞掉。
    if (e.button !== 0) {
      return
    }

    e.preventDefault()
    e.stopPropagation()
    detachRef.current?.()

    const target = e.currentTarget
    const pointerId = e.pointerId
    target.setPointerCapture(pointerId)
    const releaseCapture = holdWindowMouseCapture(windowId)
    const startX = e.clientX
    const startY = e.clientY
    let dx = 0
    let dy = 0
    let thresholdMet = threshold === 0
    let finished = false

    const onMoveListener = (ev: PointerEvent): void => {
      if (ev.pointerId !== pointerId) {
        return
      }

      if ((ev.buttons & 1) === 0) {
        cancel()

        return
      }

      dx = ev.clientX - startX
      dy = ev.clientY - startY

      if (!thresholdMet) {
        if (Math.hypot(dx, dy) < threshold) {
          return
        }

        thresholdMet = true
      }

      setDelta({ dx, dy })
    }

    const detach = (): void => {
      if (finished) {
        return
      }

      finished = true
      window.removeEventListener('pointermove', onMoveListener)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
      window.removeEventListener('blur', cancel)
      document.removeEventListener('visibilitychange', onVisibilityChange)
      target.removeEventListener('lostpointercapture', cancel)
      detachRef.current = null

      if (target.hasPointerCapture(pointerId)) {
        target.releasePointerCapture(pointerId)
      }

      releaseCapture()
      setDelta({ dx: 0, dy: 0 })
    }

    const cancel = (): void => detach()

    const onUp = (ev: PointerEvent): void => {
      if (ev.pointerId !== pointerId) {
        return
      }

      try {
        if (thresholdMet) {
          onCommitRef.current?.({ dx: ev.clientX - startX, dy: ev.clientY - startY })
        }
      } finally {
        detach()
      }
    }

    const onCancel = (ev: PointerEvent): void => {
      if (ev.pointerId === pointerId) {
        cancel()
      }
    }

    const onVisibilityChange = (): void => {
      if (document.visibilityState === 'hidden') {
        cancel()
      }
    }

    detachRef.current = cancel
    window.addEventListener('pointermove', onMoveListener)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onCancel)
    window.addEventListener('blur', cancel)
    document.addEventListener('visibilitychange', onVisibilityChange)
    target.addEventListener('lostpointercapture', cancel)
  }

  return { delta, onPointerDown }
}
