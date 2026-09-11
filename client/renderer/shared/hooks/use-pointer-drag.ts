import { type PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react'

export interface PointerDragDelta {
  dx: number
  dy: number
}

export interface UsePointerDragOptions {
  // 实时累计位移回调：threshold 越过后每次 move 触发。onMove 不传时，位移累计在 hook 内部的 delta 状态里。
  onMove?: (delta: PointerDragDelta) => void
  // 松手时回调：相对 pointerdown 起点的最终累计位移。
  onCommit?: (delta: PointerDragDelta) => void
  // 越过该像素阈值才认为开始拖拽；避免点击误触发。0 表示立即激活。
  threshold?: number
}

// 通用指针拖拽 hook：返回 { delta, onPointerDown }。调用方把 onPointerDown 挂到拖拽触发节点。
// - onMove 提供时 hook 不暴露 delta 内部状态（消费者用 onMove 自行管状态）；
//   不提供时 hook 内部维护 delta state，松手时回到 0。
// - onCommit 在松手时拿到最终累计位移，便于"在已有 base 上叠加"的模式：
//   视觉上 base + live delta，提交时 base += final delta。
//
// 不适用：拖拽过程需要触发额外领域逻辑（locomotion、缩放、屏幕边缘吸附等）——那种情况
// 直接挂 onPointerDown + 内部管理 listeners 更合适，参见 sprite-stage.tsx。
export function usePointerDrag(opts: UsePointerDragOptions = {}): {
  delta: PointerDragDelta
  onPointerDown: (e: ReactPointerEvent<HTMLElement>) => void
} {
  const { onCommit, onMove, threshold = 0 } = opts
  const [delta, setDelta] = useState<PointerDragDelta>({ dx: 0, dy: 0 })

  const dragRef = useRef<{
    active: boolean
    dx: number
    dy: number
    startX: number
    startY: number
    thresholdMet: boolean
  } | null>(null)

  // onCommit / onMove 通过 ref 转发，避免 listener 闭包每次 render 重建时被换成旧引用。
  const onCommitRef = useRef(onCommit)
  onCommitRef.current = onCommit
  const onMoveRef = useRef(onMove)
  onMoveRef.current = onMove
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

      if (onMoveRef.current) {
        onMoveRef.current({ dx: s.dx, dy: s.dy })
      } else {
        setDelta({ dx: s.dx, dy: s.dy })
      }
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
