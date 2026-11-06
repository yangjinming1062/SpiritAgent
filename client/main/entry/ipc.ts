import { app, clipboard, dialog, ipcMain, nativeImage, screen } from 'electron'

import { registerAuthIpc, registerDesktopAccountIpc } from '../ipc/auth'
import { registerClipboardIpc } from '../ipc/clipboard'
import { registerConnectionIpc } from '../ipc/connection'
import { registerDesktopDock } from '../ipc/desktop-dock'
import { clearPendingFeed, registerFilesIpc } from '../ipc/files'
import { registerGatewayIpc } from '../ipc/gateway'
import { registerLogIpc } from '../ipc/log'
import { registerOnboardingAudioIpc } from '../ipc/onboarding-audio'
import { registerPrefsIpc } from '../ipc/prefs'
import { registerRunnerConfigIpc } from '../ipc/runner-config'
import { registerSessionHistoryIpc } from '../ipc/session-history'
import { registerShortcutsIpc } from '../ipc/shortcuts'
import { registerSkillsIpc } from '../ipc/skills'
import { registerSpriteIpc } from '../ipc/sprite'
import { registerSystemIpc } from '../ipc/system'
import { registerUiThemeIpc } from '../ipc/ui-theme'
import { registerUpdateIpc } from '../ipc/update'
import { registerVoicePlaybackIpc } from '../ipc/voice-playback'
import { showActivation, toggleMainWindow } from '../lifecycle/tray'
import { isSenderWindow } from '../security/ipc-trust'

import type { EntryServices } from './services'

/** IPC 注册表：只把通道接到服务，不构造服务。 */
export function registerEntryIpc(services: EntryServices): ReturnType<typeof registerAuthIpc> {
  const {
    appQuit,
    assetDiskCache,
    authBroadcaster,
    autoUpdater,
    backendHttp,
    configSync,
    ensureBackend,
    ensureBackendSession,
    gatewayRuntime,
    getMainWindow,
    mediaIpc,
    presentation,
    publishUiTheme,
    readBackendAsset,
    rememberLog,
    resetBackendCache,
    runnerHost,
    sessionHistoryDiskCache,
    sessionRuntime,
    spiritagentHome,
    surfaces,
    voicePlaybackStore
  } = services

  registerSystemIpc({ electron: { app }, ipcMain })
  registerUiThemeIpc({ ipcMain, log: rememberLog, publishTheme: publishUiTheme })
  registerPrefsIpc({
    applyServerVoice: payload =>
      configSync.applyServerVoice(payload.accountId, payload.voiceId, () => {
        const session = ensureBackendSession().getSession()

        return session?.accountId === payload.accountId && session.sessionId === payload.authSessionId
      }),
    ipcMain,
    log: rememberLog
  })

  registerDesktopDock({
    ipcMain,
    userData: spiritagentHome,
    isDesktopSender: sender => presentation?.isDesktopSender(sender) ?? false,
    getDesktopWindow: () => presentation?.getWindow() ?? null,
    getRunningApplications: () =>
      presentation?.getRunningApplications() ?? { status: 'inactive', error: null, windows: [] },
    onRunningApplicationsChanged: listener => presentation?.onRunningApplicationsChanged(listener) ?? (() => {}),
    captureEligibility: (sender, interaction) =>
      presentation?.captureApplicationEligibility(sender, interaction) ?? {
        current: () => false,
        continuation: () => false
      },
    refreshApplications: sender => presentation?.refreshApplications(sender) ?? Promise.resolve(),
    activateExternal: (sender, windowId, allowRemote, eligible) =>
      presentation?.activateExternal(sender, windowId, allowRemote, eligible) ?? Promise.resolve(),
    launchExternal: (sender, target, appId, eligible) =>
      presentation?.launchExternal(sender, target, appId, eligible) ?? Promise.resolve(),
    closeExternal: (sender, windowIds, eligible) =>
      presentation?.closeExternal(sender, windowIds, eligible) ?? Promise.resolve(),
    getJumpListTasks: (sender, target) => presentation?.getJumpListTasks(sender, target) ?? Promise.resolve([]),
    activateJumpListTask: (sender, target, taskId, eligible) =>
      presentation?.activateJumpListTask(sender, target, taskId, eligible) ?? Promise.resolve()
  })

  registerShortcutsIpc({ ipcMain, rememberLog, surfaces: surfaces ?? undefined, toggleMainWindow })
  registerClipboardIpc({
    electron: { clipboard, dialog, getMainWindow, nativeImage },
    ipcMain
  })
  registerLogIpc({ ipcMain, log: rememberLog })
  registerFilesIpc({
    electron: { dialog, getMainWindow },
    authIdentity: () => sessionRuntime.ensureBackendSession().getSession()?.sessionId ?? null,
    isFeedSender: sender => isSenderWindow(sender, getMainWindow()),
    isFeedReceiver: sender => Boolean(surfaces?.isSurfaceSender(sender) || presentation?.isDesktopSender(sender)),
    ipcMain
  })
  registerOnboardingAudioIpc({
    appRoot: services.appRoot,
    spiritagentHome,
    ipcMain
  })

  registerConnectionIpc({
    assetDiskCache,
    ensureBackend,
    invokeApi: backendHttp.invoke,
    getGateway: () => gatewayRuntime ?? null,
    getCurrentAuth: () => sessionRuntime.getCurrentAuth(),
    getSelectedAccountId: () => sessionRuntime.ensureBackendSession().getSelectedAccountId(),
    ipcMain,
    readAsset: readBackendAsset
  })

  registerGatewayIpc({
    ipcMain,
    gateway: gatewayRuntime!,
    isPresenceSender: sender => isSenderWindow(sender, getMainWindow())
  })

  const authActions = registerAuthIpc({
    clearAccountCaches: async accountId => {
      presentation?.clearUiState(accountId)
      await Promise.all([
        assetDiskCache.clear(accountId),
        mediaIpc.clearAccountCache(accountId),
        sessionHistoryDiskCache.clear(accountId),
        voicePlaybackStore.clear(accountId)
      ])
    },
    deps: {
      autoStartBridge: () => runnerHost.autoStart(),
      autoStopBridge: () => runnerHost.autoStop(),
      restartBridge: () => runnerHost.restartForCurrentSession(),
      beforeAccountSwitch: () => presentation?.captureUiState() ?? Promise.resolve(),
      broadcastAuthChanged: authBroadcaster.broadcastAuthChanged,
      buildClientContext: () => sessionRuntime.buildClientContext(),
      ensureBackendSession,
      getSessionAfterRestore: () => sessionRuntime.getSessionAfterRestore(),
      log: rememberLog,
      onAccountIdentityChanged: async () => {
        clearPendingFeed()

        try {
          await presentation?.accountChanged()
          await surfaces?.closeSurface()
        } finally {
          services.showMainWindow()
        }
      },
      resetBackendCache,
      spiritagentHome
    },
    ipcMain
  })

  registerSessionHistoryIpc({
    ensureBackendSession,
    ipcMain,
    sessionHistoryDiskCache
  })
  registerVoicePlaybackIpc({
    ensureBackendSession,
    ipcMain,
    store: voicePlaybackStore
  })
  runnerHost.registerIpc(ipcMain)
  registerRunnerConfigIpc({
    ipcMain,
    isAuthorizedSender: event =>
      Boolean(surfaces?.isSurfaceSender(event.sender) || presentation?.isDesktopSender(event.sender))
  })
  registerSkillsIpc({
    spiritagentHome,
    getRunnerBridge: () => runnerHost.getBridge(),
    ipcMain
  })
  registerUpdateIpc({
    electron: { app },
    feed: autoUpdater,
    ipcMain,
    isInstallSender: sender => Boolean(surfaces?.isSurfaceSender(sender) || presentation?.isDesktopSender(sender)),
    prepareInstall: () => presentation?.stop() ?? Promise.resolve(),
    markQuitting: () => appQuit.markQuitting()
  })

  registerSpriteIpc({
    deps: {
      getSpriteWindow: getMainWindow,
      stageAvailable: () => presentation?.getState().effectiveMode !== 'desktop',
      getUserDataDir: () => app.getPath('userData'),
      log: rememberLog,
      screen
    },
    ipcMain
  })

  registerDesktopAccountIpc({
    deps: {
      dialog,
      ensureBackendSession,
      isDesktopSender: sender => Boolean(presentation?.isDesktopSender(sender) || surfaces?.isSurfaceSender(sender)),
      onLeavingDesktop: async () => {
        if (presentation?.getState().effectiveMode === 'desktop') {
          await presentation.setMode('window')
        } else {
          await presentation?.captureUiState()
        }

        await surfaces?.closeSurface()
      },
      openMainWindow: showActivation,
      quitApp: () => app.quit(),
      removeAccount: authActions.removeAccount,
      switchAccount: authActions.switchAccount
    },
    ipcMain
  })

  return authActions
}
