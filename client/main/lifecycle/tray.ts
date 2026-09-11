import { IPC, type IpcEventChannel, type IpcEventContract, type SurfaceId } from '@ipc/contracts'
import {
  type App,
  type BrowserWindow,
  type Menu,
  type MenuItemConstructorOptions,
  type nativeImage,
  screen,
  type Tray
} from 'electron'

import type { BackendSessionLike } from '../runner/reverse-rpc'
import { buildPrefsHydratedFromConfig } from '../shared/lib/config-sync'
import * as runnerConfigStore from '../shared/lib/runner-config-store'
import { broadcastToAllWindows, errorMessage, hideAndSkipTaskbar, sendToMain } from '../shared/utils'

import type { SurfacesManager } from './surfaces'

interface TrayDeps {
  Menu: typeof Menu
  Tray: typeof Tray
  app: App
  bridgeDeps: {
    backendSession?: BackendSessionLike | null
    ensureBackendSession?: () => BackendSessionLike | null | undefined
    getMainWindow: () => BrowserWindow | null | undefined
    isQuitting?: boolean
  }
  createWindow: () => void
  getAppIconPath: () => null | string
  nativeImage: typeof nativeImage
  rememberLog: (chunk: string) => void
  surfaces?: SurfacesManager
}

const TRAY_STRINGS = {
  zh: {
    brandName: '唤生',
    activate: '激活...',
    deactivate: '反激活',
    hide: '隐藏',
    language: '语言 / Language',
    living: '生活空间',
    quit: (brandName: string) => `退出 ${brandName}`,
    quitTooltip: '退出客户端',
    resetPosition: '一键归位',
    show: '显示',
    workbench: '工作台'
  },
  en: {
    brandName: 'SpiritAgent',
    activate: 'Activate...',
    deactivate: 'Deactivate',
    hide: 'Hide',
    language: 'Language / 语言',
    living: 'Living Space',
    quit: (brandName: string) => `Quit ${brandName}`,
    quitTooltip: 'Quit',
    resetPosition: 'Reset Position',
    show: 'Show',
    workbench: 'Workbench'
  }
} as const

let trayInstance: null | Tray = null
let trayDeps: null | TrayDeps = null

// 设置和激活/反激活放在托盘右键菜单里，而不是应用内界面。
function isAuthenticated(): boolean {
  return Boolean(trayDeps?.bridgeDeps?.ensureBackendSession?.()?.getSession()?.hasToken)
}

function isSpriteVisible(): boolean {
  const win = trayDeps?.bridgeDeps?.getMainWindow?.()

  if (!win || win.isDestroyed()) {
    return false
  }

  return win.isVisible() && !win.isMinimized()
}

function sendToMainWindow<C extends IpcEventChannel>(channel: C, ...payload: IpcEventContract[C]): void {
  sendToMain(trayDeps?.bridgeDeps?.getMainWindow?.(), channel, ...payload)
}

function getCurrentLanguage(): 'en' | 'zh' {
  const lang = runnerConfigStore.read().language

  return lang === 'en' ? 'en' : 'zh'
}

async function setSystemLanguage(lang: 'en' | 'zh'): Promise<void> {
  const current = getCurrentLanguage()

  if (current === lang) {
    return
  }

  try {
    const result = await runnerConfigStore.patch(['language'], { value: lang })

    if (result.error) {
      trayDeps?.rememberLog(`[tray] setSystemLanguage(${lang}) failed: ${result.error}`)

      return
    }

    // 走与 config-sync 水合同源的 prefs-hydrated 载荷构造，确保主题规范化等不变量一致。
    broadcastToAllWindows(IPC.event.prefsHydrated, buildPrefsHydratedFromConfig(runnerConfigStore.read()))
  } catch (err) {
    const message = errorMessage(err)
    trayDeps?.rememberLog(`[tray] setSystemLanguage(${lang}) failed: ${message}`)
  } finally {
    rebuildTrayMenu()
  }
}

export function buildTrayMenu(): Menu | null {
  if (!trayDeps) {
    return null
  }

  const authed = isAuthenticated()
  const visible = isSpriteVisible()
  const currentLang = getCurrentLanguage()
  const t = TRAY_STRINGS[currentLang]

  let mainActionLabel: string
  let mainActionClick: () => void

  if (visible) {
    mainActionLabel = t.hide
    mainActionClick = () => hideMainWindow()
  } else if (authed) {
    mainActionLabel = t.show
    mainActionClick = () => showMainWindow()
  } else {
    mainActionLabel = t.activate

    mainActionClick = () => {
      showMainWindow()
      // 仅拉窗口不够——激活浮层是 React state，关掉后只能再翻回 true；
      // 通知渲染器把 activationOpen 翻回来。
      sendToMainWindow(IPC.event.trayActivate)
    }
  }

  const template: MenuItemConstructorOptions[] = [
    {
      click: mainActionClick,
      label: mainActionLabel
    },
    {
      click: () => resetMainWindowPosition(),
      label: t.resetPosition
    }
  ]

  if (authed) {
    // DESIGN §6.1：对话模式触发源之一是托盘——聊天面板是渲染层 React state，
    // 拉起窗口外还要通知渲染器开面板（与 trayActivate 同一模式）。
    // 客户端重构后这里换成两个入口：生活空间 / 工作台。
    template.push(
      { type: 'separator' },
      {
        click: () => {
          void openSurfaceFromTray('living')
        },
        label: t.living
      },
      {
        click: () => {
          void openSurfaceFromTray('workbench')
        },
        label: t.workbench
      }
    )
  }

  template.push(
    { type: 'separator' },
    {
      label: t.language,
      submenu: [
        {
          checked: currentLang === 'zh',
          click: () => {
            void setSystemLanguage('zh')
          },
          label: '简体中文',
          type: 'radio'
        },
        {
          checked: currentLang === 'en',
          click: () => {
            void setSystemLanguage('en')
          },
          label: 'English',
          type: 'radio'
        }
      ]
    }
  )

  if (authed) {
    template.push({ type: 'separator' }, { click: () => sendToMainWindow(IPC.event.trayLogout), label: t.deactivate })
  }

  template.push({ type: 'separator' }, { click: () => quitAppFully(), label: t.quit(t.brandName) })

  return trayDeps.Menu.buildFromTemplate(template)
}

export function rebuildTrayMenu(): void {
  if (trayInstance && !trayInstance.isDestroyed()) {
    trayInstance.setToolTip(TRAY_STRINGS[getCurrentLanguage()].brandName)
    const menu = buildTrayMenu()

    if (menu) {
      trayInstance.setContextMenu(menu)
    }
  }
}

export function installCloseInterceptor(win: BrowserWindow): void {
  win.on('close', event => {
    if (trayDeps?.bridgeDeps.isQuitting) {
      return
    }

    event.preventDefault()
    hideAndSkipTaskbar(win)

    rebuildTrayMenu()
  })

  win.on('show', () => rebuildTrayMenu())
  win.on('hide', () => rebuildTrayMenu())
  win.on('minimize', () => rebuildTrayMenu())
  win.on('restore', () => rebuildTrayMenu())
}

function hideMainWindow(): void {
  const win = trayDeps?.bridgeDeps?.getMainWindow?.()

  if (win && !win.isDestroyed()) {
    hideAndSkipTaskbar(win)

    rebuildTrayMenu()
  }
}

export function showMainWindow(): void {
  const win = trayDeps?.bridgeDeps?.getMainWindow?.()

  if (!win || win.isDestroyed()) {
    trayDeps?.createWindow()
    rebuildTrayMenu()

    return
  }

  if (win.isMinimized()) {
    win.restore()
  }

  if (!win.isVisible()) {
    // 精灵是无边框置顶浮层，任何路径都不该进任务栏——show 前把 skipTaskbar
    // 钉回 true（而非像工具窗口那样翻 false），否则 Windows 任务栏会多出
    // 一个按钮，dev 下还顶着 electron.exe 的默认图标。
    if (process.platform === 'win32') {
      win.setSkipTaskbar(true)
    }

    win.show()
  }

  win.focus()
  rebuildTrayMenu()
}

export function resetMainWindowPosition(): void {
  const win = trayDeps?.bridgeDeps?.getMainWindow?.()

  if (!win || win.isDestroyed()) {
    trayDeps?.createWindow()
    rebuildTrayMenu()

    return
  }

  if (win.isMinimized()) {
    win.restore()
  }

  try {
    const primaryDisplay = screen.getPrimaryDisplay()
    win.setBounds(primaryDisplay.workArea)
  } catch (err) {
    const message = errorMessage(err)
    trayDeps?.rememberLog(`[tray] reset bounds failed: ${message}`)
  }

  if (process.platform === 'win32') {
    win.setSkipTaskbar(true)
  }

  if (process.platform === 'darwin') {
    win.setAlwaysOnTop(true, 'screen-saver', 1)
  } else {
    win.setAlwaysOnTop(true, 'floating')
  }

  if (!win.isVisible()) {
    win.show()
  }

  win.focus()
  win.moveTop()

  sendToMainWindow(IPC.event.trayResetPosition)
  rebuildTrayMenu()
}

function toggleMainWindow(): void {
  if (isSpriteVisible()) {
    hideMainWindow()
  } else {
    showMainWindow()
  }
}

async function openSurfaceFromTray(id: SurfaceId): Promise<void> {
  if (!trayDeps?.surfaces) {
    return
  }

  try {
    await trayDeps.surfaces.openSurface({ surface: id })
  } catch (err) {
    const message = errorMessage(err)
    trayDeps.rememberLog(`[tray] openSurface(${id}) failed: ${message}`)
  }
}

function quitAppFully(): void {
  trayDeps?.app.quit()
}

export function installTray(deps: TrayDeps): null | Tray {
  trayDeps = deps

  let image: null | ReturnType<typeof deps.nativeImage.createFromPath> = null

  try {
    const iconPath = trayDeps.getAppIconPath()
    image = iconPath ? deps.nativeImage.createFromPath(iconPath) : null

    if (!image || image.isEmpty()) {
      deps.rememberLog('[tray] no usable icon resolved — operating in hide-only mode')

      return null
    }
  } catch (err) {
    const message = errorMessage(err)
    deps.rememberLog(`[tray] icon load failed: ${message} — operating in hide-only mode`)

    return null
  }

  try {
    trayInstance = new deps.Tray(image)
  } catch (err) {
    const message = errorMessage(err)
    deps.rememberLog(`[tray] init failed: ${message} — operating in hide-only mode`)

    return null
  }

  trayInstance.setToolTip(TRAY_STRINGS[getCurrentLanguage()].brandName)
  const menu = buildTrayMenu()

  if (menu) {
    trayInstance.setContextMenu(menu)
  }

  trayInstance.on('click', () => {
    toggleMainWindow()
  })

  trayInstance.on('double-click', () => {
    const last = trayDeps?.surfaces?.hydrateLastSurface() ?? 'living'
    void openSurfaceFromTray(last)
  })

  return trayInstance
}

export function destroyTray(): void {
  if (trayInstance && !trayInstance.isDestroyed()) {
    trayInstance.destroy()
  }

  trayInstance = null
}

export function registerSingleInstanceForwarder(deps: TrayDeps): void {
  trayDeps = deps
  deps.app.on('second-instance', () => {
    deps.rememberLog?.('[instance] second-instance forwarded')
    const win = deps.bridgeDeps.getMainWindow()

    if (!win || win.isDestroyed()) {
      deps.createWindow()

      return
    }

    showMainWindow()
  })
}
