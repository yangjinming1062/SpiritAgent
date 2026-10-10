import type { BrowserWindow, Menu, PowerMonitor, session } from 'electron'

import { errorMessage } from '../shared/utils'

import type { ContextMenuHelpers } from './window-context-menu-helpers'
import type { ZoomPersistence } from './zoom-persistence'

const DEFAULT_RENDERER_RELOAD_WINDOW_MS = 60_000
const DEFAULT_RENDERER_RELOAD_MAX = 3

interface WindowHandlersOptions {
  clipboard: { writeText: (text: string) => void }
  cspPolicies: { dev: string; prod: string }
  isDevServer: string | null | undefined
  isMac: boolean
  isPackaged: boolean
  menu: typeof Menu
  openExternalUrl: (url: string) => boolean
  powerMonitor: PowerMonitor
  rememberLog: (chunk: string) => void
  sendPowerResume: () => void
  session: typeof session
  zoomPersistence: ZoomPersistence
  contextMenuHelpers: ContextMenuHelpers
}

export function createWindowHandlers({
  clipboard,
  cspPolicies,
  isDevServer,
  isMac,
  isPackaged,
  menu,
  openExternalUrl,
  powerMonitor,
  rememberLog,
  sendPowerResume,
  session,
  zoomPersistence,
  contextMenuHelpers
}: WindowHandlersOptions) {
  let rendererReloadTimes: number[] = []

  function toggleDevTools(targetWin: BrowserWindow): void {
    const { webContents } = targetWin

    if (webContents.isDevToolsOpened()) {
      webContents.closeDevTools()
    } else {
      webContents.openDevTools({ mode: 'detach' })
    }
  }

  function installDevToolsShortcut(targetWin: BrowserWindow): void {
    targetWin.webContents.on('before-input-event', (event, input) => {
      const key = input.key.toLowerCase()

      const isInspectShortcut =
        input.key === 'F12' ||
        (isMac && input.meta && input.alt && key === 'i') ||
        (!isMac && input.control && input.shift && key === 'i')

      if (!isInspectShortcut) {
        return
      }

      event.preventDefault()
      toggleDevTools(targetWin)
    })
  }

  function installZoomShortcuts(targetWin: BrowserWindow): void {
    targetWin.webContents.on('before-input-event', (event, input) => {
      const mod = isMac ? input.meta : input.control

      if (!mod || input.alt || input.shift) {
        return
      }

      const key = input.key

      if (key === '0') {
        event.preventDefault()
        zoomPersistence.setAndPersistZoomLevel(targetWin, 0)
      } else if (key === '=' || key === '+') {
        event.preventDefault()
        zoomPersistence.stepZoomLevel(targetWin, 1)
      } else if (key === '-') {
        event.preventDefault()
        zoomPersistence.stepZoomLevel(targetWin, -1)
      }
    })
  }

  function installContextMenu(targetWin: BrowserWindow): void {
    targetWin.webContents.on('context-menu', (_event, params) => {
      const { editFlags, isEditable, linkURL, misspelledWord, srcURL } = params
      const hasSelection = Boolean(params.selectionText?.trim())

      // data: 图部分环境下 mediaType 不为 image，仍应提供复制/另存；不用路径段启发式，任意含 /image/ 的 URL 会被误判。
      const hasImage =
        Boolean(srcURL) &&
        (params.mediaType === 'image' ||
          srcURL.startsWith('data:image/') ||
          /\.(png|jpe?g|webp|gif|bmp|svg)(\?|#|$)/i.test(srcURL))

      const suggestions = Array.isArray(params.dictionarySuggestions) ? params.dictionarySuggestions.slice(0, 5) : []

      // 各组之间以分隔线隔开，没有内容的组不占分隔线。
      const groups: Electron.MenuItemConstructorOptions[][] = []

      if (hasImage) {
        groups.push([
          {
            enabled: !srcURL.startsWith('data:'),
            label: 'Open Image',
            click: () => openExternalUrl(srcURL)
          },
          {
            label: 'Copy Image',
            click: () => {
              void contextMenuHelpers
                .copyImageFromUrl(srcURL)
                .catch(error => rememberLog(`Copy image failed: ${errorMessage(error)}`))
            }
          },
          {
            label: 'Copy Image Address',
            click: () => clipboard.writeText(srcURL)
          },
          {
            label: 'Save Image As...',
            click: () => {
              void contextMenuHelpers
                .saveImageFromUrl(srcURL, targetWin)
                .catch(error => rememberLog(`Save image failed: ${errorMessage(error)}`))
            }
          }
        ])
      }

      if (linkURL) {
        groups.push([
          {
            label: 'Open Link',
            click: () => openExternalUrl(linkURL)
          },
          {
            label: 'Copy Link',
            click: () => clipboard.writeText(linkURL)
          }
        ])
      }

      if (isEditable && misspelledWord && suggestions.length > 0) {
        groups.push([
          ...suggestions.map(suggestion => ({
            label: suggestion,
            click: () => targetWin.webContents.replaceMisspelling(suggestion)
          })),
          { type: 'separator' },
          {
            label: 'Add to dictionary',
            click: () => targetWin.webContents.session.addWordToSpellCheckerDictionary(misspelledWord)
          }
        ])
      }

      if (isEditable) {
        groups.push([
          { enabled: editFlags.canCut, role: 'cut' },
          { enabled: editFlags.canCopy, role: 'copy' },
          { enabled: editFlags.canPaste, role: 'paste' },
          { type: 'separator' },
          { enabled: editFlags.canSelectAll, role: 'selectAll' }
        ])
      } else if (hasSelection) {
        groups.push([{ enabled: editFlags.canCopy, role: 'copy' }])
      }

      const template = groups.flatMap((group, index) => (index ? [{ type: 'separator' as const }, ...group] : group))

      // 没有任何适用项时不弹菜单
      if (!template.length) {
        return
      }

      menu.buildFromTemplate(template).popup({ window: targetWin })
    })
  }

  function installSurfaceWindowHandlers(win: BrowserWindow, options?: { reloadOnCrash?: boolean }): void {
    installStandardWindowHandlers(win, options)
    installZoomShortcuts(win)
    installContextMenu(win)
  }

  function isAudioCapturePermission(
    permission: string,
    details:
      | Electron.PermissionRequest
      | Electron.FilesystemPermissionRequest
      | Electron.MediaAccessPermissionRequest
      | Electron.OpenExternalPermissionRequest
  ): boolean {
    if (permission === 'audioCapture') {
      return true
    }

    if (permission !== 'media') {
      return false
    }

    const mediaTypes = 'mediaTypes' in details ? details.mediaTypes : undefined

    if (!Array.isArray(mediaTypes) || mediaTypes.length === 0) {
      return true
    }

    return mediaTypes.includes('audio') && !mediaTypes.includes('video')
  }

  function installMediaPermissions(): void {
    session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback, details) => {
      callback(isAudioCapturePermission(permission, details))
    })

    session.defaultSession.setPermissionCheckHandler((_webContents, permission, _origin, details) => {
      return ['media', 'audioCapture'].includes(permission) && details?.mediaType !== 'video'
    })
  }

  function installContentSecurityPolicy(): void {
    const policy = isPackaged ? cspPolicies.prod : cspPolicies.dev

    session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
      callback({
        responseHeaders: {
          ...details.responseHeaders,
          'Content-Security-Policy': [policy]
        }
      })
    })
  }

  function installStandardWindowHandlers(win: BrowserWindow, options?: { reloadOnCrash?: boolean }): void {
    installDevToolsShortcut(win)

    win.webContents.setWindowOpenHandler(details => {
      openExternalUrl(details.url)

      return { action: 'deny' }
    })

    win.webContents.on('will-navigate', (event, url) => {
      if (url.startsWith(isDevServer || 'file:')) {
        return
      }

      event.preventDefault()
      openExternalUrl(url)
    })

    win.webContents.on('render-process-gone', (_event, details) => {
      rememberLog(`[renderer] render-process-gone reason=${details?.reason} exitCode=${details?.exitCode}`)

      // reloadOnCrash: false 时只记日志不自动 reload（桌面窗口保持崩溃态直至销毁），不占共享 reload 预算。
      if (options?.reloadOnCrash === false) {
        return
      }

      if (details?.reason === 'crashed' || details?.reason === 'oom') {
        const now = Date.now()

        rendererReloadTimes = rendererReloadTimes.filter(t => now - t < DEFAULT_RENDERER_RELOAD_WINDOW_MS)

        if (rendererReloadTimes.length >= DEFAULT_RENDERER_RELOAD_MAX) {
          rememberLog(
            `[renderer] suppressing reload: ${rendererReloadTimes.length} crashes within ${DEFAULT_RENDERER_RELOAD_WINDOW_MS}ms (likely a crash loop)`
          )

          return
        }

        rendererReloadTimes.push(now)

        setImmediate(() => {
          if (!win || win.isDestroyed()) {
            return
          }

          try {
            win.webContents.reload()
          } catch (err: unknown) {
            const message = errorMessage(err)

            rememberLog(`[renderer] reload after crash failed: ${message}`)
          }
        })
      }
    })

    win.webContents.on('unresponsive', () => rememberLog('[renderer] webContents became unresponsive'))

    win.webContents.on('console-message', event => {
      if (event.level !== 'error') {
        return
      }

      rememberLog(`[renderer console] ${event.message} (${event.sourceId}:${event.lineNumber})`)
    })
  }

  let powerResumeRegistered = false

  function registerPowerResumeListeners(): void {
    if (powerResumeRegistered) {
      return
    }

    powerResumeRegistered = true
    powerMonitor.on('resume', sendPowerResume)
    powerMonitor.on('unlock-screen', sendPowerResume)
  }

  function configureSpellChecker(app: { getLocale?: () => string | null }): void {
    try {
      const available = session.defaultSession.availableSpellCheckerLanguages || []
      const locale = app.getLocale?.() || 'en-US'
      const candidates = [locale, locale.split('-')[0], 'en-US', 'en']
      const chosen = candidates.find(lang => available.includes(lang)) || 'en-US'

      session.defaultSession.setSpellCheckerLanguages([chosen])
    } catch (error: unknown) {
      const message = errorMessage(error)

      rememberLog(`Spellchecker setup failed: ${message}`)
    }
  }

  return {
    configureSpellChecker,
    installContentSecurityPolicy,
    installMediaPermissions,
    installStandardWindowHandlers,
    installSurfaceWindowHandlers,
    registerPowerResumeListeners
  }
}
