/**
 * Runner/托盘/配置同步对后端的最小端口。
 * 只描述结构契约，不 import backend 实现，保持 shared 为叶子、runner 与 backend 互不直连。
 */

export interface BackendRequestPortOptions {
  body?: unknown
  headers?: Record<string, string>
  query?: Record<string, unknown>
  signal?: AbortSignal
  timeoutMs?: number
  token?: string
}

export interface BackendClientPort {
  baseUrl: string
  delete: <T = unknown>(path: string, options?: BackendRequestPortOptions) => Promise<T>
  get: <T = unknown>(path: string, options?: BackendRequestPortOptions) => Promise<T>
  patch: <T = unknown>(path: string, options?: BackendRequestPortOptions) => Promise<T>
  post: <T = unknown>(path: string, options?: BackendRequestPortOptions) => Promise<T>
  put: <T = unknown>(path: string, options?: BackendRequestPortOptions) => Promise<T>
  request: <T = unknown>(method: string, path: string, options?: BackendRequestPortOptions) => Promise<T>
}

export interface SessionSnapshotPort {
  accountId: string
  baseUrl: null | string
  hasToken: boolean
  sessionId: string
  tokenExpiresAt: null | number
  user: null | { id: null | number; username: null | string }
}

/** Runner 侧 reverse-rpc / bridge 需要的会话面。 */
export interface BackendSessionLike {
  client: () => BackendClientPort
  getSession: () => null | SessionSnapshotPort
  getToken: () => null | string
}

/** 会话运行时需要的完整会话面（activate/restore/logout/refresh）。 */
export interface BackendSessionPort extends BackendSessionLike {
  activate: (payload?: { clientContext?: unknown; code?: string }) => Promise<null | SessionSnapshotPort>
  listAccounts: () => Array<{ active: boolean; baseUrl: string; id: string; username: string }>
  logout: (expectedSessionId?: string) => Promise<{ ignored?: boolean; ok: boolean }>
  refresh: (payload?: { clientContext?: unknown }) => Promise<null | SessionSnapshotPort>
  removeAccount: (accountId: string) => Promise<void>
  restoreSession: () => Promise<null | SessionSnapshotPort>
  switchAccount: (accountId: string, payload?: { clientContext?: unknown }) => Promise<null | SessionSnapshotPort>
}

export interface BackendHttpPort {
  fetchJson: (
    url: string,
    token?: string,
    options?: { body?: unknown; method?: string; timeoutMs?: number }
  ) => Promise<unknown>
  mintWsTicket?: (baseUrl: string, token: null | string) => Promise<null | string>
  resolveRemoteBackend?: () => Promise<null | { baseUrl: string }>
  resolveSpiritAgentVersion: () => string
  waitForSpiritAgent?: (baseUrl: string, token?: string) => Promise<void>
}
