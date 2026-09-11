import { type RefObject, useEffect, useRef } from 'react'

export type InteractiveRegion = {
  getRect: () => DOMRect | null
  hitTest?: (x: number, y: number) => boolean
  id: string
}

interface GlobalInteractiveState {
  isIgnoringByWindow: Map<number, boolean>
  lastPointsByWindow: Map<number, { x: number; y: number }>
  probesByWindow: Map<number, () => void>
  regionsByWindow: Map<number, Map<string, InteractiveRegion>>
  releaseTimers: Map<number, ReturnType<typeof setTimeout>>
}

const g = globalThis as unknown as {
  __spiritagent_interactive_state__?: GlobalInteractiveState
}

if (!g.__spiritagent_interactive_state__) {
  g.__spiritagent_interactive_state__ = {
    isIgnoringByWindow: new Map(),
    lastPointsByWindow: new Map(),
    probesByWindow: new Map(),
    regionsByWindow: new Map(),
    releaseTimers: new Map()
  }
}

const state = g.__spiritagent_interactive_state__

function bucket(windowId: number): Map<string, InteractiveRegion> {
  let m = state.regionsByWindow.get(windowId)

  if (!m) {
    m = new Map()
    state.regionsByWindow.set(windowId, m)
  }

  return m
}

function registerInteractiveRegion(
  id: string,
  getRect: () => DOMRect | null,
  windowId: number = 0,
  hitTest?: (x: number, y: number) => boolean
): void {
  const m = bucket(windowId)
  m.set(id, { getRect, hitTest, id })
  state.probesByWindow.get(windowId)?.()
}

function unregisterInteractiveRegion(id: string, windowId: number = 0): void {
  const m = bucket(windowId)

  if (!m.delete(id)) {
    return
  }

  state.probesByWindow.get(windowId)?.()
}

function setCaptureProbe(fn: (() => void) | null, windowId: number = 0): void {
  if (fn === null) {
    state.probesByWindow.delete(windowId)
  } else {
    state.probesByWindow.set(windowId, fn)
  }
}

/** Re-run the window's capture probe outside the mousemove path — e.g. an
 * async hit refinement just landed for a stationary cursor. */
export function probeInteractiveRegions(windowId?: number): void {
  if (windowId !== undefined) {
    state.probesByWindow.get(windowId)?.()
  } else {
    for (const probe of state.probesByWindow.values()) {
      probe()
    }
  }
}

function isPointInteractive(x: number, y: number, windowId: number = 0): boolean {
  const regions = bucket(windowId)

  for (const region of regions.values()) {
    const rect = region.getRect()

    if (!rect) {
      continue
    }

    if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
      // 用像素谓词细化矩形命中；缺失或非布尔值时保持纯矩形语义。
      if (region.hitTest?.(x, y) !== false) {
        return true
      }
    }
  }

  return false
}

export function isRegionHit(id: string, x: number, y: number, windowId: number = 0): boolean {
  const regions = bucket(windowId)
  const region = regions.get(id)

  if (!region) {
    return false
  }

  const rect = region.getRect()

  if (!rect) {
    return false
  }

  if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
    return region.hitTest?.(x, y) !== false
  }

  return false
}

const defaultGetRect = (el: HTMLElement): DOMRect | null => el.getBoundingClientRect()

// 通过 ref 获取可见矩形注册交互区域；返回 null 表示该帧退出交互。
export function useInteractiveRegion(
  id: string,
  ref: RefObject<HTMLElement | null>,
  getRect: (el: HTMLElement) => DOMRect | null = defaultGetRect,
  hitTest?: (x: number, y: number) => boolean,
  windowId: number = 0
): void {
  const getRectRef = useRef(getRect)
  getRectRef.current = getRect
  const hitTestRef = useRef(hitTest)
  hitTestRef.current = hitTest

  useEffect(() => {
    registerInteractiveRegion(
      id,
      () => {
        const el = ref.current

        return el ? getRectRef.current(el) : null
      },
      windowId,
      (x, y) => (hitTestRef.current ? hitTestRef.current(x, y) : true)
    )

    return () => unregisterInteractiveRegion(id, windowId)
  }, [id, ref, windowId])

  useEffect(() => {
    state.probesByWindow.get(windowId)?.()
  }, [hitTest, windowId])
}

export interface WindowMouseCaptureOptions {
  setIgnoreMouseEvents?: (payload: { forward?: boolean; ignore: boolean }) => Promise<void> | void
}

// 状态与定时器挂在 globalThis 并在卸载时取消，避免 HMR 重载期间遗留定时器把窗口置为 ignore。
export function useWindowMouseCapture(windowId: number = 0, options?: WindowMouseCaptureOptions): void {
  const setIgnoreFnRef = useRef(options?.setIgnoreMouseEvents)
  setIgnoreFnRef.current = options?.setIgnoreMouseEvents

  useEffect(() => {
    const setIgnoreMouseEvents = (ignore: boolean, forward?: boolean) => {
      try {
        state.isIgnoringByWindow.set(windowId, ignore)

        const payload = {
          forward: ignore && forward !== false,
          ignore
        }

        const customFn = setIgnoreFnRef.current

        if (customFn) {
          void customFn(payload)
        } else {
          void window.spiritagent?.sprite?.setIgnoreMouseEvents?.(payload)
        }
      } catch {
        // 忽略测试或非 Electron 环境
      }
    }

    const cancelPendingRelease = () => {
      const timer = state.releaseTimers.get(windowId)

      if (timer) {
        clearTimeout(timer)
        state.releaseTimers.delete(windowId)
      }
    }

    const captureImmediate = () => {
      cancelPendingRelease()
      setIgnoreMouseEvents(false)
    }

    const releaseDebounced = () => {
      cancelPendingRelease()

      const timer = setTimeout(() => {
        state.releaseTimers.delete(windowId)
        setIgnoreMouseEvents(true, true)
      }, 100)

      state.releaseTimers.set(windowId, timer)
    }

    const probe = () => {
      const p = state.lastPointsByWindow.get(windowId)

      if (!p) {
        return
      }

      if (isPointInteractive(p.x, p.y, windowId)) {
        captureImmediate()
      } else {
        releaseDebounced()
      }
    }

    setCaptureProbe(probe, windowId)

    const onMouseMove = (e: MouseEvent) => {
      state.lastPointsByWindow.set(windowId, { x: e.clientX, y: e.clientY })

      if (isPointInteractive(e.clientX, e.clientY, windowId)) {
        captureImmediate()
      } else {
        releaseDebounced()
      }
    }

    window.addEventListener('mousemove', onMouseMove, { passive: true })
    window.addEventListener('focus', probe)

    // 挂载时立即执行一次 probe，以便在热重启/重新挂载时恢复上一次已知鼠标位置的交互态
    probe()

    return () => {
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('focus', probe)
      cancelPendingRelease()
      setCaptureProbe(null, windowId)
      state.lastPointsByWindow.delete(windowId)
      state.isIgnoringByWindow.delete(windowId)
      setIgnoreMouseEvents(false)
    }
  }, [windowId])
}
