import { type App, BrowserWindow, type Menu } from 'electron'

import type { ZoomPersistence } from './zoom-persistence'

interface MenuOptions {
  app: Pick<App, 'getVersion' | 'setAboutPanelOptions' | 'showAboutPanel'>
  appName: string
  getMainWindow: () => BrowserWindow | null
  isMac: boolean
  menu: typeof Menu
  zoomPersistence: ZoomPersistence
  minimizeWindow: (win: BrowserWindow) => void
  toggleMaximizeWindow: (win: BrowserWindow) => void
}

function setAboutPanel(app: Pick<App, 'getVersion' | 'setAboutPanelOptions'>, appName: string): void {
  app.setAboutPanelOptions({
    applicationName: appName,
    applicationVersion: app.getVersion(),
    copyright: `Copyright © 2026 ${appName}`
  })
}

/** 应用名、Windows AppUserModelID 与关于面板；须在 app ready 前调用。AppUserModelID 与 package.json 的 build.appId 一致。 */
export function applyAppIdentity(
  app: Pick<App, 'getVersion' | 'setAboutPanelOptions' | 'setAppUserModelId' | 'setName'>,
  appName: string
): void {
  app.setName(appName)

  if (process.platform === 'win32') {
    app.setAppUserModelId('io.spiritagent.agent')
  }

  setAboutPanel(app, appName)
}

export function createMenu({
  app,
  appName,
  getMainWindow,
  isMac,
  menu,
  zoomPersistence,
  minimizeWindow,
  toggleMaximizeWindow
}: MenuOptions) {
  function showAboutPanelFresh(): void {
    setAboutPanel(app, appName)
    app.showAboutPanel()
  }

  /** Close/Zoom 作用在焦点窗；无焦点时回落精灵，避免表面窗快捷键打到精灵。 */
  function targetWindow(): BrowserWindow | null {
    return BrowserWindow.getFocusedWindow() || getMainWindow()
  }

  // 仅在 macOS 安装（见 installApplicationMenu）。
  function buildApplicationMenu(): Menu {
    return menu.buildFromTemplate([
      {
        label: appName,
        submenu: [
          { click: () => showAboutPanelFresh(), label: `About ${appName}` },
          { type: 'separator' },
          { role: 'services' },
          { type: 'separator' },
          { role: 'hide' },
          { role: 'hideOthers' },
          { role: 'unhide' },
          { type: 'separator' },
          { role: 'quit' }
        ]
      },
      {
        label: 'File',
        submenu: [
          {
            accelerator: 'CommandOrControl+W',
            click: () => {
              targetWindow()?.close()
            },
            label: 'Close'
          }
        ]
      },
      {
        label: 'Edit',
        submenu: [
          { role: 'undo' },
          { role: 'redo' },
          { type: 'separator' },
          { role: 'cut' },
          { role: 'copy' },
          { role: 'paste' },
          { role: 'delete' },
          { role: 'selectAll' }
        ]
      },
      {
        label: 'View',
        submenu: [
          { role: 'reload' },
          { role: 'forceReload' },
          { role: 'toggleDevTools' },
          { type: 'separator' },
          {
            accelerator: 'CommandOrControl+0',
            click: () => {
              zoomPersistence.setAndPersistZoomLevel(targetWindow(), 0)
            },
            label: 'Actual Size'
          },
          {
            accelerator: 'CommandOrControl+Plus',
            click: () => {
              zoomPersistence.stepZoomLevel(targetWindow(), 1)
            },
            label: 'Zoom In'
          },
          {
            accelerator: 'CommandOrControl+-',
            click: () => {
              zoomPersistence.stepZoomLevel(targetWindow(), -1)
            },
            label: 'Zoom Out'
          },
          { type: 'separator' },
          { role: 'togglefullscreen' }
        ]
      },
      {
        label: 'Window',
        submenu: [
          {
            label: 'Minimize',
            accelerator: 'CommandOrControl+M',
            click: () => {
              const win = targetWindow()

              if (win) {
                minimizeWindow(win)
              }
            }
          },
          {
            label: 'Zoom',
            click: () => {
              const win = targetWindow()

              if (win) {
                toggleMaximizeWindow(win)
              }
            }
          },
          { role: 'front' }
        ]
      }
    ])
  }

  // 只有 macOS 保留应用菜单（系统菜单栏与编辑快捷键）；其他平台移除默认菜单。
  function installApplicationMenu(): void {
    menu.setApplicationMenu(isMac ? buildApplicationMenu() : null)
  }

  return { installApplicationMenu }
}
