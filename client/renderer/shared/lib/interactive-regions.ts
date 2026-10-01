import { createContext, type RefObject, useContext, useEffect, useRef } from 'react'

import { log } from '@/shared/lib/log'

// 当前窗口的鼠标捕获 ID：精灵窗取默认 0，完整入口在渲染根部提供 1，经 Portal 挂载的弹层同样继承。
export const CaptureWindowIdContext = createContext<number>(0)

export type InteractiveRegion = {
  getRect: () => DOMRect | null
  hitTest?: (x: number, y: number) => boolean
  id: string
}

interface GlobalInteractiveState {
  captureHoldsByWindow: Map<number, Set<symbol>>
  lastPointsByWindow: Map<number, { x: number; y: number }>
  probesByWindow: Map<number, () => void>
  regionsByWindow: Map<number, Map<string, InteractiveRegion>>
  releaseTimers: Map<number, ReturnType<typeof setTimeout>>
}

declare global {
  var __spiritagent_interactive_state__: GlobalInteractiveState | undefined
}

const state = (globalThis.__spiritagent_interactive_state__ ??= {
  captureHoldsByWindow: new Map(),
  lastPointsByWindow: new Map(),
  probesByWindow: new Map(),
  regionsByWindow: new Map(),
  releaseTimers: new Map()
})

/** 手势期间保持窗口接收鼠标；返回的释放函数可重复调用。 */
export function holdWindowMouseCapture(windowId = 0): () => void {
  const holds = state.captureHoldsByWindow.get(windowId) ?? new Set<symbol>()
  state.captureHoldsByWindow.set(windowId, holds)
  const token = Symbol()
  holds.add(token)
  probeInteractiveRegions(windowId)

  return () => {
    if (holds.delete(token)) {
      probeInteractiveRegions(windowId)
    }
  }
}

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
  probeInteractiveRegions(windowId)
}

function unregisterInteractiveRegion(id: string, windowId: number = 0): void {
  const m = bucket(windowId)

  if (!m.delete(id)) {
    return
  }

  probeInteractiveRegions(windowId)
}

function setCaptureProbe(fn: (() => void) | null, windowId: number = 0): void {
  if (fn === null) {
    state.probesByWindow.delete(windowId)
  } else {
    state.probesByWindow.set(windowId, fn)
  }
}

/** Re-run the window's capture probe outside the mousemove path — e.g. an async hit refinement just landed for a stationary cursor. */
export function probeInteractiveRegions(windowId?: number): void {
  if (windowId !== undefined) {
    state.probesByWindow.get(windowId)?.()
  } else {
    for (const probe of state.probesByWindow.values()) {
      probe()
    }
  }
}

function hitRegion(region: InteractiveRegion | undefined, x: number, y: number): boolean {
  const rect = region?.getRect()

  return Boolean(
    rect && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom && region?.hitTest?.(x, y) !== false
  )
}

function isPointInteractive(x: number, y: number, windowId: number = 0): boolean {
  for (const region of bucket(windowId).values()) {
    if (hitRegion(region, x, y)) {
      return true
    }
  }

  return false
}

export function isRegionHit(id: string, x: number, y: number, windowId: number = 0): boolean {
  return hitRegion(bucket(windowId).get(id), x, y)
}

const defaultGetRect = (el: HTMLElement): DOMRect | null => el.getBoundingClientRect()

// 通过 ref 获取可见矩形注册交互区域；返回 null 表示该帧退出交互。未传 windowId 时登记到 CaptureWindowIdContext 给出的当前窗口捕获 ID。
export function useInteractiveRegion(
  id: string,
  ref: RefObject<HTMLElement | null>,
  getRect: (el: HTMLElement) => DOMRect | null = defaultGetRect,
  hitTest?: (x: number, y: number) => boolean,
  windowId?: number
): void {
  const contextWindowId = useContext(CaptureWindowIdContext)
  const regionWindowId = windowId ?? contextWindowId
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
      regionWindowId,
      (x, y) => (hitTestRef.current ? hitTestRef.current(x, y) : true)
    )

    return () => unregisterInteractiveRegion(id, regionWindowId)
  }, [id, ref, regionWindowId])

  useEffect(() => {
    probeInteractiveRegions(regionWindowId)
  }, [hitTest, regionWindowId])
}

export interface WindowMouseCaptureOptions {
  setIgnoreMouseEvents?: (payload: { forward?: boolean; ignore: boolean }) => Promise<void>
}

// 状态与定时器挂在 globalThis 并在卸载时取消，避免 HMR 重载期间遗留定时器把窗口置为 ignore。
export function useWindowMouseCapture(windowId: number = 0, options?: WindowMouseCaptureOptions): void {
  const setIgnoreFnRef = useRef(options?.setIgnoreMouseEvents)
  setIgnoreFnRef.current = options?.setIgnoreMouseEvents

  useEffect(() => {
    let requestedIgnore: boolean | undefined
    let latestRequest: Promise<void> | undefined

    const setIgnoreMouseEvents = (ignore: boolean) => {
      if (requestedIgnore === ignore) {
        return
      }

      const payload = { forward: ignore, ignore }
      const customFn = setIgnoreFnRef.current
      const request = customFn ? customFn(payload) : window.spiritagent?.sprite?.setIgnoreMouseEvents?.(payload)

      if (request === undefined) {
        return
      }

      requestedIgnore = ignore
      latestRequest = request
      void request.catch(error => {
        // 失败后允许下次探测重试；旧请求失败不能作废较新的设置。
        if (latestRequest === request) {
          requestedIgnore = undefined
        }

        log.warn('interactive-regions', 'setIgnoreMouseEvents failed', error)
      })
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

      if (requestedIgnore === true) {
        return
      }

      const timer = setTimeout(() => {
        state.releaseTimers.delete(windowId)

        if (!state.captureHoldsByWindow.get(windowId)?.size) {
          setIgnoreMouseEvents(true)
        }
      }, 100)

      state.releaseTimers.set(windowId, timer)
    }

    const probe = () => {
      if (state.captureHoldsByWindow.get(windowId)?.size) {
        captureImmediate()

        return
      }

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

      probe()
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
      setIgnoreMouseEvents(false)
    }
  }, [windowId])
}
