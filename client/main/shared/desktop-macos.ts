import { createRequire } from 'node:module'
import path from 'node:path'

import { app } from 'electron'

import type { RunningApplicationWindow } from './desktop-applications'

export interface MacosApplicationInfo {
  path: string
  name: string
  bundleId: string | null
  icon: string | null
}

export interface DesktopMacosNative {
  configureSurfaces: (background: Buffer, overlay: Buffer) => void
  surfaceState: (bounds: Electron.Rectangle) => {
    active: boolean
    stageAvailable: boolean
    fullscreen: boolean
    spaceRevision: number
  }
  listWindows: (bounds: Electron.Rectangle) => Promise<RunningApplicationWindow[]>
  activateWindow: (id: string, bounds: Electron.Rectangle | null) => Promise<void>
  closeWindows: (ids: string[]) => Promise<void>
  applicationInfo: (paths: string[], includeIcon?: boolean) => Promise<MacosApplicationInfo[]>
  accessibilityStatus: () => boolean
  requestAccessibility: () => void
  cancelOperations: () => void
  reset: () => void
}

const requireNative = createRequire(import.meta.url)
let loaded: DesktopMacosNative | undefined
let loadError: Error | undefined

function isNativeModule(value: unknown): value is DesktopMacosNative {
  if (!value || typeof value !== 'object') {
    return false
  }

  const methods: (keyof DesktopMacosNative)[] = [
    'configureSurfaces',
    'surfaceState',
    'listWindows',
    'activateWindow',
    'closeWindows',
    'applicationInfo',
    'accessibilityStatus',
    'requestAccessibility',
    'cancelOperations',
    'reset'
  ]

  return methods.every(method => method in value && typeof Reflect.get(value, method) === 'function')
}

/** 仅可信主进程加载；Node-API 模块位于 asar 外，开发与打包使用同一架构。 */
export function desktopMacos(): DesktopMacosNative {
  if (process.platform !== 'darwin') {
    throw new Error('macOS 桌面组件只能在 macOS 加载。')
  }

  if (loadError) {
    throw loadError
  }

  if (!loaded) {
    const filename = app.isPackaged
      ? path.join(process.resourcesPath, 'desktop_macos.node')
      : path.join(app.getAppPath(), 'build', process.arch, 'desktop_macos.node')

    try {
      const module: unknown = requireNative(filename)

      if (!isNativeModule(module)) {
        throw new Error('组件接口与当前客户端不兼容。')
      }

      loaded = module
    } catch (error) {
      loadError = new Error(`macOS 桌面组件加载失败：${error instanceof Error ? error.message : String(error)}`)
      throw loadError
    }
  }

  return loaded
}

export function desktopMacosAvailability(): { available: boolean; reason: string | null } {
  try {
    desktopMacos()

    return { available: true, reason: null }
  } catch (error) {
    return { available: false, reason: error instanceof Error ? error.message : String(error) }
  }
}
