import {
  DEFAULT_SHORTCUTS,
  type DesktopShortcutsConfig,
  type DesktopShortcutsSetPayload,
  type DesktopShortcutsState,
  IPC,
  type SurfaceId
} from '@ipc/contracts'
import { globalShortcut, type IpcMain } from 'electron'

import * as store from '../shared/lib/runner-config-store'
import { broadcastToAllWindows, errorMessage } from '../shared/utils'

/** 窄接口：快捷键只需要切表面，不依赖 lifecycle/surfaces 具体类型。 */
interface SurfaceToggler {
  toggleSurface: (payload: { surface: SurfaceId }) => Promise<void>
}

interface ShortcutsIpcDeps {
  ipcMain: IpcMain
  rememberLog?: (chunk: string) => void
  surfaces?: SurfaceToggler
  /** 精灵窗可见则隐藏，否则显示。 */
  toggleMainWindow: () => void
}

let deps: ShortcutsIpcDeps | null = null
const currentRegistered = new Map<keyof DesktopShortcutsConfig, string>()

const idleStatus = (): DesktopShortcutsState['status'] => ({
  openLiving: { registered: false },
  openWorkbench: { registered: false },
  toggleVisibility: { registered: false }
})

let currentStatus = idleStatus()

// 订阅方是生活空间设置页，不是精灵窗。
function broadcastShortcutsChanged(state: DesktopShortcutsState): void {
  broadcastToAllWindows(IPC.event.shortcutsChanged, state)
}

// 逐键取 source 中的字符串值，缺失或类型不符时回落 fallback；source 来自配置镜像或渲染层，类型不可信。
function overlayShortcuts(source: unknown, fallback: Readonly<DesktopShortcutsConfig>): DesktopShortcutsConfig {
  const raw = (source ?? {}) as Partial<Record<keyof DesktopShortcutsConfig, unknown>>

  const pick = (action: keyof DesktopShortcutsConfig): string => {
    const value = raw[action]

    return typeof value === 'string' ? value : fallback[action]
  }

  return {
    openLiving: pick('openLiving'),
    openWorkbench: pick('openWorkbench'),
    toggleVisibility: pick('toggleVisibility')
  }
}

function readShortcutsConfig(): DesktopShortcutsConfig {
  return overlayShortcuts(store.read().shortcuts, DEFAULT_SHORTCUTS)
}

function handleToggleVisibility(): void {
  deps?.toggleMainWindow()
}

function handleToggleSurface(surface: SurfaceId): () => void {
  return () => {
    deps?.surfaces?.toggleSurface({ surface }).catch(err => {
      deps?.rememberLog?.(`[shortcuts] toggleSurface(${surface}) failed: ${errorMessage(err)}`)
    })
  }
}

function getActionHandler(action: keyof DesktopShortcutsConfig): () => void {
  if (action === 'toggleVisibility') {
    return handleToggleVisibility
  }

  if (action === 'openLiving') {
    return handleToggleSurface('living')
  }

  return handleToggleSurface('workbench')
}

function registerSingleShortcut(action: keyof DesktopShortcutsConfig, accelerator: string): void {
  const previous = currentRegistered.get(action)

  if (previous) {
    try {
      if (globalShortcut.isRegistered(previous)) {
        globalShortcut.unregister(previous)
      }
    } catch (err) {
      deps?.rememberLog?.(`[shortcuts] error unregistering "${previous}" for ${action}: ${errorMessage(err)}`)
    }

    currentRegistered.delete(action)
  }

  const trimmed = accelerator.trim()

  if (!trimmed) {
    currentStatus[action] = { registered: false }

    return
  }

  try {
    const handler = getActionHandler(action)
    const success = globalShortcut.register(trimmed, handler)

    if (!success) {
      currentStatus[action] = {
        error: '快捷键已被系统或其他应用占用',
        registered: false
      }
      deps?.rememberLog?.(`[shortcuts] failed to register "${trimmed}" for ${action}: occupied`)
    } else {
      currentRegistered.set(action, trimmed)
      currentStatus[action] = { registered: true }
    }
  } catch (err) {
    const message = errorMessage(err)
    currentStatus[action] = {
      error: message,
      registered: false
    }
    deps?.rememberLog?.(`[shortcuts] error registering "${trimmed}" for ${action}: ${message}`)
  }
}

function applyShortcuts(config: DesktopShortcutsConfig): DesktopShortcutsState {
  registerSingleShortcut('toggleVisibility', config.toggleVisibility)
  registerSingleShortcut('openLiving', config.openLiving)
  registerSingleShortcut('openWorkbench', config.openWorkbench)

  return {
    config,
    status: { ...currentStatus }
  }
}

// globalShortcut 须在 app ready 后使用；启动时由 entry 的 whenReady 调用。
export function syncShortcutsFromConfig(): DesktopShortcutsState {
  const config = readShortcutsConfig()
  const state = applyShortcuts(config)
  broadcastShortcutsChanged(state)

  return state
}

export function cleanupShortcuts(): void {
  try {
    globalShortcut.unregisterAll()
  } catch {
    // 忽略退出清理异常
  }

  currentRegistered.clear()
  currentStatus = idleStatus()
}

export function registerShortcutsIpc(options: ShortcutsIpcDeps): void {
  deps = options
  const { ipcMain } = options

  ipcMain.handle(IPC.invoke.shortcutsGet, (): DesktopShortcutsState => {
    return {
      config: readShortcutsConfig(),
      status: { ...currentStatus }
    }
  })

  ipcMain.handle(
    IPC.invoke.shortcutsSet,
    async (_event, payload: DesktopShortcutsSetPayload): Promise<DesktopShortcutsState> => {
      const next = overlayShortcuts(payload?.shortcuts, readShortcutsConfig())

      await store.patch(['shortcuts'], { value: next })
      const state = applyShortcuts(next)
      broadcastShortcutsChanged(state)

      return state
    }
  )
}
