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
}

interface DesktopLayout {
  windows: DesktopWindowState[]
}

export function fitRect(rect: DesktopRect, width: number, height: number): DesktopRect {
  const w = Math.min(Math.max(560, rect.width), Math.max(1, width))
  const h = Math.min(Math.max(360, rect.height), Math.max(1, height))

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

      // 切页按关闭旧窗口处理，但沿用当前窗口的几何状态，避免页面切换时跳动。
      const next = {
        id,
        bounds: currentWindow?.bounds ?? {
          x: 30,
          y: 24,
          width: id === 'chat' ? 1120 : 840,
          height: 650
        },
        minimized: false,
        maximized: currentWindow?.maximized ?? false
      }

      return { ...current, windows: [next] }
    })
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

  return { layout, activate, updateWindow, close }
}
