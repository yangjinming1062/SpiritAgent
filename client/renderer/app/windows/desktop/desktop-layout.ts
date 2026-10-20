import { useCallback, useEffect, useRef, useState } from 'react'

import {
  accountStorageKey,
  currentClearEpoch,
  persistString,
  registerCompanionStorageKey,
  storedJson
} from '@/shared/lib/storage'
import { APP_PAGES } from '@ipc/contracts'

export const DESKTOP_APPS = APP_PAGES

export type DesktopApp = (typeof DESKTOP_APPS)[number]

export interface DesktopRect {
  x: number
  y: number
  width: number
  height: number
}

export interface DesktopWindowState {
  id: DesktopApp
  bounds: DesktopRect
  minimized: boolean
  maximized: boolean
}

interface DesktopLayout {
  windows: DesktopWindowState[]
  whisperOpen: boolean
  whisperSide: 'left' | 'right'
}

/** 落盘只保留轻语状态；内部窗口集合仅在本次会话有效，不跨启动恢复。 */
interface StoredLayout {
  whisperOpen: boolean
  whisperSide: 'left' | 'right'
}

const LAYOUT_KEY = registerCompanionStorageKey('da.desktop.layout.v2')

const DEFAULT_STORED_LAYOUT: StoredLayout = { whisperOpen: true, whisperSide: 'right' }

// 只认轻语状态这一种结构：带窗口列表的记录按无效处理，不做字段迁移。
function isStoredLayout(value: unknown): value is StoredLayout {
  if (!value || typeof value !== 'object') {
    return false
  }

  const layout = value as Record<string, unknown>

  return (
    Object.keys(layout).length === 2 &&
    typeof layout.whisperOpen === 'boolean' &&
    (layout.whisperSide === 'left' || layout.whisperSide === 'right')
  )
}

export function fitRect(
  rect: DesktopRect,
  width: number,
  height: number,
  minWidth = 560,
  minHeight = 360
): DesktopRect {
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
  // 启动不恢复上次打开的窗口：窗口集合只在本次会话内有效。
  const [layout, setLayout] = useState<DesktopLayout>(() => ({
    windows: [],
    ...storedJson(LAYOUT_KEY, DEFAULT_STORED_LAYOUT, isStoredLayout)
  }))

  const scope = useRef({ epoch: currentClearEpoch(), key: accountStorageKey(LAYOUT_KEY) })
  const latest = useRef(layout)

  const persist = useCallback((): void => {
    if (scope.current.epoch === currentClearEpoch() && scope.current.key === accountStorageKey(LAYOUT_KEY)) {
      const { whisperOpen, whisperSide } = latest.current

      persistString(LAYOUT_KEY, JSON.stringify({ whisperOpen, whisperSide }))
    }
  }, [])

  useEffect(() => {
    latest.current = layout
  }, [layout])

  // 只有轻语状态跨启动保留，落盘时机随之收敛到轻语状态变化。
  useEffect(() => {
    const timer = window.setTimeout(persist, 200)

    return () => window.clearTimeout(timer)
  }, [layout.whisperOpen, layout.whisperSide, persist])

  useEffect(() => {
    window.addEventListener('pagehide', persist)

    return () => {
      window.removeEventListener('pagehide', persist)
      persist()
    }
  }, [persist])

  const activate = useCallback((id: DesktopApp): void => {
    setLayout(current => {
      const last = current.windows.at(-1)

      if (last?.id === id && !last.minimized) {
        return current
      }

      const existing = current.windows.find(item => item.id === id)

      const next = existing
        ? { ...existing, minimized: false }
        : {
            id,
            bounds: {
              x: 30 + current.windows.length * 18,
              y: 24 + current.windows.length * 18,
              width: id === 'chat' ? 1120 : 840,
              height: 650
            },
            minimized: false,
            maximized: false
          }

      // 激活对话窗口即收起轻语（新开、恢复与重新置前共用本分支）；已置前的提前返回不打扰手动展开的并存状态。
      return {
        ...current,
        windows: [...current.windows.filter(item => item.id !== id), next],
        whisperOpen: id === 'chat' ? false : current.whisperOpen
      }
    })
  }, [])

  const updateWindow = (id: DesktopApp, patch: Partial<DesktopWindowState>): void =>
    setLayout(current => ({
      ...current,
      windows: current.windows.map(item => (item.id === id ? { ...item, ...patch, id } : item))
    }))

  const close = (id: DesktopApp): void =>
    setLayout(current => ({
      ...current,
      windows: current.windows.filter(item => item.id !== id)
    }))

  return { layout, setLayout, activate, updateWindow, close }
}
