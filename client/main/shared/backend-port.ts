import type { ApiOperation, ApiTransport } from '../../../shared/api'

export type BackendClientContext = ApiOperation<'user.activate'>['body']['client_context']

export interface BackendConnection {
  baseUrl: string
  token: null | string
}

export interface BackendClientPort extends ApiTransport {
  baseUrl: string
}

export interface SessionSnapshotPort {
  accountId: string
  baseUrl: null | string
  hasToken: boolean
  sessionId: string
  tokenExpiresAt: null | number
  user: null | { id: null | number; username: null | string }
}

/** 会话运行时需要的完整会话面（activate/restore/logout/refresh）。 */
export interface BackendSessionPort {
  getSession: () => null | SessionSnapshotPort
  getToken: () => null | string
  activate: (payload?: { clientContext?: BackendClientContext; code?: string }) => Promise<null | SessionSnapshotPort>
  /** Token 失效后仍返回本机选中的账户。 */
  getSelectedAccountId: () => null | string
  listAccounts: () => Array<{ active: boolean; baseUrl: string; id: string; username: string }>
  logout: (expectedSessionId?: string) => Promise<{ ignored?: boolean; ok: boolean }>
  refresh: (payload?: { clientContext?: BackendClientContext }) => Promise<null | SessionSnapshotPort>
  removeAccount: (accountId: string) => Promise<void>
  restoreSession: () => Promise<null | SessionSnapshotPort>
  switchAccount: (
    accountId: string,
    payload?: { clientContext?: BackendClientContext }
  ) => Promise<null | SessionSnapshotPort>
}
