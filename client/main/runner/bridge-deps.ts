import type { App, BrowserWindow, Net, SafeStorage } from 'electron'

import type { BackendHttp } from '../backend/http'
import type { BackendSession, createBackendSession, SessionSnapshot } from '../backend/session'
import type { autoStartBridge, autoStopBridge } from '../ipc/runner'
import type { buildClientContext } from '../shared/client-context'

import type { createRunnerBridge, RunnerBridge } from './bridge'
import type { createRunnerProcess } from './process'
import type { createReverseRpc } from './reverse-rpc'
import type { createRunnerWsServer } from './rpc-ws'

export interface RunnerBridgeDeps {
  app: { getPath: (name: string) => string; [key: string]: unknown }
  atomicWriteFile: (path: string, content: Buffer | string | Uint8Array) => Promise<void>
  autoStartBridge: () => void
  autoStopBridge: () => void
  backendSession: null | BackendSession
  broadcastAuthChanged: (snapshot: null | SessionSnapshot) => void
  buildClientContext: () => ReturnType<typeof buildClientContext>
  createBackendSession: typeof createBackendSession
  createReverseRpc: typeof createReverseRpc
  createRunnerBridge: typeof createRunnerBridge
  createRunnerProcess: typeof createRunnerProcess
  createRunnerWsServer: typeof createRunnerWsServer
  electronNet: Net
  ensureBackendSession: () => BackendSession
  fetchJson: BackendHttp['fetchJson']
  fileExists: (path: string) => boolean
  getMainWindow: () => BrowserWindow | null
  getSpriteWindow: () => BrowserWindow | null
  readonly isQuitting: boolean
  setQuitting: (quitting: boolean) => void
  rebuildTrayMenu: () => void
  rememberLog: (chunk: string) => void
  resetBackendCache: () => void
  resolveSpiritAgentVersion: BackendHttp['resolveSpiritAgentVersion']
  rewireAuthToken: () => void
  runnerBridge: null | RunnerBridge
  safeStorage: SafeStorage
  spiritagentHome: string
  taggedLogger: (prefix: string) => (msg: string) => void
}

export interface CreateBridgeDepsGlobals {
  app: Pick<App, 'getPath'>
  atomicWriteFile: RunnerBridgeDeps['atomicWriteFile']
  autoStartBridge: typeof autoStartBridge
  autoStopBridge: typeof autoStopBridge
  backendHttp: BackendHttp
  broadcastAuthChanged: RunnerBridgeDeps['broadcastAuthChanged']
  buildClientContext: typeof buildClientContext
  createBackendSession: typeof createBackendSession
  createReverseRpc: typeof createReverseRpc
  createRunnerBridge: typeof createRunnerBridge
  createRunnerProcess: typeof createRunnerProcess
  createRunnerWsServer: typeof createRunnerWsServer
  electronNet: Net
  errorMessage: (error: unknown) => string
  fileExists: RunnerBridgeDeps['fileExists']
  getAuthToken: { getter: () => string | null; setter: (fn: () => string | null) => void }
  getMainWindow?: () => BrowserWindow | null
  getSpriteWindow?: () => BrowserWindow | null
  readStoredBackendUrl: (home: string | null | undefined) => string | null
  rebuildTrayMenu: () => void
  rememberLog: (chunk: string) => void
  resetBackendCache: () => void
  safeStorage: SafeStorage
  spiritagentHome: string
}

export function createBridgeDeps(globals: CreateBridgeDepsGlobals): RunnerBridgeDeps {
  let quitting = false

  const deps: RunnerBridgeDeps = {
    app: globals.app as unknown as RunnerBridgeDeps['app'],
    atomicWriteFile: globals.atomicWriteFile,
    autoStartBridge: () => {
      throw new Error('autoStartBridge not yet initialized')
    },
    autoStopBridge: () => {
      throw new Error('autoStopBridge not yet initialized')
    },
    backendSession: null,
    broadcastAuthChanged: globals.broadcastAuthChanged,
    buildClientContext: () =>
      globals.buildClientContext({
        desktopVersion: globals.backendHttp.resolveSpiritAgentVersion(),
        spiritagentHome: globals.spiritagentHome
      }),
    createBackendSession: globals.createBackendSession,
    createReverseRpc: globals.createReverseRpc,
    createRunnerBridge: globals.createRunnerBridge,
    createRunnerProcess: globals.createRunnerProcess,
    createRunnerWsServer: globals.createRunnerWsServer,
    electronNet: globals.electronNet,
    ensureBackendSession: () => {
      if (deps.backendSession) {
        return deps.backendSession
      }

      deps.backendSession = globals.createBackendSession({
        appVersion: globals.backendHttp.resolveSpiritAgentVersion(),
        defaultBaseUrl: globals.readStoredBackendUrl(globals.spiritagentHome) || null,
        fetchImpl: (url, options) => globals.electronNet.fetch(url, options),
        log: chunk => globals.rememberLog(chunk),
        safeStorage: globals.safeStorage,
        userDataDir: globals.app.getPath('userData')
      })

      try {
        deps.backendSession
          .restoreSession()
          .then((snapshot: null | SessionSnapshot) => {
            if (snapshot) {
              globals.broadcastAuthChanged(snapshot)
              deps.autoStartBridge()
            } else {
              globals.rebuildTrayMenu()
            }
          })
          .catch((error: unknown) => {
            const msg = globals.errorMessage(error)

            globals.rememberLog(`[session] restore failed: ${msg}`)
            globals.rebuildTrayMenu()
          })
      } catch (error: unknown) {
        const msg = globals.errorMessage(error)

        globals.rememberLog(`[session] restore failed: ${msg}`)
      }

      return deps.backendSession
    },
    fetchJson: globals.backendHttp.fetchJson,
    fileExists: globals.fileExists,
    getMainWindow: globals.getMainWindow || (() => null),
    getSpriteWindow: globals.getSpriteWindow || (() => null),
    get isQuitting() {
      return quitting
    },
    setQuitting: (value: boolean) => {
      quitting = value
    },
    rebuildTrayMenu: () => globals.rebuildTrayMenu(),
    rememberLog: chunk => globals.rememberLog(chunk),
    resetBackendCache: globals.resetBackendCache,
    resolveSpiritAgentVersion: () => globals.backendHttp.resolveSpiritAgentVersion(),
    rewireAuthToken: () => {
      globals.getAuthToken.setter(() => deps.ensureBackendSession().getToken() ?? null)
    },
    runnerBridge: null,
    safeStorage: globals.safeStorage,
    spiritagentHome: globals.spiritagentHome,
    taggedLogger: (prefix: string) => (chunk: string) => globals.rememberLog(`${prefix} ${chunk}`)
  }

  deps.autoStartBridge = () => globals.autoStartBridge(deps)
  deps.autoStopBridge = () => globals.autoStopBridge(deps)

  return deps
}
