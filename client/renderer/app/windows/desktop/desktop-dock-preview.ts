import { useCallback, useEffect, useLayoutEffect, useRef } from 'react'

import { shellApi } from '@/shared/platform/desktop'
import type { DockPreviewRequest } from '@client-shared/ipc/contracts'

let nextRequestId = 0

type PreviewAnchor = NonNullable<DockPreviewRequest['layout']>['anchor']

type OpenPreview = {
  epoch: number
  entryId: string
  requestId: number
  button: HTMLButtonElement
  anchor: PreviewAnchor
}

function visibleAnchor(button: HTMLButtonElement): PreviewAnchor | null {
  const viewport = button.closest<HTMLElement>('[data-dock-viewport]')

  if (!button.isConnected || !viewport) {
    return null
  }

  const bounds = button.getBoundingClientRect()
  const clip = viewport.getBoundingClientRect()
  const x = Math.max(bounds.left, clip.left + viewport.clientLeft, 0)
  const y = Math.max(bounds.top, clip.top + viewport.clientTop, 0)
  const right = Math.min(bounds.right, clip.left + viewport.clientLeft + viewport.clientWidth, window.innerWidth)
  const bottom = Math.min(bounds.bottom, clip.top + viewport.clientTop + viewport.clientHeight, window.innerHeight)

  return right > x && bottom > y ? { x, y, width: right - x, height: bottom - y } : null
}

function setPreview(request: DockPreviewRequest): void {
  void shellApi()
    .dock.setPreview(request)
    .catch(error => console.warn('[Dock] window preview:', error))
}

export function useDesktopDockPreview(
  enabled: boolean,
  epoch: number,
  onActivate: (entryId: string, windowId: string, requestId: number) => void
) {
  const timer = useRef<number | null>(null)
  const open = useRef<OpenPreview | null>(null)
  const activate = useRef(onActivate)
  activate.current = onActivate

  const leave = useCallback((): void => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
  }, [])

  const dismiss = useCallback((): void => {
    leave()

    if (open.current) {
      setPreview({ epoch: open.current.epoch, requestId: ++nextRequestId, layout: null })
      open.current = null
    }
  }, [leave])

  const revalidate = useCallback((): void => {
    const preview = open.current

    if (!preview) {
      return
    }

    const current = visibleAnchor(preview.button)
    const { x, y, width, height } = preview.anchor

    if (!current || current.x !== x || current.y !== y || current.width !== width || current.height !== height) {
      dismiss()
    }
  }, [dismiss])

  useLayoutEffect(revalidate)

  useEffect(() => {
    if (!enabled) {
      return
    }

    const unsubscribe = shellApi().dock.onPreviewSelected(({ requestId, windowId }) => {
      revalidate()
      const preview = open.current

      if (preview?.requestId === requestId && preview.epoch === epoch) {
        open.current = null
        activate.current(preview.entryId, windowId, requestId)
      }
    })

    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        dismiss()
      }
    }

    window.addEventListener('pointerdown', dismiss, true)
    window.addEventListener('dragstart', dismiss, true)
    window.addEventListener('blur', dismiss)
    window.addEventListener('resize', dismiss)
    window.addEventListener('keydown', onKey)

    return () => {
      unsubscribe()
      window.removeEventListener('pointerdown', dismiss, true)
      window.removeEventListener('dragstart', dismiss, true)
      window.removeEventListener('blur', dismiss)
      window.removeEventListener('resize', dismiss)
      window.removeEventListener('keydown', onKey)
      dismiss()
    }
  }, [dismiss, enabled, epoch, revalidate])

  const enter = (entryId: string, button: HTMLButtonElement, running: boolean): void => {
    if (open.current?.entryId === entryId && running) {
      // 穿过浮层与图标之间的空隙后，也允许重新悬停打开已收起的原生浮层。
      leave()
    } else {
      dismiss()
    }

    if (!enabled || !running) {
      return
    }

    timer.current = window.setTimeout(() => {
      timer.current = null
      const anchor = visibleAnchor(button)

      if (!anchor) {
        return
      }

      const requestId = ++nextRequestId
      open.current = { epoch, entryId, requestId, button, anchor }
      setPreview({
        epoch,
        requestId,
        layout: { entryId, anchor, viewport: { width: window.innerWidth, height: window.innerHeight } }
      })
    }, 350)
  }

  // 浮层由原生宿主命中。离开图标只取消开场计时，已打开时等宿主判断是否移进了浮层。
  return { enter, leave, dismiss, revalidate }
}
