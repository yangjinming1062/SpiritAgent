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
    const ZOOM_STEP = 0.1

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
        zoomPersistence.setAndPersistZoomLevel(targetWin, targetWin.webContents.getZoomLevel() + ZOOM_STEP)
      } else if (key === '-') {
        event.preventDefault()
        zoomPersistence.setAndPersistZoomLevel(targetWin, targetWin.webContents.getZoomLevel() - ZOOM_STEP)
      }
    })
  }

  function installContextMenu(targetWin: BrowserWindow): void {
    targetWin.webContents.on('context-menu', (_event, params) => {
      const template: Electron.MenuItemConstructorOptions[] = []
      const hasSelection = Boolean(params.selectionText?.trim())
      const hasImage = params.mediaType === 'image' && Boolean(params.srcURL)
      const hasLink = Boolean(params.linkURL)
      const isEditable = Boolean(params.isEditable)

      if (hasImage) {
        template.push(
          {
            enabled: !params.srcURL.startsWith('data:'),
            label: 'Open Image',
            click: () => {
              if (params.srcURL && !params.srcURL.startsWith('data:')) {
                openExternalUrl(params.srcURL)
              }
            }
          },
          {
            label: 'Copy Image',
            click: () => {
              void contextMenuHelpers
                .copyImageFromUrl(params.srcURL)
                .catch(error => rememberLog(`Copy image failed: ${errorMessage(error)}`))
            }
          },
          {
            label: 'Copy Image Address',
            click: () => clipboard.writeText(params.srcURL)
          },
          {
            label: 'Save Image As...',
            click: () => {
              void contextMenuHelpers
                .saveImageFromUrl(params.srcURL, targetWin)
                .catch(error => rememberLog(`Save image failed: ${errorMessage(error)}`))
            }
          }
        )
      }

      if (hasLink) {
        if (template.length) {
          template.push({ type: 'separator' })
        }

        template.push(
          {
            label: 'Open Link',
            click: () => openExternalUrl(params.linkURL)
          },
          {
            label: 'Copy Link',
            click: () => clipboard.writeText(params.linkURL)
          }
        )
      }

      const suggestions = Array.isArray(params.dictionarySuggestions) ? params.dictionarySuggestions : []

      if (isEditable && params.misspelledWord && suggestions.length > 0) {
        if (template.length) {
          template.push({ type: 'separator' })
        }

        for (const suggestion of suggestions.slice(0, 5)) {
          template.push({
            label: suggestion,
            click: () => targetWin.webContents.replaceMisspelling(suggestion)
          })
        }

        template.push({ type: 'separator' })
        template.push({
          label: 'Add to dictionary',
          click: () => targetWin.webContents.session.addWordToSpellCheckerDictionary(params.misspelledWord)
        })
      }

      if (hasSelection || isEditable) {
        if (template.length) {
          template.push({ type: 'separator' })
        }

        if (isEditable) {
          template.push(
            { enabled: params.editFlags.canCut, role: 'cut' },
            { enabled: params.editFlags.canCopy, role: 'copy' },
            { enabled: params.editFlags.canPaste, role: 'paste' },
            { type: 'separator' },
            { enabled: params.editFlags.canSelectAll, role: 'selectAll' }
          )
        } else {
          template.push({ enabled: params.editFlags.canCopy, role: 'copy' })
        }
      }

      if (!template.length) {
        template.push({ role: 'selectAll' })
      }

      menu.buildFromTemplate(template).popup({ window: targetWin })
    })
  }

  function installSurfaceWindowHandlers(win: BrowserWindow): void {
    installStandardWindowHandlers(win)
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

    const mediaTypes =
      'mediaTypes' in details ? (details as { mediaTypes?: ReadonlyArray<string> }).mediaTypes : undefined

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
      if ((permission as string) === 'media' || (permission as string) === 'audioCapture') {
        const mediaType = details?.mediaType

        if (mediaType === 'video') {
          return false
        }

        return true
      }

      return false
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

  function installStandardWindowHandlers(win: BrowserWindow): void {
    installDevToolsShortcut(win)

    win.webContents.setWindowOpenHandler(details => {
      openExternalUrl(details.url)

      return { action: 'deny' }
    })

    win.webContents.on('will-navigate', (event, url) => {
      if ((isDevServer && url.startsWith(isDevServer)) || (!isDevServer && url.startsWith('file:'))) {
        return
      }

      event.preventDefault()
      openExternalUrl(url)
    })

    win.webContents.on('render-process-gone', (_event, details) => {
      rememberLog(`[renderer] render-process-gone reason=${details?.reason} exitCode=${details?.exitCode}`)

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

    win.webContents.on(
      'console-message',
      (
        _event: Electron.Event,
        detailsOrLevel: number | Electron.WebContentsConsoleMessageEventParams,
        message?: string,
        line?: number,
        sourceId?: string
      ) => {
        const details = detailsOrLevel && typeof detailsOrLevel === 'object' ? detailsOrLevel : null

        const level: number = details
          ? details.level === 'error'
            ? 3
            : details.level === 'warning'
              ? 2
              : details.level === 'info'
                ? 1
                : 0
          : (detailsOrLevel as number)

        if (level !== 3) {
          return
        }

        const text = details ? details.message : (message ?? '')
        const src = details ? details.sourceId : (sourceId ?? '')
        const lineNo = details ? details.lineNumber : (line ?? 0)
        rememberLog(`[renderer console] ${text} (${src}:${lineNo})`)
      }
    )
  }

  let powerResumeRegistered = false

  function registerPowerResumeListeners(): void {
    if (powerResumeRegistered) {
      return
    }

    powerResumeRegistered = true

    try {
      powerMonitor.on('resume', sendPowerResume)
      powerMonitor.on('unlock-screen', sendPowerResume)
    } catch {
      // 尽力而为
    }
  }

  function configureSpellChecker(app: { getLocale?: () => string | null }): void {
    try {
      const available = session.defaultSession.availableSpellCheckerLanguages || []
      const locale = (app.getLocale && app.getLocale()) || 'en-US'
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
    installContextMenu,
    installMediaPermissions,
    installStandardWindowHandlers,
    installSurfaceWindowHandlers,
    installZoomShortcuts,
    registerPowerResumeListeners
  }
}
