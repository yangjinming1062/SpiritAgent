import { type App, BrowserWindow, type Menu } from 'electron'

import type { ZoomPersistence } from './zoom-persistence'

interface MenuOptions {
  app: Pick<App, 'getVersion' | 'setAboutPanelOptions' | 'showAboutPanel'>
  appName: string
  getMainWindow: () => BrowserWindow | null
  isMac: boolean
  menu: typeof Menu
  zoomPersistence: ZoomPersistence
}

export function createMenu({ app, appName, getMainWindow, isMac, menu, zoomPersistence }: MenuOptions) {
  function showAboutPanelFresh(): void {
    app.setAboutPanelOptions({
      applicationName: appName,
      applicationVersion: app.getVersion(),
      copyright: `Copyright © 2026 ${appName}`
    })
    app.showAboutPanel()
  }

  /** Close/Zoom 作用在焦点窗；无焦点时回落精灵，避免表面窗快捷键打到精灵。 */
  function targetWindow(): BrowserWindow | null {
    return BrowserWindow.getFocusedWindow() || getMainWindow()
  }

  function buildApplicationMenu(): Menu {
    const template: Electron.MenuItemConstructorOptions[] = []

    if (isMac) {
      template.push({
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
      })
    }

    template.push({
      label: 'File',
      submenu: [
        isMac
          ? {
              accelerator: 'CommandOrControl+W',
              click: () => {
                targetWindow()?.close()
              },
              label: 'Close'
            }
          : { role: 'quit' }
      ]
    })

    template.push({
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
    })

    template.push({
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
            const win = targetWindow()

            if (win && !win.isDestroyed()) {
              zoomPersistence.setAndPersistZoomLevel(win, win.webContents.getZoomLevel() + 0.1)
            }
          },
          label: 'Zoom In'
        },
        {
          accelerator: 'CommandOrControl+-',
          click: () => {
            const win = targetWindow()

            if (win && !win.isDestroyed()) {
              zoomPersistence.setAndPersistZoomLevel(win, win.webContents.getZoomLevel() - 0.1)
            }
          },
          label: 'Zoom Out'
        },
        { type: 'separator' },
        { role: 'togglefullscreen' }
      ]
    })

    template.push({
      label: 'Window',
      submenu: isMac
        ? [{ role: 'minimize' }, { role: 'zoom' }, { role: 'front' }]
        : [{ role: 'minimize' }, { role: 'close' }]
    })

    return menu.buildFromTemplate(template)
  }

  return { buildApplicationMenu, showAboutPanelFresh }
}
