import { useCallback, useEffect, useRef, useState } from 'react'

import {
  accountStorageKey,
  currentClearEpoch,
  persistString,
  registerCompanionStorageKey,
  storedJson
} from '@/shared/lib/storage'

export const DESKTOP_APPS = [
  'chat',
  'posts',
  'diary',
  'scene',
  'appearance',
  'desktopLife',
  'remote',
  'settings'
] as const

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

const LAYOUT_KEY = registerCompanionStorageKey('da.desktop.layout')

function validRect(value: unknown): value is DesktopRect {
  if (!value || typeof value !== 'object') {
    return false
  }

  const rect = value as Record<string, unknown>

  return (
    ['x', 'y', 'width', 'height'].every(key => typeof rect[key] === 'number' && Number.isFinite(rect[key])) &&
    Number(rect.width) > 0 &&
    Number(rect.height) > 0
  )
}

function isLayout(value: unknown): value is DesktopLayout {
  if (!value || typeof value !== 'object') {
    return false
  }

  const layout = value as Partial<DesktopLayout>

  return (
    Array.isArray(layout.windows) &&
    layout.windows.length <= DESKTOP_APPS.length &&
    layout.windows.every(
      item =>
        item &&
        typeof item === 'object' &&
        DESKTOP_APPS.includes(item.id) &&
        validRect(item.bounds) &&
        typeof item.minimized === 'boolean' &&
        typeof item.maximized === 'boolean'
    ) &&
    new Set(layout.windows.map(item => item.id)).size === layout.windows.length &&
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
  const [layout, setLayout] = useState<DesktopLayout>(() =>
    storedJson(
      LAYOUT_KEY,
      {
        windows: [],
        whisperOpen: true,
        whisperSide: 'right'
      },
      isLayout
    )
  )

  const scope = useRef({ epoch: currentClearEpoch(), key: accountStorageKey(LAYOUT_KEY) })
  const latest = useRef(layout)

  const persist = useCallback((): void => {
    if (scope.current.epoch === currentClearEpoch() && scope.current.key === accountStorageKey(LAYOUT_KEY)) {
      persistString(LAYOUT_KEY, JSON.stringify(latest.current))
    }
  }, [])

  useEffect(() => {
    latest.current = layout
    const timer = window.setTimeout(persist, 200)

    return () => window.clearTimeout(timer)
  }, [layout, persist])

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
