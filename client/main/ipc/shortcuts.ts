import {
  DEFAULT_SHORTCUTS,
  type DesktopShortcutsConfig,
  type DesktopShortcutsSetPayload,
  type DesktopShortcutsState,
  IPC,
  type ShortcutRegistrationStatus,
  type SurfaceId
} from '@ipc/contracts'
import { type BrowserWindow, globalShortcut, type IpcMain } from 'electron'

import type { SurfacesManager } from '../lifecycle/surfaces'
import * as store from '../shared/lib/runner-config-store'
import { errorMessage, sendToMain } from '../shared/utils'

interface ShortcutsIpcDeps {
  getMainWindow: () => BrowserWindow | null | undefined
  hideMainWindow: () => void
  ipcMain: IpcMain
  rememberLog?: (chunk: string) => void
  showMainWindow: () => void
  surfaces?: SurfacesManager
}

let deps: ShortcutsIpcDeps | null = null
const currentRegistered = new Map<keyof DesktopShortcutsConfig, string>()

const currentStatus: Record<keyof DesktopShortcutsConfig, ShortcutRegistrationStatus> = {
  openLiving: { registered: false },
  openWorkbench: { registered: false },
  toggleVisibility: { registered: false }
}

function broadcastShortcutsChanged(state: DesktopShortcutsState): void {
  if (!deps) {
    return
  }

  sendToMain(deps.getMainWindow(), IPC.event.shortcutsChanged, state)
}

export function readShortcutsConfig(): DesktopShortcutsConfig {
  const root = store.read()
  const raw = root.shortcuts as Record<string, unknown> | undefined

  return {
    openLiving: typeof raw?.openLiving === 'string' ? raw.openLiving : DEFAULT_SHORTCUTS.openLiving,
    openWorkbench: typeof raw?.openWorkbench === 'string' ? raw.openWorkbench : DEFAULT_SHORTCUTS.openWorkbench,
    toggleVisibility:
      typeof raw?.toggleVisibility === 'string' ? raw.toggleVisibility : DEFAULT_SHORTCUTS.toggleVisibility
  }
}

function handleToggleVisibility(): void {
  if (!deps) {
    return
  }

  const win = deps.getMainWindow()

  if (win && !win.isDestroyed() && win.isVisible() && !win.isMinimized()) {
    deps.hideMainWindow()
  } else {
    deps.showMainWindow()
  }
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
    } catch {
      // 忽略注销时的异常
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

export function applyShortcuts(config: DesktopShortcutsConfig): DesktopShortcutsState {
  registerSingleShortcut('toggleVisibility', config.toggleVisibility)
  registerSingleShortcut('openLiving', config.openLiving)
  registerSingleShortcut('openWorkbench', config.openWorkbench)

  return {
    config,
    status: { ...currentStatus }
  }
}

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
  currentStatus.toggleVisibility = { registered: false }
  currentStatus.openLiving = { registered: false }
  currentStatus.openWorkbench = { registered: false }
}

export function registerShortcutsIpc(options: ShortcutsIpcDeps): void {
  deps = options
  const { ipcMain } = options

  // 启动时应用当前配置
  syncShortcutsFromConfig()

  ipcMain.handle(IPC.invoke.shortcutsGet, (): DesktopShortcutsState => {
    return {
      config: readShortcutsConfig(),
      status: { ...currentStatus }
    }
  })

  ipcMain.handle(
    IPC.invoke.shortcutsSet,
    async (_event, payload: DesktopShortcutsSetPayload): Promise<DesktopShortcutsState> => {
      const current = readShortcutsConfig()

      const next: DesktopShortcutsConfig = {
        openLiving:
          typeof payload?.shortcuts?.openLiving === 'string' ? payload.shortcuts.openLiving : current.openLiving,
        openWorkbench:
          typeof payload?.shortcuts?.openWorkbench === 'string'
            ? payload.shortcuts.openWorkbench
            : current.openWorkbench,
        toggleVisibility:
          typeof payload?.shortcuts?.toggleVisibility === 'string'
            ? payload.shortcuts.toggleVisibility
            : current.toggleVisibility
      }

      await store.patch(['shortcuts'], { value: next })
      const state = applyShortcuts(next)
      broadcastShortcutsChanged(state)

      return state
    }
  )

  ipcMain.handle(IPC.invoke.shortcutsReset, async (): Promise<DesktopShortcutsState> => {
    await store.patch(['shortcuts'], { value: DEFAULT_SHORTCUTS })
    const state = applyShortcuts(DEFAULT_SHORTCUTS)
    broadcastShortcutsChanged(state)

    return state
  })
}
