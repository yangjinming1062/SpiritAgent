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
  baseUrl: null | string
  hasToken: boolean
  tokenExpiresAt: null | number
  user: null | { id: null | number; username: null | string }
}

/** Runner 侧 reverse-rpc / bridge 需要的会话面。 */
export interface BackendSessionLike {
  client: () => BackendClientPort
  getSession: () => null | SessionSnapshotPort
  getToken: () => null | string
}

/** bridge-deps 组装需要的完整会话面（activate/restore/logout/refresh）。 */
export interface BackendSessionPort extends BackendSessionLike {
  activate: (payload?: { clientContext?: unknown; code?: string }) => Promise<null | SessionSnapshotPort>
  authHeaders?: () => Record<string, string>
  clearSession?: () => Promise<void>
  logout: () => Promise<unknown>
  refresh: (payload?: { clientContext?: unknown }) => Promise<null | SessionSnapshotPort>
  restoreSession: () => Promise<null | SessionSnapshotPort>
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
