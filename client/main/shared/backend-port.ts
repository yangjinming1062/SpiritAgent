import type { ApiOperation, ApiTransport } from '../../../shared/api'

export type BackendClientContext = ApiOperation<'user.activate'>['body']['client_context']

export interface BackendConnection {
  baseUrl: string
  token: null | string
}

export interface BackendClientPort extends ApiTransport {
  baseUrl: string
}

/** 会话快照：字段与实现产出的形状一致，实现侧不会产出 null。 */
export interface SessionSnapshotPort {
  accountId: string
  baseUrl: string
  hasToken: boolean
  sessionId: string
  tokenExpiresAt: number
  user: { id: number; username: string }
}

/** 会话运行时需要的完整会话面（activate/restore/logout/refresh）。 */
export interface BackendSessionPort {
  getSession: () => null | SessionSnapshotPort
  getToken: () => null | string
  activate: (payload?: { clientContext?: BackendClientContext; code?: string }) => Promise<null | SessionSnapshotPort>
  /** Token 失效后仍返回本机选中的账户。 */
  getSelectedAccountId: () => null | string
  listAccounts: () => Array<{ active: boolean; baseUrl: string; id: string; username: string }>
  logout: (
    expectedSessionId?: string
  ) => Promise<{ backendUnreachable?: boolean; error?: string; ignored?: boolean; ok: boolean }>
  refresh: (payload?: { clientContext?: BackendClientContext }) => Promise<null | SessionSnapshotPort>
  removeAccount: (accountId: string) => Promise<void>
  restoreSession: () => Promise<null | SessionSnapshotPort>
  switchAccount: (
    accountId: string,
    payload?: { clientContext?: BackendClientContext }
  ) => Promise<null | SessionSnapshotPort>
}
