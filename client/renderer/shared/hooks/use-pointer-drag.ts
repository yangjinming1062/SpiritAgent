import { type PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react'

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
  const [delta, setDelta] = useState<PointerDragDelta>({ dx: 0, dy: 0 })

  const dragRef = useRef<{
    active: boolean
    dx: number
    dy: number
    startX: number
    startY: number
    thresholdMet: boolean
  } | null>(null)

  // onCommit 通过 ref 转发，避免 listener 闭包每次 render 重建时被换成旧引用。
  const onCommitRef = useRef(onCommit)
  onCommitRef.current = onCommit
  const thresholdRef = useRef(threshold)
  thresholdRef.current = threshold
  const detachRef = useRef<null | (() => void)>(null)

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

    dragRef.current = {
      active: true,
      dx: 0,
      dy: 0,
      startX: e.clientX,
      startY: e.clientY,
      thresholdMet: thresholdRef.current === 0
    }

    const onMoveListener = (ev: PointerEvent): void => {
      const s = dragRef.current

      if (!s) {
        return
      }

      s.dx = ev.clientX - s.startX
      s.dy = ev.clientY - s.startY

      if (!s.thresholdMet) {
        if (Math.hypot(s.dx, s.dy) < thresholdRef.current) {
          return
        }

        s.thresholdMet = true
      }

      setDelta({ dx: s.dx, dy: s.dy })
    }

    const detach = (): void => {
      window.removeEventListener('pointermove', onMoveListener)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      detachRef.current = null
    }

    const onUp = (): void => {
      const s = dragRef.current

      if (s) {
        onCommitRef.current?.({ dx: s.dx, dy: s.dy })
      }

      dragRef.current = null
      setDelta({ dx: 0, dy: 0 })
      detach()
    }

    detachRef.current?.()
    detachRef.current = detach
    window.addEventListener('pointermove', onMoveListener)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
  }

  return { delta, onPointerDown }
}
