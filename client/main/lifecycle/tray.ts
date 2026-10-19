import type { dialog } from 'electron'
import {
  type App,
  type BrowserWindow,
  type Menu,
  type MenuItemConstructorOptions,
  type nativeImage,
  screen,
  type Tray
} from 'electron'

import { IPC, type IpcEventChannel, type IpcEventContract, type PresentationMode } from '@ipc/contracts'
import { errorMessage } from '@runtime'

import type { BackendSessionPort } from '../shared/backend-port'
import { confirmAccountRemoval, showAccountRemovalError } from '../shared/lib/account-removal'
import { setClientLanguage } from '../shared/lib/client-language'
import * as runnerConfigStore from '../shared/lib/runner-config-store'
import { hideAndSkipTaskbar, isWindowShown, sendToWindow } from '../shared/utils'

import type { DesktopPresentation } from './desktop-presentation'
import type { SurfacesManager } from './surfaces'

interface TrayDeps {
  Menu: typeof Menu
  Tray: typeof Tray
  app: App
  dialog: typeof dialog
  ensureBackendSession?: () => BackendSessionPort | null | undefined
  getIsQuitting?: () => boolean
  getMainWindow: () => BrowserWindow | null | undefined
  createWindow: () => void
  getAppIconPath: () => null | string
  nativeImage: typeof nativeImage
  rememberLog: (chunk: string) => void
  removeAccount: (accountId: string, isLive?: () => boolean) => Promise<void>
  surfaces?: SurfacesManager
  presentation?: Pick<DesktopPresentation, 'getState' | 'setMode'>
  switchAccount: (accountId: string) => Promise<unknown>
}

const TRAY_STRINGS = {
  zh: {
    brandName: '唤生',
    activate: '激活...',
    accountSwitch: '切换账户',
    addAccount: '添加账户…',
    removeAccount: '移除账户',
    switchFailed: '切换账户失败',
    hide: '隐藏',
    language: '语言 / Language',
    openMain: '打开主窗口',
    quit: (brandName: string) => `退出 ${brandName}`,
    resetPosition: '一键归位',
    show: '显示',
    presentation: '呈现方式',
    desktopMode: '桌面模式',
    windowMode: '窗口模式',
    presentationFailed: '呈现方式切换失败'
  },
  en: {
    brandName: 'SpiritAgent',
    activate: 'Activate...',
    accountSwitch: 'Switch Account',
    addAccount: 'Add Account…',
    removeAccount: 'Remove Account',
    switchFailed: 'Could not switch account',
    hide: 'Hide',
    language: 'Language / 语言',
    openMain: 'Open Main Window',
    quit: (brandName: string) => `Quit ${brandName}`,
    resetPosition: 'Reset Position',
    show: 'Show',
    presentation: 'Presentation',
    desktopMode: 'Desktop mode',
    windowMode: 'Window mode',
    presentationFailed: 'Could not change presentation'
  }
} as const

const MAC_TRAY_ICON_SIZE = 16

let trayInstance: null | Tray = null
let trayDeps: null | TrayDeps = null
let accountOperationBusy = false
let presentationOperationBusy = false

function isAuthenticated(): boolean {
  return Boolean(trayDeps?.ensureBackendSession?.()?.getSession()?.hasToken)
}

function isSpriteVisible(): boolean {
  return isWindowShown(trayDeps?.getMainWindow?.())
}

function sendToMainWindow<C extends IpcEventChannel>(channel: C, ...payload: IpcEventContract[C]): void {
  sendToWindow(trayDeps?.getMainWindow?.(), channel, ...payload)
}

function showActivation(): void {
  showMainWindow()
  const win = trayDeps?.getMainWindow?.()

  if (win?.webContents.isLoading()) {
    win.webContents.once('did-finish-load', () => sendToWindow(win, IPC.event.trayActivate))
  } else {
    sendToMainWindow(IPC.event.trayActivate)
  }
}

function resizeMacTrayIcon(
  image: ReturnType<typeof nativeImage.createFromPath>,
  nativeImageApi: typeof nativeImage
): ReturnType<typeof nativeImage.createFromPath> {
  const trayImage = nativeImageApi.createEmpty()

  for (const scaleFactor of [1, 2]) {
    const pixelSize = MAC_TRAY_ICON_SIZE * scaleFactor
    const representation = image.resize({ height: pixelSize, width: pixelSize })

    trayImage.addRepresentation({
      buffer: representation.toPNG(),
      height: pixelSize,
      scaleFactor,
      width: pixelSize
    })
  }

  return trayImage
}

async function switchFromTray(accountId: string): Promise<void> {
  if (!trayDeps || accountOperationBusy) {
    return
  }

  accountOperationBusy = true
  rebuildTrayMenu()

  try {
    await trayDeps.switchAccount(accountId)
  } catch (error) {
    trayDeps.dialog.showErrorBox(TRAY_STRINGS[getCurrentLanguage()].switchFailed, errorMessage(error))
  } finally {
    accountOperationBusy = false
    rebuildTrayMenu()
  }
}

async function changePresentationFromTray(mode: PresentationMode): Promise<void> {
  const presentation = trayDeps?.presentation

  if (!trayDeps || !presentation || presentationOperationBusy) {
    return
  }

  presentationOperationBusy = true
  rebuildTrayMenu()

  try {
    await presentation.setMode(mode)
  } catch (error) {
    trayDeps.dialog.showErrorBox(TRAY_STRINGS[getCurrentLanguage()].presentationFailed, errorMessage(error))
  } finally {
    presentationOperationBusy = false
    rebuildTrayMenu()
  }
}

async function removeFromTray(accountId: string): Promise<void> {
  if (!trayDeps || accountOperationBusy) {
    return
  }

  const session = trayDeps.ensureBackendSession?.()
  const account = session?.listAccounts().find(item => item.id === accountId)

  if (!session || !account) {
    return
  }

  const sessionId = session.getSession()?.sessionId
  const selectedAccountId = session.getSelectedAccountId()

  const isLive = (): boolean =>
    session.getSession()?.sessionId === sessionId &&
    session.getSelectedAccountId() === selectedAccountId &&
    session.listAccounts().some(item => item.id === accountId)

  accountOperationBusy = true
  rebuildTrayMenu()

  try {
    if ((await confirmAccountRemoval({ account, dialog: trayDeps.dialog })) && isLive()) {
      await trayDeps.removeAccount(accountId, isLive)
    }
  } catch (error) {
    showAccountRemovalError(trayDeps.dialog, error)
  } finally {
    accountOperationBusy = false
    rebuildTrayMenu()
  }
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
    const result = await setClientLanguage(lang)

    if (result.error) {
      trayDeps?.rememberLog(`[tray] setSystemLanguage(${lang}) failed: ${result.error}`)

      return
    }
  } catch (err) {
    const message = errorMessage(err)
    trayDeps?.rememberLog(`[tray] setSystemLanguage(${lang}) failed: ${message}`)
  } finally {
    rebuildTrayMenu()
  }
}

function buildTrayMenu(): Menu | null {
  if (!trayDeps) {
    return null
  }

  const authed = isAuthenticated()
  const visible = isSpriteVisible()
  const currentLang = getCurrentLanguage()
  const t = TRAY_STRINGS[currentLang]

  const mainAction = visible
    ? { click: hideMainWindow, label: t.hide }
    : authed
      ? { click: showMainWindow, label: t.show }
      : { click: showActivation, label: t.activate }

  const template: MenuItemConstructorOptions[] = [
    mainAction,
    {
      click: resetMainWindowPosition,
      label: t.resetPosition
    }
  ]

  if (authed) {
    template.push(
      { type: 'separator' },
      {
        click: () => {
          void openSurfaceFromTray()
        },
        label: t.openMain
      }
    )
  }

  const presentation = trayDeps.presentation?.getState()

  if (authed && presentation?.supported) {
    const enabled =
      !presentationOperationBusy &&
      !presentation.switchBlockedReason &&
      presentation.status !== 'starting' &&
      presentation.status !== 'recovering'

    template.push({
      label: t.presentation,
      submenu: [
        ...(presentation.switchBlockedReason
          ? [{ label: presentation.switchBlockedReason, enabled: false }, { type: 'separator' as const }]
          : []),
        {
          type: 'radio',
          label: t.windowMode,
          checked: presentation.effectiveMode === 'window',
          enabled,
          click: () => void changePresentationFromTray('window')
        },
        {
          type: 'radio',
          label: t.desktopMode,
          checked: presentation.effectiveMode === 'desktop',
          enabled,
          click: () => void changePresentationFromTray('desktop')
        }
      ]
    })
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

  const accounts = trayDeps.ensureBackendSession?.()?.listAccounts() ?? []
  const counts = new Map<string, number>()

  for (const account of accounts) {
    counts.set(account.username, (counts.get(account.username) ?? 0) + 1)
  }

  // 同名账户追加后端地址以区分。
  const labelOf = (account: (typeof accounts)[number]): string =>
    (counts.get(account.username) ?? 0) > 1 ? `${account.username} (${account.baseUrl})` : account.username

  const accountItems: MenuItemConstructorOptions[] = accounts.map(account => ({
    checked: account.active,
    click: () => {
      void switchFromTray(account.id)
    },
    enabled: !accountOperationBusy,
    label: labelOf(account),
    type: 'radio'
  }))

  if (accountItems.length) {
    accountItems.push({ type: 'separator' })
  }

  accountItems.push({ click: showActivation, enabled: !accountOperationBusy, label: t.addAccount })

  if (accounts.length) {
    accountItems.push({
      enabled: !accountOperationBusy,
      label: t.removeAccount,
      submenu: accounts.map(account => ({
        click: () => {
          void removeFromTray(account.id)
        },
        label: labelOf(account)
      }))
    })
  }

  template.push(
    { type: 'separator' },
    { label: t.accountSwitch, submenu: accountItems },
    { type: 'separator' },
    { click: quitAppFully, label: t.quit(t.brandName) }
  )

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
    if (trayDeps?.getIsQuitting?.()) {
      return
    }

    event.preventDefault()
    hideAndSkipTaskbar(win)

    rebuildTrayMenu()
  })

  // 每次创建精灵窗都经此挂载；首个窗口创建时托盘依赖尚未注入，因此在事件发生时再取 surfaces。
  const onVisibilityChanged = (): void => {
    rebuildTrayMenu()
    trayDeps?.surfaces?.publishSpriteVisibility()
  }

  win.on('show', onVisibilityChanged)
  win.on('hide', onVisibilityChanged)
  win.on('minimize', onVisibilityChanged)
  win.on('restore', onVisibilityChanged)
}

function hideMainWindow(): void {
  const win = trayDeps?.getMainWindow?.()

  if (win && !win.isDestroyed()) {
    hideAndSkipTaskbar(win)

    rebuildTrayMenu()
  }
}

export function showMainWindow(): void {
  if (trayDeps?.surfaces?.isOpen() || trayDeps?.presentation?.getState().effectiveMode === 'desktop') {
    void openSurfaceFromTray()

    return
  }

  const win = trayDeps?.getMainWindow?.()

  if (!win || win.isDestroyed()) {
    trayDeps?.createWindow()
    rebuildTrayMenu()

    return
  }

  if (win.isMinimized()) {
    win.restore()
  }

  if (!win.isVisible()) {
    // 精灵是无边框置顶浮层，任何路径都不该进任务栏—— show 前把 skipTaskbar 钉回 true，否则 Windows 任务栏会多出按钮（dev 下还顶着 electron.exe 图标）。
    if (process.platform === 'win32') {
      win.setSkipTaskbar(true)
    }

    win.show()
  }

  win.focus()
  rebuildTrayMenu()
}

function resetMainWindowPosition(): void {
  const win = trayDeps?.getMainWindow?.()

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

  win.setAlwaysOnTop(true, 'floating')

  if (!win.isVisible()) {
    win.show()
  }

  win.focus()
  win.moveTop()

  sendToMainWindow(IPC.event.trayResetPosition)
  rebuildTrayMenu()
}

export function toggleMainWindow(): void {
  if (trayDeps?.surfaces?.isOpen() || trayDeps?.presentation?.getState().effectiveMode === 'desktop') {
    void openSurfaceFromTray()

    return
  }

  if (isSpriteVisible()) {
    hideMainWindow()
  } else {
    showMainWindow()
  }
}

async function openSurfaceFromTray(): Promise<void> {
  if (!trayDeps?.surfaces) {
    return
  }

  try {
    await trayDeps.surfaces.openSurface({})
  } catch (err) {
    const message = errorMessage(err)
    trayDeps.rememberLog(`[tray] openSurface failed: ${message}`)
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

    if (process.platform === 'darwin') {
      image = resizeMacTrayIcon(image, deps.nativeImage)
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

  rebuildTrayMenu()

  trayInstance.on('click', () => {
    toggleMainWindow()
  })

  trayInstance.on('double-click', () => {
    void openSurfaceFromTray()
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
    deps.rememberLog('[instance] second-instance forwarded')
    showMainWindow()
  })
}
