import { createHash, randomUUID } from 'node:crypto'
import path from 'node:path'

import type { SafeStorageApi } from '../security/hardening'
import { atomicWriteFile, errorMessage, safeReadJson } from '../shared/utils'

import { type BackendClient, BackendRequestError, createBackendClient, type FetchFunction } from './client'

const SESSION_FILENAME = 'agent-session.json'
const SESSION_SCHEMA_VERSION = 3
const REFRESH_LEAD_MS = 5 * 60 * 1000

interface SessionErrorOptions {
  cause?: unknown
  code: string
  message: string
  status?: number
}

class SessionError extends Error {
  code: string
  status?: number

  constructor({ cause, code, message, status }: SessionErrorOptions) {
    super(message)
    this.name = 'SessionError'
    this.code = code

    if (cause) {
      this.cause = cause
    }

    if (status !== undefined) {
      this.status = status
    }
  }
}

interface SessionUser {
  id: number
  username: string
}

interface EncryptedToken {
  encoding: 'safeStorage'
  value: string
}

interface SavedAccount {
  activationCode: string
  baseUrl: string
  id: string
  user: SessionUser
}

interface StoredAccount {
  activationCode: EncryptedToken
  baseUrl: string
  user: SessionUser
}

interface StoredAccountsPayload {
  accounts: StoredAccount[]
  activeAccountId: null | string
  schemaVersion: number
}

interface TokenAuthResponse {
  access_token?: string
  expires_in: number
  user?: unknown
}

interface ActiveSession extends SavedAccount {
  sessionId: string
  token: string
  tokenExpiresAt: number
}

export interface AccountSummary {
  active: boolean
  baseUrl: string
  id: string
  username: string
}

export interface BackendSessionOptions {
  appVersion?: string
  defaultBaseUrl?: null | string
  fetchImpl: FetchFunction
  log?: (chunk: string) => void
  now?: () => number
  safeStorage?: null | SafeStorageApi
  userDataDir: string
}

export interface SessionSnapshot {
  accountId: string
  baseUrl: string
  hasToken: boolean
  sessionId: string
  tokenExpiresAt: number
  user: SessionUser
}

export interface BackendSession {
  activate: (payload?: { clientContext?: unknown; code?: string }) => Promise<null | SessionSnapshot>
  client: () => BackendClient
  getSession: () => null | SessionSnapshot
  getToken: () => null | string
  listAccounts: () => AccountSummary[]
  logout: (
    expectedSessionId?: string
  ) => Promise<{ backendUnreachable?: boolean; error?: string; ignored?: boolean; ok: boolean }>
  refresh: (payload?: { clientContext?: unknown }) => Promise<null | SessionSnapshot>
  removeAccount: (accountId: string) => Promise<void>
  restoreSession: () => Promise<null | SessionSnapshot>
  switchAccount: (accountId: string, payload?: { clientContext?: unknown }) => Promise<null | SessionSnapshot>
}

function encryptToken(raw: string, safeStorage?: null | SafeStorageApi): EncryptedToken {
  if (!safeStorage?.isEncryptionAvailable?.()) {
    throw new SessionError({
      code: 'safe-storage-unavailable',
      message: '安全存储不可用，无法保存激活码。请启用系统钥匙串后重试。'
    })
  }

  return { encoding: 'safeStorage', value: safeStorage.encryptString(raw).toString('base64') }
}

function decryptToken(blob: unknown, safeStorage?: null | SafeStorageApi): null | string {
  if (!blob || typeof blob !== 'object') {
    return null
  }

  const value = blob as { encoding?: unknown; value?: unknown }

  if (value.encoding !== 'safeStorage' || typeof value.value !== 'string' || !safeStorage?.isEncryptionAvailable?.()) {
    return null
  }

  try {
    return safeStorage.decryptString?.(Buffer.from(value.value, 'base64')) || null
  } catch {
    return null
  }
}

function normalizeUser(raw: unknown): null | SessionUser {
  if (!raw || typeof raw !== 'object') {
    return null
  }

  const user = raw as { id?: unknown; username?: unknown }

  if (!Number.isSafeInteger(user.id) || Number(user.id) <= 0 || typeof user.username !== 'string' || !user.username) {
    return null
  }

  return { id: Number(user.id), username: user.username }
}

function accountId(baseUrl: string, userId: number): string {
  return createHash('sha256').update(`${baseUrl}\0${userId}`).digest('hex')
}

function decodeActivationCode(code: string, fetchImpl: FetchFunction): string {
  const padding = '='.repeat((4 - (code.length % 4)) % 4)
  const raw = Buffer.from(code + padding, 'base64url').toString('utf8')
  const data = JSON.parse(raw) as { b?: unknown; t?: unknown }

  if (typeof data.b !== 'string' || !data.b || typeof data.t !== 'string' || !data.t) {
    throw new Error('activation code missing required fields')
  }

  return createBackendClient({ baseUrl: data.b, fetch: fetchImpl }).baseUrl
}

export function createBackendSession(options: BackendSessionOptions): BackendSession {
  const {
    appVersion = 'unknown',
    defaultBaseUrl = null,
    fetchImpl,
    now = () => Date.now(),
    safeStorage = null,
    userDataDir
  } = options

  if (!userDataDir || typeof fetchImpl !== 'function') {
    throw new SessionError({ code: 'invalid-session-options', message: 'userDataDir and fetchImpl are required' })
  }

  const sessionPath = path.join(userDataDir, SESSION_FILENAME)
  const log = options.log ?? (() => {})
  let loaded = false
  let accounts: SavedAccount[] = []
  let activeAccountId: null | string = null
  let cached: null | ActiveSession = null
  let backendClient: null | BackendClient = null
  let backendClientBaseUrl: null | string = null
  let refreshTimer: NodeJS.Timeout | null = null
  let sessionEpoch = 0
  let operations: Promise<unknown> = Promise.resolve()

  function enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const next = operations.then(operation, operation)
    operations = next.catch(() => {})

    return next
  }

  function loadAccounts(): void {
    if (loaded) {
      return
    }

    loaded = true
    const raw = safeReadJson(sessionPath)

    if (!raw || typeof raw !== 'object') {
      return
    }

    const record = raw as Partial<StoredAccountsPayload>

    if (record.schemaVersion === SESSION_SCHEMA_VERSION && Array.isArray(record.accounts)) {
      for (const item of record.accounts) {
        if (!item || typeof item.baseUrl !== 'string') {
          continue
        }

        const code = decryptToken(item.activationCode, safeStorage)
        const user = normalizeUser(item.user)

        if (!code || !user) {
          continue
        }

        try {
          const baseUrl = decodeActivationCode(code, fetchImpl)

          if (baseUrl !== createBackendClient({ baseUrl: item.baseUrl, fetch: fetchImpl }).baseUrl) {
            continue
          }

          const id = accountId(baseUrl, user.id)

          if (!accounts.some(account => account.id === id)) {
            accounts.push({ activationCode: code, baseUrl, id, user })
          }
        } catch {
          continue
        }
      }

      activeAccountId = typeof record.activeAccountId === 'string' ? record.activeAccountId : null

      return
    }
  }

  async function persist(nextAccounts: SavedAccount[], nextActiveAccountId: null | string): Promise<void> {
    const payload: StoredAccountsPayload = {
      accounts: nextAccounts.map(account => ({
        activationCode: encryptToken(account.activationCode, safeStorage),
        baseUrl: account.baseUrl,
        user: account.user
      })),
      activeAccountId: nextActiveAccountId,
      schemaVersion: SESSION_SCHEMA_VERSION
    }

    await atomicWriteFile(sessionPath, JSON.stringify(payload, null, 2))
  }

  function clearRefreshTimer(): void {
    if (refreshTimer) {
      clearTimeout(refreshTimer)
    }

    refreshTimer = null
  }

  function clearActive(): void {
    sessionEpoch++
    clearRefreshTimer()
    cached = null
    backendClient = null
    backendClientBaseUrl = null
  }

  function snapshot(): null | SessionSnapshot {
    if (!cached) {
      return null
    }

    return {
      accountId: cached.id,
      baseUrl: cached.baseUrl,
      hasToken: true,
      sessionId: cached.sessionId,
      tokenExpiresAt: cached.tokenExpiresAt,
      user: cached.user
    }
  }

  function listAccounts(): AccountSummary[] {
    loadAccounts()

    return accounts.map(account => ({
      active: Boolean(cached?.token && cached.id === account.id),
      baseUrl: account.baseUrl,
      id: account.id,
      username: account.user.username
    }))
  }

  function client(): BackendClient {
    const baseUrl = cached?.baseUrl || defaultBaseUrl

    if (!baseUrl) {
      throw new SessionError({ code: 'no-base-url', message: 'Backend base URL is not configured.' })
    }

    if (backendClient && backendClientBaseUrl === baseUrl) {
      return backendClient
    }

    backendClient = createBackendClient({ baseUrl, fetch: fetchImpl })
    backendClientBaseUrl = baseUrl

    return backendClient
  }

  function translateBackendError(error: unknown): never {
    if (!(error instanceof BackendRequestError)) {
      throw error
    }

    if (error.status === 401) {
      throw new SessionError({ cause: error, code: 'bad-credentials', message: '激活码无效。', status: 401 })
    }

    throw new SessionError({
      cause: error,
      code: error.code || 'backend-error',
      message: error.message,
      status: error.status ?? undefined
    })
  }

  function validateResponse(
    response: TokenAuthResponse,
    fallbackCode: string
  ): { expiresAt: number; token: string; user: SessionUser } {
    const user = normalizeUser(response?.user)

    if (!response || typeof response.access_token !== 'string' || !response.access_token || !user) {
      throw new SessionError({ code: fallbackCode, message: 'Backend did not return a valid session.' })
    }

    if (!Number.isFinite(response.expires_in) || response.expires_in <= 0) {
      throw new SessionError({ code: fallbackCode, message: 'Backend did not return a valid token expiry.' })
    }

    return { expiresAt: now() + response.expires_in * 1000, token: response.access_token, user }
  }

  function scheduleRefresh(): void {
    clearRefreshTimer()

    if (!cached) {
      return
    }

    const delay = cached.tokenExpiresAt - now() - REFRESH_LEAD_MS

    if (delay <= 0) {
      return
    }

    refreshTimer = setTimeout(() => {
      refreshTimer = null
      void refresh().catch(error => log(`[session] proactive refresh failed: ${errorMessage(error)}`))
    }, delay)
    refreshTimer.unref?.()
  }

  function retirePrevious(previous: null | ActiveSession, next: ActiveSession): void {
    if (!previous || previous.id === next.id) {
      return
    }

    const backend = createBackendClient({ baseUrl: previous.baseUrl, fetch: fetchImpl })
    void backend.post('/api/user/logout', { token: previous.token }).catch(error => {
      log(`[session] previous account logout failed: ${errorMessage(error)}`)
    })
  }

  async function activateCode(code: string, clientContext?: unknown): Promise<null | SessionSnapshot> {
    loadAccounts()

    if (cached?.activationCode === code && cached.tokenExpiresAt > now()) {
      return snapshot()
    }

    // 预检：安全存储不可用时先失败，避免网络激活成功后无法保存凭据。
    encryptToken(code, safeStorage)

    let baseUrl: string

    try {
      baseUrl = decodeActivationCode(code, fetchImpl)
    } catch {
      throw new SessionError({ code: 'invalid-code', message: '激活码格式无效。' })
    }

    const backend = createBackendClient({ baseUrl, fetch: fetchImpl })
    let response: TokenAuthResponse

    try {
      response = await backend.post<TokenAuthResponse>('/api/user/activate', {
        body: { client_context: clientContext || undefined, client_version: appVersion, code }
      })
    } catch (error) {
      return translateBackendError(error)
    }

    const verified = validateResponse(response, 'invalid-activate-response')
    const id = accountId(baseUrl, verified.user.id)
    const account: SavedAccount = { activationCode: code, baseUrl, id, user: verified.user }
    const nextAccounts = [...accounts.filter(item => item.id !== id), account]
    await persist(nextAccounts, id)

    const previous = cached
    accounts = nextAccounts
    activeAccountId = id
    cached = { ...account, sessionId: randomUUID(), token: verified.token, tokenExpiresAt: verified.expiresAt }
    sessionEpoch++
    backendClient = null
    backendClientBaseUrl = null
    scheduleRefresh()
    retirePrevious(previous, cached)
    log(`[session] activate ok base=${baseUrl} user=${verified.user.username}`)

    return snapshot()
  }

  function activate(payload: { clientContext?: unknown; code?: string } = {}): Promise<null | SessionSnapshot> {
    if (!payload.code) {
      return Promise.reject(new SessionError({ code: 'missing-code', message: 'Activation code is required.' }))
    }

    return enqueue(() => activateCode(payload.code!, payload.clientContext))
  }

  function switchAccount(id: string, payload: { clientContext?: unknown } = {}): Promise<null | SessionSnapshot> {
    return enqueue(async () => {
      loadAccounts()
      const account = accounts.find(item => item.id === id)

      if (!account) {
        throw new SessionError({ code: 'account-not-found', message: '该账户未保存在本机。' })
      }

      if (cached?.id === id && cached.tokenExpiresAt > now()) {
        return snapshot()
      }

      return activateCode(account.activationCode, payload.clientContext)
    })
  }

  async function refresh(payload: { clientContext?: unknown } = {}): Promise<null | SessionSnapshot> {
    if (!cached) {
      throw new SessionError({ code: 'not-logged-in', message: 'Cannot refresh without an active session.' })
    }

    const current = cached
    const epoch = sessionEpoch
    const backend = createBackendClient({ baseUrl: current.baseUrl, fetch: fetchImpl })

    try {
      const response = await backend.post<TokenAuthResponse>('/api/user/refresh', {
        body: { client_context: payload.clientContext || undefined, client_version: appVersion },
        token: current.token
      })

      if (epoch !== sessionEpoch || cached?.id !== current.id) {
        throw new SessionError({ code: 'session-superseded', message: 'Session changed during refresh.' })
      }

      const verified = validateResponse(response, 'invalid-refresh-response')

      if (verified.user.id !== current.user.id) {
        throw new SessionError({
          code: 'invalid-refresh-response',
          message: 'Backend returned another user during refresh.'
        })
      }

      cached = { ...current, token: verified.token, tokenExpiresAt: verified.expiresAt }
      sessionEpoch++
      scheduleRefresh()

      return snapshot()
    } catch (error) {
      if (epoch === sessionEpoch && error instanceof BackendRequestError && error.status === 401) {
        await enqueue(async () => {
          if (epoch === sessionEpoch) {
            clearActive()
          }
        })
      }

      return translateBackendError(error)
    }
  }

  function logout(
    expectedSessionId?: string
  ): Promise<{ backendUnreachable?: boolean; error?: string; ignored?: boolean; ok: boolean }> {
    return enqueue(async () => {
      if (expectedSessionId && cached?.sessionId !== expectedSessionId) {
        return { ignored: true, ok: true }
      }

      const previous = cached
      clearActive()

      if (!previous) {
        return { ok: true }
      }

      try {
        const backend = createBackendClient({ baseUrl: previous.baseUrl, fetch: fetchImpl })
        await backend.post('/api/user/logout', { token: previous.token })

        return { ok: true }
      } catch (error) {
        const message = errorMessage(error)
        log(`[session] logout backend call failed: ${message}`)

        return { backendUnreachable: true, error: message, ok: true }
      }
    })
  }

  function removeAccount(id: string): Promise<void> {
    return enqueue(async () => {
      loadAccounts()

      if (!accounts.some(item => item.id === id)) {
        return
      }

      const nextAccounts = accounts.filter(item => item.id !== id)
      const nextActive = activeAccountId === id ? null : activeAccountId
      await persist(nextAccounts, nextActive)
      accounts = nextAccounts
      activeAccountId = nextActive

      if (cached?.id === id) {
        const previous = cached
        clearActive()
        const backend = createBackendClient({ baseUrl: previous.baseUrl, fetch: fetchImpl })
        void backend.post('/api/user/logout', { token: previous.token }).catch(error => {
          log(`[session] removed account logout failed: ${errorMessage(error)}`)
        })
      }
    })
  }

  function restoreSession(): Promise<null | SessionSnapshot> {
    return enqueue(async () => {
      loadAccounts()
      const selected = accounts.find(item => item.id === activeAccountId)

      if (!selected) {
        return null
      }

      try {
        return await activateCode(selected.activationCode)
      } catch (error) {
        log(`[session] restore activation failed: ${errorMessage(error)}`)

        return null
      }
    })
  }

  function getToken(): null | string {
    return cached?.token ?? null
  }

  return {
    activate,
    client,
    getSession: snapshot,
    getToken,
    listAccounts,
    logout,
    refresh,
    removeAccount,
    restoreSession,
    switchAccount
  }
}
