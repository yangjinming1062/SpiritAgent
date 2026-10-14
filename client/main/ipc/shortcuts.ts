import { globalShortcut, type IpcMain } from 'electron'

import {
  DEFAULT_SHORTCUTS,
  type DesktopShortcutsConfig,
  type DesktopShortcutsSetPayload,
  type DesktopShortcutsState,
  IPC
} from '@ipc/contracts'
import { errorMessage } from '@runtime'

import * as store from '../shared/lib/runner-config-store'
import { broadcastToAllWindows } from '../shared/utils'

/** 窄接口：快捷键只需要切表面，不依赖 lifecycle/surfaces 具体类型。 */
interface SurfaceToggler {
  toggleSurface: (payload: Record<string, never>) => Promise<void>
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
  openMain: { registered: false },
  toggleVisibility: { registered: false }
})

let currentStatus = idleStatus()

// 设置页可位于主窗口或交互桌面。
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
    openMain: pick('openMain'),
    toggleVisibility: pick('toggleVisibility')
  }
}

function readShortcutsConfig(): DesktopShortcutsConfig {
  return overlayShortcuts(store.read().shortcuts, DEFAULT_SHORTCUTS)
}

function handleToggleVisibility(): void {
  deps?.toggleMainWindow()
}

function handleToggleSurface(): void {
  deps?.surfaces?.toggleSurface({}).catch(err => {
    deps?.rememberLog?.(`[shortcuts] toggleSurface failed: ${errorMessage(err)}`)
  })
}

function getActionHandler(action: keyof DesktopShortcutsConfig): () => void {
  if (action === 'toggleVisibility') {
    return handleToggleVisibility
  }

  return handleToggleSurface
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
  registerSingleShortcut('openMain', config.openMain)

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
