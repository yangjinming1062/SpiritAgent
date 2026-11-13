import { useCallback, useState } from 'react'

import type { AppPage } from '@ipc/contracts'

export interface DesktopRect {
  x: number
  y: number
  width: number
  height: number
}

export interface DesktopWindowState {
  id: AppPage
  bounds: DesktopRect
  minimized: boolean
  maximized: boolean
  call?: { restoreBounds: DesktopRect; restoreMaximized: boolean }
}

interface DesktopLayout {
  windows: DesktopWindowState[]
}

const CALL_WINDOW_SIZE = { width: 420, height: 760 }
export const CALL_WINDOW_MIN = { minWidth: 320, minHeight: 480 }

interface FitOptions {
  minHeight?: number
  minWidth?: number
}

export function fitRect(rect: DesktopRect, width: number, height: number, options?: FitOptions): DesktopRect {
  const minWidth = options?.minWidth ?? 560
  const minHeight = options?.minHeight ?? 360
  const w = Math.min(Math.max(minWidth, rect.width), Math.max(1, width))
  const h = Math.min(Math.max(minHeight, rect.height), Math.max(1, height))

  return {
    width: w,
    height: h,
    x: Math.max(0, Math.min(rect.x, width - w)),
    y: Math.max(0, Math.min(rect.y, height - h))
  }
}

export function useDesktopLayout() {
  // 启动不恢复上次打开的窗口：唯一内置窗口只在本次会话内有效，不跨启动落盘。
  const [layout, setLayout] = useState<DesktopLayout>(() => ({ windows: [] }))

  const activate = useCallback((id: AppPage): void => {
    setLayout(current => {
      const currentWindow = current.windows.at(-1)

      if (currentWindow?.id === id) {
        const next = { ...currentWindow, minimized: false }

        if (current.windows.length === 1 && !currentWindow.minimized) {
          return current
        }

        return { ...current, windows: [next] }
      }

      // 切页按关闭旧窗口处理；通话接管中的竖版尺寸不能带给下一个页面。
      const restore = currentWindow?.call

      const next = {
        id,
        bounds: restore?.restoreBounds ??
          currentWindow?.bounds ?? {
            x: 30,
            y: 24,
            width: id === 'chat' ? 1120 : 840,
            height: 650
          },
        minimized: false,
        maximized: restore?.restoreMaximized ?? currentWindow?.maximized ?? false
      }

      return { ...current, windows: [next] }
    })
  }, [])

  // 通话期间把当前聊天内窗临时改成竖版；恢复信息随窗口状态保存，避免泄漏给其他页面。
  const enterCall = useCallback((id: AppPage, area: { height: number; width: number }): void => {
    setLayout(current => {
      const currentWindow = current.windows.at(-1)

      if (!currentWindow || currentWindow.id !== id || currentWindow.call) {
        return current
      }

      const portrait = fitRect(
        { x: 0, y: 0, width: CALL_WINDOW_SIZE.width, height: CALL_WINDOW_SIZE.height },
        area.width,
        area.height,
        CALL_WINDOW_MIN
      )

      const bounds = {
        ...portrait,
        x: Math.max(0, Math.round((area.width - portrait.width) / 2)),
        y: Math.max(0, Math.round((area.height - portrait.height) / 2))
      }

      return {
        ...current,
        windows: [
          {
            ...currentWindow,
            bounds,
            maximized: false,
            call: { restoreBounds: currentWindow.bounds, restoreMaximized: currentWindow.maximized }
          }
        ]
      }
    })
  }, [])

  const exitCall = useCallback((id: AppPage): void => {
    setLayout(current => ({
      ...current,
      windows: current.windows.map(item =>
        item.id === id && item.call
          ? { ...item, bounds: item.call.restoreBounds, maximized: item.call.restoreMaximized, call: undefined }
          : item
      )
    }))
  }, [])

  const updateWindow = (id: AppPage, patch: Partial<DesktopWindowState>): void =>
    setLayout(current => ({
      ...current,
      windows: current.windows.map(item => (item.id === id ? { ...item, ...patch, id } : item))
    }))

  const close = (id: AppPage): void =>
    setLayout(current => ({
      ...current,
      windows: current.windows.filter(item => item.id !== id)
    }))

  return { layout, activate, enterCall, exitCall, updateWindow, close }
}
