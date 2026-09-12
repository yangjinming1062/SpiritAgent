import fs from 'node:fs'
import path from 'node:path'

import type { SafeStorageApi } from '../security/hardening'
import { atomicWriteFile, errorMessage, safeReadJson } from '../shared/utils'

import { type BackendClient, BackendRequestError, createBackendClient, type FetchFunction } from './client'

const SESSION_FILENAME = 'agent-session.json'
const SESSION_SCHEMA_VERSION = 2
const KNOWN_TOKEN_TTL_MS = 8 * 60 * 60 * 1000
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
  id: null | number
  username: null | string
}

interface EncryptedToken {
  encoding: 'safeStorage'
  value: string
}

interface StoredSessionPayload {
  activationCode: EncryptedToken | null
  baseUrl: null | string
  schemaVersion: number
  user: null | SessionUser
}

interface TokenAuthResponse {
  access_token?: string
  expires_in?: number
  user?: unknown
}

async function atomicWriteJson(targetPath: string, payload: unknown): Promise<void> {
  await atomicWriteFile(targetPath, JSON.stringify(payload, null, 2))
}

function encryptToken(raw: null | string | undefined, safeStorage?: null | SafeStorageApi): EncryptedToken | null {
  const value = String(raw || '')

  if (!value) {
    return null
  }

  if (!safeStorage?.isEncryptionAvailable?.()) {
    throw new SessionError({
      code: 'safe-storage-unavailable',
      message:
        'Secure token storage is unavailable, so the desktop client cannot save the Backend token. ' +
        'Enable OS keychain access and try again.'
    })
  }

  return {
    encoding: 'safeStorage',
    value: safeStorage.encryptString(value).toString('base64')
  }
}

function decryptToken(blob: unknown, safeStorage?: null | SafeStorageApi): null | string {
  if (!blob || typeof blob !== 'object') {
    return null
  }

  const blobRecord = blob as { encoding?: string; value?: unknown }

  if (blobRecord.encoding !== 'safeStorage') {
    return null
  }

  if (!safeStorage?.isEncryptionAvailable?.()) {
    return null
  }

  try {
    const buf = Buffer.from(String(blobRecord.value || ''), 'base64')

    return safeStorage.decryptString ? safeStorage.decryptString(buf) : null
  } catch {
    return null
  }
}

function normalizeUser(raw: unknown): null | SessionUser {
  if (!raw || typeof raw !== 'object') {
    return null
  }

  const record = raw as Record<string, unknown>
  const id = record.id ?? record.user_id ?? null
  const username = record.username ?? null

  if (id === null && username === null) {
    return null
  }

  return {
    id: id === null ? null : Number(id),
    username: username === null ? null : String(username)
  }
}

export function decodeActivationCode(code: string): { baseUrl: string; token: string } {
  const padding = '='.repeat((4 - (code.length % 4)) % 4)
  const raw = Buffer.from(code + padding, 'base64url').toString('utf8')
  const data = JSON.parse(raw) as { b?: string; t?: string }
  const baseUrl = data.b
  const token = data.t

  if (!baseUrl || !token) {
    throw new Error('activation code missing required fields')
  }

  return { baseUrl, token }
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
  baseUrl: null | string
  hasToken: boolean
  tokenExpiresAt: null | number
  user: null | SessionUser
}

export interface BackendSession {
  activate: (payload?: { clientContext?: unknown; code?: string }) => Promise<null | SessionSnapshot>
  authHeaders: () => Record<string, string>
  clearSession: () => Promise<void>
  client: () => BackendClient
  getSession: () => null | SessionSnapshot
  getToken: () => null | string
  logout: () => Promise<{ backendUnreachable?: boolean; error?: string; ok: boolean }>
  refresh: (payload?: { clientContext?: unknown }) => Promise<null | SessionSnapshot>
  restoreSession: () => Promise<null | SessionSnapshot>
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

  if (!userDataDir) {
    throw new SessionError({ code: 'missing-user-data-dir', message: 'userDataDir is required' })
  }

  if (typeof fetchImpl !== 'function') {
    throw new SessionError({ code: 'missing-fetch', message: 'fetch implementation is required' })
  }

  const sessionPath = path.join(userDataDir, SESSION_FILENAME)

  const log = typeof options.log === 'function' ? options.log : () => {}

  let cached: null | {
    activationCode: string
    baseUrl: null | string
    token: null | string
    tokenExpiresAt: null | number
    user: null | SessionUser
  } = null

  let backendClient: null | BackendClient = null
  let backendClientBaseUrl: null | string = null
  let activatePromise: null | Promise<null | SessionSnapshot> = null
  let refreshTimer: NodeJS.Timeout | null = null
  // activate/clearSession 时递增：在途 refresh 完成前若代数已变则丢弃。
  let sessionEpoch = 0

  async function persistCurrent(): Promise<void> {
    if (!cached) {
      return
    }

    try {
      await atomicWriteJson(sessionPath, {
        activationCode: encryptToken(cached.activationCode, safeStorage),
        baseUrl: cached.baseUrl,
        schemaVersion: SESSION_SCHEMA_VERSION,
        user: cached.user
      })
    } catch (err) {
      log(`[session] persistCurrent failed: ${errorMessage(err)}`)
    }
  }

  function clearRefreshTimer(): void {
    if (refreshTimer !== null) {
      clearTimeout(refreshTimer)
      refreshTimer = null
    }
  }

  function scheduleRefresh(): void {
    clearRefreshTimer()

    if (!cached?.tokenExpiresAt) {
      return
    }

    const delay = cached.tokenExpiresAt - now() - REFRESH_LEAD_MS

    if (delay <= 0) {
      return
    }

    refreshTimer = setTimeout(() => {
      refreshTimer = null

      if (!cached?.token) {
        return
      }

      log('[session] proactive token refresh triggered')
      refresh().catch((err: unknown) => {
        const msg = errorMessage(err)
        log(`[session] proactive refresh failed: ${msg}`)
      })
    }, delay)

    if (typeof refreshTimer.unref === 'function') {
      refreshTimer.unref()
    }
  }

  function loadFromDisk(): null | {
    activationCode: string
    baseUrl: null | string
    user: null | SessionUser
  } {
    const raw = safeReadJson(sessionPath)

    if (!raw || typeof raw !== 'object') {
      return null
    }

    const record = raw as Partial<StoredSessionPayload>

    if (record.schemaVersion !== SESSION_SCHEMA_VERSION) {
      return null
    }

    const activationCode = decryptToken(record.activationCode, safeStorage)

    if (!activationCode) {
      return null
    }

    return {
      activationCode,
      baseUrl: typeof record.baseUrl === 'string' ? record.baseUrl : null,
      user: normalizeUser(record.user)
    }
  }

  function snapshot(): null | SessionSnapshot {
    if (!cached) {
      return null
    }

    const { baseUrl, token, tokenExpiresAt, user } = cached

    return { baseUrl, hasToken: Boolean(token), tokenExpiresAt, user }
  }

  function effectiveBaseUrl(): null | string {
    if (cached?.baseUrl) {
      return cached.baseUrl
    }

    if (defaultBaseUrl) {
      return defaultBaseUrl
    }

    return null
  }

  function client(): BackendClient {
    const baseUrl = effectiveBaseUrl()

    if (!baseUrl) {
      throw new SessionError({
        code: 'no-base-url',
        message: 'Backend base URL is not configured. Activate with a valid activation code.'
      })
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
      if (error instanceof Error) {
        throw error
      }

      throw new SessionError({ code: 'unknown-error', message: String(error) })
    }

    if (error.status === 401) {
      throw new SessionError({
        cause: error,
        code: 'bad-credentials',
        message: '激活码无效。',
        status: 401
      })
    }

    throw new SessionError({
      cause: error,
      code: error.code || 'backend-error',
      message: error.message,
      status: error.status ?? undefined
    })
  }

  async function applySession({
    activationCode,
    baseUrl,
    source,
    token,
    tokenExpiresAt,
    user
  }: {
    activationCode?: null | string
    baseUrl?: null | string
    source: string
    token: string
    tokenExpiresAt: null | number
    user?: unknown
  }): Promise<null | SessionSnapshot> {
    if (!token) {
      throw new SessionError({
        code: 'no-token',
        message: 'Cannot apply a session without a session token.'
      })
    }

    const resolvedBaseUrl = baseUrl || cached?.baseUrl || null
    const resolvedUser = normalizeUser(user) || cached?.user || { id: null, username: null }
    const resolvedCode = activationCode || cached?.activationCode || null

    if (!resolvedCode) {
      throw new SessionError({
        code: 'no-activation-code',
        message: 'Cannot apply a session without an activation code.'
      })
    }

    cached = {
      activationCode: resolvedCode,
      baseUrl: resolvedBaseUrl,
      token,
      tokenExpiresAt,
      user: resolvedUser
    }

    sessionEpoch++
    backendClient = null
    backendClientBaseUrl = null

    await persistCurrent()
    scheduleRefresh()

    log(`[session] ${source} ok base=${resolvedBaseUrl} user=${resolvedUser?.username ?? '?'}`)

    return snapshot()
  }

  // 验签 token 响应并写入会话；activate 和 refresh 共用此逻辑，
  // 仅错误码与是否携带 baseUrl/activationCode 不同。
  async function applyTokenResponse(
    response: TokenAuthResponse,
    source: 'activate' | 'refresh',
    fallbackCode: string,
    overrides: { activationCode?: string; baseUrl?: string } = {}
  ): Promise<null | SessionSnapshot> {
    if (!response || typeof response.access_token !== 'string' || !response.access_token) {
      throw new SessionError({
        code: fallbackCode,
        message: 'Backend did not return an access token.'
      })
    }

    const expiresIn =
      typeof response.expires_in === 'number' && Number.isFinite(response.expires_in) && response.expires_in > 0
        ? response.expires_in * 1000
        : KNOWN_TOKEN_TTL_MS

    return applySession({
      activationCode: overrides.activationCode,
      baseUrl: overrides.baseUrl,
      source,
      token: response.access_token,
      tokenExpiresAt: now() + expiresIn,
      user: response.user
    })
  }

  async function activate(payload: { clientContext?: unknown; code?: string } = {}): Promise<null | SessionSnapshot> {
    const { clientContext, code } = payload

    if (!code) {
      throw new SessionError({
        code: 'missing-code',
        message: 'Activation code is required.'
      })
    }

    let baseUrl: string

    try {
      const decoded = decodeActivationCode(code)
      baseUrl = decoded.baseUrl
    } catch {
      throw new SessionError({
        code: 'invalid-code',
        message: '激活码格式无效。'
      })
    }

    if (!baseUrl) {
      throw new SessionError({
        code: 'no-base-url',
        message: 'Activation code does not contain a backend address.'
      })
    }

    if (activatePromise) {
      return activatePromise
    }

    const backend = createBackendClient({ baseUrl, fetch: fetchImpl })
    activatePromise = backend
      .post<TokenAuthResponse>('/api/user/activate', {
        body: {
          client_context: clientContext || undefined,
          client_version: appVersion,
          code
        }
      })
      .then(response =>
        applyTokenResponse(response, 'activate', 'invalid-activate-response', { activationCode: code, baseUrl })
      )
      .catch(translateBackendError)
      .finally(() => {
        activatePromise = null
      })

    return activatePromise
  }

  async function refresh(payload: { clientContext?: unknown } = {}): Promise<null | SessionSnapshot> {
    if (!cached || !cached.token) {
      return Promise.reject(
        new SessionError({
          code: 'not-logged-in',
          message: 'Cannot refresh without an active session.'
        })
      )
    }

    const { clientContext } = payload
    const backend = client()
    const epoch = sessionEpoch

    return backend
      .post<TokenAuthResponse>('/api/user/refresh', {
        body: {
          client_context: clientContext || undefined,
          client_version: appVersion
        },
        token: cached.token
      })
      .then(response => {
        if (epoch !== sessionEpoch) {
          throw new SessionError({
            code: 'session-superseded',
            message: 'Session changed during refresh; dropping stale token response.'
          })
        }

        return applyTokenResponse(response, 'refresh', 'invalid-refresh-response')
      })
      .catch(async (error: unknown) => {
        // 鉴权失效：与 restore 对齐清理，避免僵尸 hasToken；身份已切换则只透传错误。
        if (epoch === sessionEpoch && error instanceof BackendRequestError && error.status === 401) {
          await clearSession()
        }

        return translateBackendError(error)
      })
  }

  async function logout(): Promise<{ backendUnreachable?: boolean; error?: string; ok: boolean }> {
    if (!cached?.token) {
      await clearSession()

      return { ok: true }
    }

    const backend = client()

    try {
      await backend.post('/api/user/logout', { token: cached.token })
      await clearSession()
      log('[session] logout ok')

      return { ok: true }
    } catch (error) {
      const msg = errorMessage(error)
      log(`[session] logout backend call failed: ${msg}`)
      await clearSession()

      return { backendUnreachable: true, error: msg, ok: true }
    }
  }

  async function clearSession(): Promise<void> {
    sessionEpoch++
    clearRefreshTimer()
    cached = null
    backendClient = null
    backendClientBaseUrl = null

    try {
      await fs.promises.unlink(sessionPath)
    } catch (error: unknown) {
      const err = error as { code?: string; message?: string }

      if (err?.code !== 'ENOENT') {
        log(`[session] clearSession unlink failed: ${err?.message || String(error)}`)
      }
    }
  }

  async function restoreSession(): Promise<null | SessionSnapshot> {
    const loaded = loadFromDisk()

    if (!loaded) {
      return null
    }

    cached = {
      activationCode: loaded.activationCode,
      baseUrl: loaded.baseUrl,
      token: null,
      tokenExpiresAt: null,
      user: loaded.user
    }

    try {
      return await activate({ code: loaded.activationCode })
    } catch (err: unknown) {
      if (err instanceof SessionError && err.code === 'bad-credentials') {
        await clearSession()
      }

      const msg = errorMessage(err)
      log(`[session] restore activation failed: ${msg}`)

      return null
    }
  }

  function getSession(): null | SessionSnapshot {
    return snapshot()
  }

  function getToken(): null | string {
    return cached?.token ?? null
  }

  function authHeaders(): Record<string, string> {
    if (!cached?.token) {
      return {}
    }

    return { Authorization: `Bearer ${cached.token}` }
  }

  return {
    activate,
    authHeaders,
    clearSession,
    client,
    getSession,
    getToken,
    logout,
    refresh,
    restoreSession
  }
}
